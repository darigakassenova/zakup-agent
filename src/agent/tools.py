import re

from agent.settings import (ANNOUNCE_LINK, CONTRACT_LINK, DEVIATION_LIMIT, MAX_TOP_K, MIN_GROUP_SIZE,
                            PEER_EXAMPLES, TOP_K, VOLUME_MIN_RATIO)

STOP_WORDS = {"для", "или", "что", "как", "это", "все", "над", "при", "про", "его", "ее", "ақ", "және", "үшін"}
VALID_STATUS = ("active", "transferred")
DATA_YEARS = (2024, 2025, 2026)

# Города республиканского значения
CITY_NAMES = {"710000000": "г. Астана", "750000000": "г. Алматы", "790000000": "г. Шымкент"}
SEGMENTS = {
    "customer": ("pf.customer_bin", "customer"),
    "supplier": ("pf.supplier_bin", "supplier"),
    "city": ("pf.city_code", "city"),
    "year": ("pf.year::text", "year"),
}


def contract_link(contract_id):
    return CONTRACT_LINK.format(id=contract_id)


def announce_link(trd_buy_id):
    return ANNOUNCE_LINK.format(id=trd_buy_id) if trd_buy_id else None


def as_float(value):
    """Число из базы (Decimal) в float; пустое значение остаётся None."""
    return float(value) if value is not None else None


def clamp_k(top_k):
    return max(1, min(int(top_k or TOP_K), MAX_TOP_K))


def name_sql(ctx, alias):
    if ctx.lang == "kk":
        return f"coalesce(nullif({alias}.name_kz, ''), {alias}.name_ru)"
    return f"coalesce({alias}.name_ru, {alias}.name_kz)"


def tokens(query):
    found = []
    for word in re.findall(r"[\w-]+", (query or "").lower()):
        if len(word) >= 3 and word not in STOP_WORDS and not word.isdigit():
            found.append(word[: max(4, len(word) - 2)])
    return found


def text_conditions(query, column, params):
    conditions = []
    for i, token in enumerate(tokens(query)):
        params[f"t{i}"] = f"%{token}%"
        conditions.append(f"{column} ILIKE %(t{i})s")
    return conditions


def known_words(ctx, query):
    kept = []
    for word in re.findall(r"[\w-]+", (query or "").lower()):
        stems = tokens(word)
        if stems and ctx.one("""SELECT 1 AS x FROM mart.price_fact pf
                                WHERE (coalesce(pf.item_name, '') || ' ' || coalesce(pf.enstru_name, '')) ILIKE %(p)s LIMIT 1""",
                             {"p": f"%{stems[0]}%"}):
            kept.append(word)
    return " ".join(kept) or None


def date_conditions(filters, params, column):
    conditions = []
    if filters.get("year_from"):
        params["date_from"] = f"{int(filters['year_from'])}-01-01"
        conditions.append(f"{column} >= %(date_from)s")
    if filters.get("year_to"):
        params["date_to"] = f"{int(filters['year_to'])}-12-31"
        conditions.append(f"{column} <= %(date_to)s")
    return conditions


def fact_conditions(filters, params, alias="pf"):
    conditions = date_conditions(filters, params, f"{alias}.sign_date")
    bins = filters.get("customer_bins") or ([filters["customer_bin"]] if filters.get("customer_bin") else [])
    if bins:
        params["customer_bins"] = list(bins)
        conditions.append(f"{alias}.customer_bin = ANY(%(customer_bins)s)")
    if filters.get("supplier_bin"):
        params["supplier_bin"] = filters["supplier_bin"]
        conditions.append(f"{alias}.supplier_bin = %(supplier_bin)s")
    if filters.get("enstru_code"):
        params["enstru_code"] = filters["enstru_code"]
        conditions.append(f"{alias}.enstru_code = %(enstru_code)s")
    conditions += text_conditions(
        filters.get("query"), f"(coalesce({alias}.item_name, '') || ' ' || coalesce({alias}.enstru_name, ''))", params)
    return conditions


def where(conditions):
    return " AND ".join(conditions) if conditions else "TRUE"


