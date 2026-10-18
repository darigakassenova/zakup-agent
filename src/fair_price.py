import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from dotenv import load_dotenv

from ingest import DEFAULT_DSN

ROOT = Path(__file__).resolve().parent.parent
CPI_FILE = ROOT / "data" / "reference" / "cpi_kz.csv"

WINDOW_MONTHS = 12
MIN_PEERS = 5
MIN_CITY_ROWS = 30
SHRINK_CITY = 50
SHRINK_MONTH = 100
DEVIATION_LIMIT = 0.30
FENCE = 1.5
ERROR_RATIO = 10
IQR_TO_SIGMA = 1.349

FACTS_SQL = """
SELECT unit_id, contract_id, customer_bin, supplier_bin, enstru_code, base_unit, sign_date,
       city_code, price_per_base::float8 AS price, qty_base::float8 AS qty
FROM mart.price_fact
WHERE is_benchmark
"""

DDL = """
DROP TABLE IF EXISTS mart.fair_price;
CREATE TABLE mart.fair_price (
    unit_id        bigint PRIMARY KEY,
    contract_id    bigint,
    customer_bin   text,
    supplier_bin   text,
    enstru_code    text,
    base_unit      text,
    sign_date      date,
    city_code      text,
    price_per_base numeric,
    qty_base       numeric,
    fair_price     numeric,
    q1             numeric,
    q3             numeric,
    n_peers        int,
    peer_level     text,
    rel_dispersion numeric,
    deviation      numeric,
    excess_amount  numeric,
    flag           text,
    confidence     text
);
CREATE INDEX ON mart.fair_price (enstru_code, base_unit);
CREATE INDEX ON mart.fair_price (customer_bin, sign_date);
CREATE INDEX ON mart.fair_price (flag, confidence);
"""

COLUMNS = ["unit_id", "contract_id", "customer_bin", "supplier_bin", "enstru_code", "base_unit",
           "sign_date", "city_code", "price", "qty", "fair_price", "q1", "q3", "n_peers",
           "peer_level", "rel_dispersion", "deviation", "excess_amount", "flag", "confidence"]


def load_facts(conn):
    cur = conn.execute(FACTS_SQL)
    facts = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    dates = pd.to_datetime(facts["sign_date"])
    facts["month_idx"] = dates.dt.year * 12 + dates.dt.month - 1
    facts["month"] = dates.dt.month
    facts["oblast"] = facts["city_code"].str[:2]
    return facts


def price_index(month_idx):
    if not CPI_FILE.exists():
        return np.ones(len(month_idx)), False
    cpi = pd.read_csv(CPI_FILE)
    months = cpi["month"].str.slice(0, 4).astype(int) * 12 + cpi["month"].str.slice(5, 7).astype(int) - 1
    level = (1 + cpi["mom_pct"] / 100).cumprod()
    order = np.argsort(months.values)
    return np.interp(month_idx, months.values[order], level.values[order]), True


def log_ratio_to_group_median(facts):
    keys = ["enstru_code", "base_unit"]
    median = facts.groupby(keys)["real_price"].transform("median")
    size = facts.groupby(keys)["real_price"].transform("size")
    return np.log(facts["real_price"] / median).where(size >= MIN_PEERS)


def shrunk_effects(log_ratio, labels, shrink, min_rows=1):
    stats = log_ratio.groupby(labels).agg(["median", "count"])
    stats = stats[stats["count"] >= min_rows]
    return stats["median"] * stats["count"] / (stats["count"] + shrink)


def add_adjustments(facts):
    facts["cpi"], used_cpi = price_index(facts["month_idx"])
    facts["real_price"] = facts["price"] / facts["cpi"]

    log_ratio = log_ratio_to_group_median(facts)
    season = shrunk_effects(log_ratio, facts["month"], SHRINK_MONTH)
    season -= season.mean()
    facts["season"] = np.exp(facts["month"].map(season).fillna(0.0))

    log_ratio = log_ratio - np.log(facts["season"])
    region = shrunk_effects(log_ratio, facts["city_code"], SHRINK_CITY, MIN_CITY_ROWS)
    facts["region_k"] = np.exp(facts["city_code"].map(region).fillna(0.0))
    return used_cpi, season, region


def peer_quantiles(values):
    q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    return q1, median, q3