def period_of(ctx, filters):
    row = ctx.one("SELECT min(sign_date) AS first, max(sign_date) AS last FROM mart.price_fact")
    return {"from": filters.get("year_from") and f"{int(filters['year_from'])}-01-01" or str(row["first"]),
            "to": filters.get("year_to") and f"{int(filters['year_to'])}-12-31" or str(row["last"])}


def filters_shown(filters):
    return {k: v for k, v in filters.items() if v not in (None, "", []) and not (k == "direction" and v == "both")}


def data_as_of(ctx):
    row = ctx.one("SELECT max(updated_at)::date AS day FROM etl_state")
    return str(row["day"]) if row and row["day"] else None


def outcome(tool, filters, ctx, period=None, **fields):
    result = {"tool": tool, "filters": filters_shown(filters), "period": period or period_of(ctx, filters),
              "language": ctx.lang, "data_as_of": data_as_of(ctx), "limitations": [], **fields}
    given = [b for b in (filters.get("customer_bins") or []) + [filters.get("customer_bin")] if b]
    outside = [b for b in given if not ctx.one("SELECT 1 AS x FROM subject WHERE bin = %(b)s AND is_target", {"b": b})]
    if outside:
        result["limitations"].append("bin_not_target")
        result["outside_bins"] = outside
    return result


def level_by_size(n):
    return "high" if n >= 20 else "medium" if n >= 8 else "low"


def subject_name(ctx, bin_value):
    if not bin_value:
        return None
    row = ctx.one(f"SELECT {name_sql(ctx, 's')} AS name FROM subject s WHERE s.bin = %(b)s", {"b": bin_value})
    return row["name"] if row and row["name"] else None


def status_names(ctx):
    rows = ctx.rows(f"SELECT id, {name_sql(ctx, 'r')} AS name FROM ref_status r WHERE kind = 'contract_status'")
    return {r["id"]: r["name"] for r in rows}


def search_contracts(ctx, query=None, customer_bin=None, supplier_bin=None, enstru_code=None,
                     year_from=None, year_to=None, top_k=TOP_K):
    filters = dict(query=query, customer_bin=customer_bin, supplier_bin=supplier_bin, enstru_code=enstru_code,
                   year_from=year_from, year_to=year_to)
    params = {}
    cond = where(fact_conditions(filters, params))
    total = ctx.one(f"SELECT count(DISTINCT pf.contract_id) AS n FROM mart.price_fact pf WHERE {cond}", params)["n"]
    params["k"] = clamp_k(top_k)
    rows = ctx.rows(f"""
        WITH hit AS (
            SELECT pf.contract_id, min(pf.sign_date) AS sign_date, min(pf.customer_bin) AS customer_bin,
                   min(pf.supplier_bin) AS supplier_bin, count(*) AS positions,
                   (array_agg(pf.item_name ORDER BY pf.total_sum DESC NULLS LAST))[1] AS item_name
            FROM mart.price_fact pf WHERE {cond} GROUP BY pf.contract_id
        )
        SELECT h.*, cc.contract_number, cc.trd_buy_id, cc.sum AS contract_sum, cc.status_id,
               {name_sql(ctx, 'cs')} AS customer, {name_sql(ctx, 'ss')} AS supplier
        FROM hit h
        JOIN mart.contract_current cc ON cc.id = h.contract_id
        LEFT JOIN subject cs ON cs.bin = h.customer_bin
        LEFT JOIN subject ss ON ss.bin = h.supplier_bin
        ORDER BY h.sign_date DESC, h.contract_id DESC LIMIT %(k)s""", params)
    statuses = status_names(ctx)
    top = [{"contract_id": r["contract_id"], "number": r["contract_number"], "date": str(r["sign_date"]),
            "customer": r["customer"] or r["customer_bin"], "supplier": r["supplier"] or r["supplier_bin"],
            "amount": float(r["contract_sum"] or 0), "status": statuses.get(r["status_id"]),
            "item": r["item_name"], "positions": r["positions"],
            "link": contract_link(r["contract_id"]), "announce_link": announce_link(r["trd_buy_id"])}
           for r in rows]
    result = outcome("search_contracts", filters, ctx, n_objects=total, method="sql_filter", confidence="high",
                     facts={"found": total, "shown": len(top)}, top=top)
    if query and tokens(query):
        result["limitations"].append("text_match")
    result["limitations"].append("terminated_included")
    return result


def spend_summary(ctx, group_by="customer", query=None, customer_bin=None, supplier_bin=None, enstru_code=None,
                  year_from=None, year_to=None, top_k=TOP_K, include_terminated=False):
    group_by = group_by if group_by in ("customer", "supplier", "enstru", "year", "month") else "customer"
    # Разбивка по той же организации, что в фильтре, дала бы одну строку, равную итогу: показываем её контрагентов.
    if group_by == "customer" and customer_bin:
        group_by = "supplier"
    elif group_by == "supplier" and supplier_bin:
        group_by = "customer"
    filters = dict(query=query, customer_bin=customer_bin, supplier_bin=supplier_bin, enstru_code=enstru_code,
                   year_from=year_from, year_to=year_to, group_by=group_by)
    statuses = ("active", "transferred", "terminated", "changed") if include_terminated else VALID_STATUS
    params = {"statuses": list(statuses), "k": clamp_k(top_k)}
    by_units = group_by == "enstru" or bool(query or enstru_code)
    if by_units:
        keys = {"customer": "pf.customer_bin", "supplier": "pf.supplier_bin", "enstru": "pf.enstru_code",
                "year": "pf.year::text", "month": "to_char(pf.sign_date, 'YYYY-MM')"}
        cond = where(fact_conditions(filters, params) + ["pf.status_group = ANY(%(statuses)s)"])
        source = f"""SELECT {keys[group_by]} AS key, count(DISTINCT pf.contract_id) AS contracts,
                            sum(coalesce(pf.total_sum, pf.unit_price * pf.quantity)) AS amount
                     FROM mart.price_fact pf WHERE {cond} GROUP BY 1"""
        total_sql = f"""SELECT count(DISTINCT pf.contract_id) AS contracts,
                               sum(coalesce(pf.total_sum, pf.unit_price * pf.quantity)) AS amount
                        FROM mart.price_fact pf WHERE {cond}"""
    else:
        keys = {"customer": "cc.customer_bin", "supplier": "cc.supplier_bin", "year": "extract(year FROM cc.sign_date)::text",
                "month": "to_char(cc.sign_date, 'YYYY-MM')"}
        conditions = date_conditions(filters, params, "cc.sign_date") + ["cc.status_group = ANY(%(statuses)s)"]
        if customer_bin:
            params["customer_bin"] = customer_bin
            conditions.append("cc.customer_bin = %(customer_bin)s")
        if supplier_bin:
            params["supplier_bin"] = supplier_bin
            conditions.append("cc.supplier_bin = %(supplier_bin)s")
        cond = where(conditions)
        source = f"""SELECT {keys[group_by]} AS key, count(*) AS contracts, sum(cc.sum) AS amount
                     FROM mart.contract_current cc WHERE {cond} GROUP BY 1"""
        total_sql = f"SELECT count(*) AS contracts, sum(cc.sum) AS amount FROM mart.contract_current cc WHERE {cond}"
    total = ctx.one(total_sql, params)
    rows = ctx.rows(f"SELECT * FROM ({source}) g ORDER BY amount DESC NULLS LAST LIMIT %(k)s", params)
    segments = []
    for r in rows:
        label = r["key"]
        if group_by in ("customer", "supplier"):
            label = f"{subject_name(ctx, r['key']) or '—'} ({r['key']})"
        elif group_by == "enstru":
            enstru = ctx.one("SELECT name_ru FROM ref_enstru WHERE code = %(c)s", {"c": r["key"]})
            label = f"{r['key']} {enstru['name_ru'] if enstru else ''}".strip()
        segments.append({"label": label, "contracts": r["contracts"], "amount": float(r["amount"] or 0)})
    if by_units:
        biggest = ctx.rows(f"""SELECT pf.contract_id, min(pf.sign_date) AS sign_date, min(pf.customer_bin) AS customer_bin,
                                      min(pf.supplier_bin) AS supplier_bin,
                                      sum(coalesce(pf.total_sum, pf.unit_price * pf.quantity)) AS amount
                               FROM mart.price_fact pf WHERE {cond} GROUP BY pf.contract_id
                               ORDER BY amount DESC NULLS LAST LIMIT %(k)s""", params)
    else:
        biggest = ctx.rows(f"""SELECT cc.id AS contract_id, cc.sign_date, cc.customer_bin, cc.supplier_bin, cc.sum AS amount
                               FROM mart.contract_current cc WHERE {cond}
                               ORDER BY cc.sum DESC NULLS LAST LIMIT %(k)s""", params)
    top = [{"contract_id": r["contract_id"], "date": str(r["sign_date"]), "amount": float(r["amount"] or 0),
            "customer": subject_name(ctx, r["customer_bin"]) or r["customer_bin"],
            "supplier": subject_name(ctx, r["supplier_bin"]) or r["supplier_bin"] or "—",
            "link": contract_link(r["contract_id"])} for r in biggest]
    result = outcome("spend_summary", filters, ctx, n_objects=total["contracts"], method="sql_sum",
                     confidence="high", facts={"amount": float(total["amount"] or 0), "contracts": total["contracts"],
                                               "group_by": group_by, "terminated": include_terminated},
                     segments=segments, top=top)
    result["limitations"].append("sum_by_units" if by_units else "sum_by_contract")
    if not include_terminated:
        result["limitations"].append("terminated_excluded")
    return result