def evaluate_group(group):
    n = len(group)
    result = np.full((n, 5), np.nan)
    levels = np.full(n, "", dtype=object)

    month = group["month_idx"].to_numpy()
    customer = pd.factorize(group["customer_bin"])[0]
    city = pd.factorize(group["city_code"])[0]
    oblast = pd.factorize(group["oblast"])[0]
    local = (group["real_price"] / group["season"]).to_numpy()
    regional = local / group["region_k"].to_numpy()
    target = (group["cpi"] * group["season"]).to_numpy()
    target_regional = target * group["region_k"].to_numpy()

    for i in range(n):
        candidates = (np.abs(month - month[i]) <= WINDOW_MONTHS) & (customer != customer[i])
        steps = (
            ("city", candidates & (city == city[i]) & (city[i] >= 0), local, target[i]),
            ("oblast", candidates & (oblast == oblast[i]) & (oblast[i] >= 0), regional, target_regional[i]),
            ("country", candidates, regional, target_regional[i]),
        )
        for level, mask, values, factor in steps:
            if mask.sum() >= MIN_PEERS:
                q1, median, q3 = peer_quantiles(values[mask])
                result[i] = [median * factor, q1 * factor, q3 * factor, mask.sum(),
                             (q3 - q1) / IQR_TO_SIGMA / median if median > 0 else np.nan]
                levels[i] = level
                break
    return result, levels


def classify(price, fair, q1, q3):
    if np.isnan(fair) or fair <= 0:
        return "insufficient"
    ratio = price / fair
    spread = q3 - q1
    if ratio >= ERROR_RATIO or ratio <= 1 / ERROR_RATIO:
        return "suspect_error"
    if ratio - 1 > DEVIATION_LIMIT and price > q3 + FENCE * spread:
        return "high"
    if ratio - 1 < -DEVIATION_LIMIT and price < q1 - FENCE * spread:
        return "low"
    return "normal"


def confidence(n_peers, dispersion, level):
    if np.isnan(n_peers) or np.isnan(dispersion):
        return "none"
    if n_peers >= 20 and dispersion <= 0.35:
        score = 2
    elif n_peers >= 8 and dispersion <= 0.8:
        score = 1
    else:
        score = 0
    if level == "country":
        score -= 1
    return ("low", "medium", "high")[max(score, 0)]


def score_all(facts):
    parts = []
    for _, group in facts.groupby(["enstru_code", "base_unit"], sort=False):
        result, levels = evaluate_group(group)
        scored = group[["unit_id"]].copy()
        scored[["fair_price", "q1", "q3", "n_peers", "rel_dispersion"]] = result
        scored["peer_level"] = levels
        parts.append(scored)
    scored = pd.concat(parts)

    out = facts.merge(scored, on="unit_id")
    out["deviation"] = out["price"] / out["fair_price"] - 1
    out["excess_amount"] = (out["price"] - out["fair_price"]) * out["qty"]
    out["flag"] = [classify(*row) for row in zip(out["price"], out["fair_price"], out["q1"], out["q3"])]
    out["confidence"] = [confidence(*row) for row in zip(out["n_peers"], out["rel_dispersion"], out["peer_level"])]
    return out[COLUMNS]


def save(conn, scored):
    conn.execute(DDL)
    rows = scored.astype(object).where(scored.notna(), None).values.tolist()
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO mart.fair_price VALUES ({', '.join(['%s'] * len(COLUMNS))})", rows)
    conn.commit()


def report(scored, used_cpi, season, region):
    print(f"Инфляция учтена: {'да' if used_cpi else 'нет (нет файла data/reference/cpi_kz.csv)'}")
    print(f"Поправка на сезон: от {np.exp(season.min()):.2f} до {np.exp(season.max()):.2f}")
    if len(region):
        print(f"Региональных коэффициентов: {len(region)}, от {np.exp(region.min()):.2f} до {np.exp(region.max()):.2f}")
    print(f"\nПозиций оценено: {len(scored)}")
    print("\nПо вердиктам и достоверности:")
    print(pd.crosstab(scored["flag"], scored["confidence"]).to_string())
    print("\nУровень сравнения:")
    print(scored["peer_level"].replace("", "нет").value_counts().to_string())


def main():
    load_dotenv(ROOT / ".env")
    with psycopg.connect(os.getenv("DATABASE_URL", DEFAULT_DSN)) as conn:
        facts = load_facts(conn)
        used_cpi, season, region = add_adjustments(facts)
        scored = score_all(facts)
        save(conn, scored)
    report(scored, used_cpi, season, region)


if __name__ == "__main__":
    main()