def resolve_enstru(ctx, filters):
    params = {}
    cond = where(fact_conditions(filters, params) + ["pf.is_benchmark"])
    return ctx.rows(f"""SELECT pf.enstru_code, pf.base_unit, max(pf.enstru_name) AS name, count(*) AS n
                        FROM mart.price_fact pf WHERE {cond} GROUP BY 1, 2 ORDER BY n DESC LIMIT 6""", params)


def group_stats_sql():
    return f"""count(*) AS n, count(DISTINCT pf.customer_bin) AS customers,
               sum(pf.price_per_base * pf.qty_base) / nullif(sum(pf.qty_base), 0) AS weighted_avg,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY pf.price_per_base) AS median,
               percentile_cont(0.25) WITHIN GROUP (ORDER BY pf.price_per_base) AS q1,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY pf.price_per_base) AS q3,
               min(pf.price_per_base) AS min_price, max(pf.price_per_base) AS max_price"""


def compare_prices(ctx, query=None, enstru_code=None, by="customer", customer_bins=None, supplier_bin=None,
                   year_from=None, year_to=None, top_k=TOP_K):
    by = by if by in SEGMENTS else "customer"
    filters = dict(query=query, enstru_code=enstru_code, customer_bins=customer_bins, supplier_bin=supplier_bin,
                   year_from=year_from, year_to=year_to, by=by)
    candidates = resolve_enstru(ctx, {k: v for k, v in filters.items() if k != "by"})
    if not candidates:
        return outcome("compare_prices", filters, ctx, n_objects=0, method="weighted_avg_median", confidence="none",
                       facts={"found": False}, segments=[], top=[])
    group = candidates[0]
    params = {"code": group["enstru_code"], "unit": group["base_unit"], "k": clamp_k(top_k)}
    base = {k: v for k, v in filters.items() if k not in ("query", "enstru_code", "by")}
    conditions = ["pf.is_benchmark", "pf.enstru_code = %(code)s", "pf.base_unit = %(unit)s"] + \
        fact_conditions(base, params)
    cond = where(conditions)
    overall = ctx.one(f"SELECT {group_stats_sql()} FROM mart.price_fact pf WHERE {cond}", params)
    key_sql, _ = SEGMENTS[by]
    rows = ctx.rows(f"""SELECT {key_sql} AS key, {group_stats_sql()}
                        FROM mart.price_fact pf WHERE {cond} GROUP BY 1 ORDER BY n DESC LIMIT %(k)s""", params)
    segments = []
    for r in rows:
        label = r["key"]
        if by in ("customer", "supplier"):
            label = f"{subject_name(ctx, r['key']) or '—'} ({r['key']})"
        elif by == "city" and r["key"]:
            kato = ctx.one("SELECT name_ru FROM ref_kato WHERE code = %(c)s", {"c": r["key"]})
            label = kato["name_ru"] if kato else CITY_NAMES.get(r["key"], r["key"])
        segments.append({"label": label or "—", **{k: as_float(r[k]) for k in
                                                   ("weighted_avg", "median", "q1", "q3", "min_price", "max_price")},
                         "n": r["n"], "customers": r["customers"]})

    extremes = ctx.rows(f"""
        WITH ranked AS (
            SELECT pf.contract_id, pf.unit_id, pf.sign_date, pf.customer_bin, pf.price_per_base,
                   row_number() OVER (PARTITION BY pf.contract_id ORDER BY pf.price_per_base DESC, pf.unit_id) AS top_rank,
                   row_number() OVER (PARTITION BY pf.contract_id ORDER BY pf.price_per_base ASC, pf.unit_id) AS bottom_rank
            FROM mart.price_fact pf WHERE {cond}
        )
        (SELECT *, 'max' AS side FROM ranked WHERE top_rank = 1 ORDER BY price_per_base DESC LIMIT 3)
        UNION ALL
        (SELECT *, 'min' AS side FROM ranked WHERE bottom_rank = 1 ORDER BY price_per_base ASC LIMIT 3)""", params)
    top = [{"contract_id": r["contract_id"], "unit_id": r["unit_id"], "date": str(r["sign_date"]),
            "customer": subject_name(ctx, r["customer_bin"]) or r["customer_bin"],
            "price": float(r["price_per_base"]), "side": r["side"], "link": contract_link(r["contract_id"])}
           for r in extremes]
    result = outcome("compare_prices", filters, ctx, n_objects=overall["n"], method="weighted_avg_median",
                     confidence=level_by_size(overall["n"]),
                     facts={"found": True, "enstru_code": group["enstru_code"], "enstru_name": group["name"],
                            "unit": group["base_unit"], "by": by,
                            **{k: as_float(overall[k]) for k in
                               ("weighted_avg", "median", "q1", "q3", "min_price", "max_price")},
                            "customers": overall["customers"]},
                     segments=segments, top=top)
    if len(candidates) > 1:
        result["limitations"].append("several_enstru")
        result["facts"]["other_codes"] = [c["enstru_code"] for c in candidates[1:4]]
    return result


def price_anomalies(ctx, query=None, enstru_code=None, customer_bin=None, supplier_bin=None, year_from=None,
                    year_to=None, threshold=DEVIATION_LIMIT, direction="both", min_group_size=MIN_GROUP_SIZE,
                    top_k=TOP_K):
    threshold = float(DEVIATION_LIMIT if threshold is None else threshold)
    threshold = threshold / 100 if threshold > 3 else threshold
    filters = dict(query=query, enstru_code=enstru_code, customer_bin=customer_bin, supplier_bin=supplier_bin,
                   year_from=year_from, year_to=year_to, threshold=threshold, direction=direction)
    params = {"threshold": threshold, "min_n": int(min_group_size), "k": clamp_k(top_k)}
    scan_params = {}
    deviation = {"up": "d.deviation > %(threshold)s", "down": "d.deviation < -%(threshold)s"}.get(
        direction, "abs(d.deviation) > %(threshold)s")
    cond = where(fact_conditions(filters, params) + ["d.n >= %(min_n)s", deviation])
    source = f"""FROM mart.price_deviation d
                 JOIN mart.price_fact pf ON pf.unit_id = d.unit_id
                 JOIN mart.contract_current cc ON cc.id = d.contract_id
                 LEFT JOIN mart.fair_price f ON f.unit_id = d.unit_id
                 WHERE {cond}"""
    summary = ctx.one(f"""SELECT count(*) AS n, count(DISTINCT (d.enstru_code, d.base_unit)) AS groups,
                                 count(*) FILTER (WHERE f.flag = 'suspect_error') AS suspect,
                                 count(*) FILTER (WHERE f.flag IN ('high', 'low')) AS confirmed,
                                 count(*) FILTER (WHERE f.flag IS NULL OR f.flag = 'insufficient') AS unchecked
                          {source}""", params)
    rows = ctx.rows(f"""SELECT d.unit_id, d.contract_id, d.enstru_code, d.enstru_name, d.item_name, d.base_unit,
                               d.price_per_base, d.ref_weighted_avg, d.n, d.deviation, d.qty_base, pf.sign_date,
                               pf.customer_bin, f.flag, f.confidence, f.fair_price, cc.contract_number,
                               (d.price_per_base - d.ref_weighted_avg) * d.qty_base AS excess
                        {source}
                        ORDER BY CASE WHEN f.flag IN ('high', 'low') AND f.confidence IN ('high', 'medium') THEN 0
                                      WHEN f.flag IN ('high', 'low') THEN 1
                                      WHEN f.flag = 'suspect_error' THEN 2 ELSE 3 END,
                                 abs((d.price_per_base - d.ref_weighted_avg) * d.qty_base) DESC NULLS LAST
                        LIMIT %(k)s""", params)
    top = [{"contract_id": r["contract_id"], "unit_id": r["unit_id"], "number": r["contract_number"],
            "date": str(r["sign_date"]), "customer": subject_name(ctx, r["customer_bin"]) or r["customer_bin"],
            "enstru": r["enstru_code"], "item": r["item_name"] or r["enstru_name"], "unit": r["base_unit"],
            "price": float(r["price_per_base"]), "reference": float(r["ref_weighted_avg"]),
            "deviation": float(r["deviation"]), "group_n": r["n"], "excess": float(r["excess"] or 0),
            "flag": r["flag"] or "insufficient", "confidence": r["confidence"] or "none",
            "fair_price": as_float(r["fair_price"]), "link": contract_link(r["contract_id"])} for r in rows]
    analyzed = ctx.one(f"""SELECT count(*) AS n FROM mart.price_deviation d
                           JOIN mart.price_fact pf ON pf.unit_id = d.unit_id
                           WHERE {where(fact_conditions(filters, scan_params) + ["d.n >= %(min_n)s"])}""",
                       {**scan_params, "min_n": params["min_n"]})["n"]
    solid = sum(1 for t in top if t["flag"] in ("high", "low") and t["confidence"] in ("high", "medium"))
    result = outcome("price_anomalies", filters, ctx, n_objects=analyzed, method="weighted_avg_leave_one_out",
                     confidence="medium" if solid else "low",
                     facts={"analyzed": analyzed, "matches": summary["n"], "groups": summary["groups"], "suspect": summary["suspect"],
                            "confirmed": summary["confirmed"], "unchecked": summary["unchecked"],
                            "threshold": threshold, "min_group_size": int(min_group_size), "direction": direction},
                     top=top)
    result["limitations"].append("heterogeneous_groups")
    if summary["suspect"]:
        result["limitations"].append("suspect_errors")
    if any(p["fair_price"] and not 1 / 1.5 < p["reference"] / p["fair_price"] < 1.5 for p in top):
        result["limitations"].append("weighted_skewed")
    return result


def peers_for(ctx, row):
    level = row["peer_level"]
    params = {"code": row["enstru_code"], "unit": row["base_unit"], "bin": row["customer_bin"],
              "date": row["sign_date"], "city": row["city_code"], "oblast": (row["city_code"] or "")[:2] + "%",
              "n": PEER_EXAMPLES}
    place = {"city": "AND pf.city_code = %(city)s", "oblast": "AND pf.city_code LIKE %(oblast)s"}.get(level, "")
    peers = ctx.rows(f"""SELECT pf.contract_id, pf.unit_id, pf.sign_date, pf.customer_bin, pf.price_per_base
                         FROM mart.price_fact pf
                         WHERE pf.is_benchmark AND pf.enstru_code = %(code)s AND pf.base_unit = %(unit)s
                           AND pf.customer_bin <> %(bin)s {place}
                           AND abs(pf.sign_date - %(date)s::date) <= 365
                         ORDER BY abs(pf.sign_date - %(date)s::date), pf.unit_id LIMIT %(n)s""", params)
    return [{"contract_id": p["contract_id"], "date": str(p["sign_date"]),
             "customer": subject_name(ctx, p["customer_bin"]) or p["customer_bin"],
             "price": float(p["price_per_base"]), "link": contract_link(p["contract_id"])} for p in peers]


def fair_price_check(ctx, lot_number=None, contract_id=None, unit_id=None, top_k=TOP_K):
    filters = dict(lot_number=lot_number, contract_id=contract_id, unit_id=unit_id)
    params = {}
    if lot_number:
        params["lot"] = str(lot_number).strip()
        target = "cu.lot_id IN (SELECT id FROM lot WHERE lot_number = %(lot)s)"
    elif unit_id:
        params["unit"] = int(unit_id)
        target = "pf.unit_id = %(unit)s"
    else:
        params["contract"] = int(contract_id or 0)
        target = "pf.contract_id = %(contract)s"

    rows = ctx.rows(f"""
        SELECT pf.unit_id, pf.contract_id, pf.customer_bin, pf.sign_date, pf.enstru_code, pf.enstru_name, pf.item_name,
               pf.base_unit, pf.city_code, pf.city_source, pf.price_per_base, pf.qty_base, pf.is_benchmark,
               pf.status_group, f.fair_price, f.q1, f.q3, f.n_peers, f.peer_level, f.deviation, f.excess_amount,
               f.flag, f.confidence, cc.contract_number, cc.trd_buy_id
        FROM mart.price_fact pf
        JOIN contract_unit cu ON cu.id = pf.unit_id
        JOIN mart.contract_current cc ON cc.id = pf.contract_id
        LEFT JOIN mart.fair_price f ON f.unit_id = pf.unit_id
        WHERE {target} ORDER BY pf.contract_id, pf.total_sum DESC NULLS LAST""", params)
    lot_known = bool(lot_number) and ctx.one("SELECT 1 AS x FROM lot WHERE lot_number = %(lot)s", params) is not None
    positions = []
    for r in rows:
        positions.append({"contract_id": r["contract_id"], "unit_id": r["unit_id"], "number": r["contract_number"],
                          "date": str(r["sign_date"]), "customer_bin": r["customer_bin"],
                          "enstru": r["enstru_code"], "item": r["item_name"] or r["enstru_name"], "unit": r["base_unit"],
                          "price": as_float(r["price_per_base"]), "qty": as_float(r["qty_base"]),
                          "fair_price": as_float(r["fair_price"]), "q1": as_float(r["q1"]), "q3": as_float(r["q3"]),
                          "n_peers": r["n_peers"], "peer_level": r["peer_level"] or None,
                          "deviation": as_float(r["deviation"]), "excess": as_float(r["excess_amount"]),
                          "flag": r["flag"] or ("not_comparable" if not r["is_benchmark"] else "insufficient"),
                          "confidence": r["confidence"] or "none", "city_source": r["city_source"],
                          "link": contract_link(r["contract_id"]), "announce_link": announce_link(r["trd_buy_id"]),
                          "row": r})
    top = positions[:clamp_k(top_k)]
    for p in top:
        p["customer"] = subject_name(ctx, p["customer_bin"]) or p["customer_bin"]
        p["peers"] = peers_for(ctx, p["row"]) if p["peer_level"] else []
    for p in positions:
        p.pop("row")

    counts = {}
    for p in positions:
        counts[p["flag"]] = counts.get(p["flag"], 0) + 1
    levels = [p["confidence"] for p in positions if p["confidence"] != "none"]
    order = {"high": 3, "medium": 2, "low": 1}
    confidence = min(levels, key=order.get) if levels else "none"

    peers_by_group = {}
    for p in positions:
        key = (p["enstru"], p["unit"])
        peers_by_group[key] = max(peers_by_group.get(key, 0), p["n_peers"] or 0)
    result = outcome("fair_price_check", filters, ctx, n_objects=sum(peers_by_group.values()),
                     method="median_iqr_peers_adjusted", confidence=confidence,
                     facts={"positions": len(positions), "shown": len(top), "counts": counts,
                            "lot_found": lot_known if lot_number else None}, top=top)
    if any(p["city_source"] == "customer" for p in positions):
        result["limitations"].append("city_from_customer")
    if any(p["peer_level"] == "country" for p in positions):
        result["limitations"].append("country_level")
    spread = same_code_spread(positions)
    if spread:
        result["limitations"].append("same_code_spread")
        result["facts"]["spread"] = spread
    return result


def same_code_spread(positions, limit=3):
    prices = {}
    for p in positions:
        if p["price"] and p["fair_price"]:
            prices.setdefault((p["enstru"], p["unit"]), []).append(p["price"])
    for (code, unit), values in prices.items():
        if len(values) >= 2 and max(values) / min(values) >= limit:
            return {"code": code, "unit": unit, "min": min(values), "max": max(values), "n": len(values)}
    return None


def volume_anomalies(ctx, customer_bin=None, enstru_code=None, query=None, year=None, min_ratio=VOLUME_MIN_RATIO,
                     top_k=TOP_K):
    if not year:
        year = ctx.one("SELECT max(year) AS y FROM mart.volume_anomaly WHERE flag <> 'no_history'")["y"]
    year = year or max(DATA_YEARS)
    filters = dict(customer_bin=customer_bin, enstru_code=enstru_code, query=query, year=year, min_ratio=min_ratio)
    params = {"year": int(year), "ratio": float(min_ratio), "k": clamp_k(top_k)}
    conditions = ["v.year = %(year)s"]
    if customer_bin:
        params["bin"] = customer_bin
        conditions.append("v.customer_bin = %(bin)s")
    if enstru_code:
        params["code"] = enstru_code
        conditions.append("v.enstru_code = %(code)s")
    conditions += text_conditions(query, "coalesce(v.enstru_name, '')", params)
    base = where(conditions)
    summary = ctx.one(f"""SELECT count(*) FILTER (WHERE v.flag <> 'no_history') AS compared,
                                 count(*) FILTER (WHERE v.ratio >= %(ratio)s) AS matches,
                                 count(*) FILTER (WHERE v.flag = 'high') AS strong
                          FROM mart.volume_anomaly v WHERE {base}""", params)
    rows = ctx.rows(f"""SELECT v.*, {name_sql(ctx, 's')} AS customer
                        FROM mart.volume_anomaly v LEFT JOIN subject s ON s.bin = v.customer_bin
                        WHERE {base} AND v.ratio >= %(ratio)s
                        ORDER BY (v.flag = 'high') DESC, v.extra_amount DESC NULLS LAST LIMIT %(k)s""", params)
    top = []
    for r in rows:
        samples = ctx.rows("""SELECT contract_id FROM mart.price_fact
                              WHERE customer_bin = %(b)s AND enstru_code = %(c)s
                              GROUP BY contract_id ORDER BY max(sign_date) DESC, contract_id DESC LIMIT 3""",
                           {"b": r["customer_bin"], "c": r["enstru_code"]})
        top.append({"customer": r["customer"] or r["customer_bin"], "enstru": r["enstru_code"],
                    "item": r["enstru_name"], "unit": r["base_unit"], "year": r["year"],
                    "plan_qty": float(r["plan_qty"]), "baseline_qty": float(r["baseline_qty"]),
                    "baseline_years": r["baseline_years"], "ratio": float(r["ratio"]),
                    "robust_z": as_float(r["robust_z"]), "extra_amount": as_float(r["extra_amount"]),
                    "flag": r["flag"], "plan_ids": list(r["plan_ids"] or [])[:5],
                    "links": [contract_link(s["contract_id"]) for s in samples]})
    compared = summary["compared"] or 0
    result = outcome("volume_anomalies", filters, ctx, period={"from": f"{int(year)}-01-01", "to": f"{int(year)}-12-31"},
                     n_objects=compared, method="plan_qty_vs_history_mad",
                     confidence="medium" if compared >= 30 and top and top[0]["baseline_years"] >= 2 else "low",
                     facts={"year": int(year), "compared": compared, "matches": summary["matches"] or 0,
                            "strong": summary["strong"] or 0, "min_ratio": float(min_ratio)}, top=top)
    result["limitations"] += ["plan_based", "short_history"]
    return result


TOOLS = {
    "search_contracts": search_contracts,
    "spend_summary": spend_summary,
    "compare_prices": compare_prices,
    "price_anomalies": price_anomalies,
    "fair_price_check": fair_price_check,
    "volume_anomalies": volume_anomalies,
}