import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import psycopg
import requests
from psycopg.types.json import Jsonb

from explore import graphql, load_token, log, make_session
from bins import bins

PAGE_SIZE = 200
DATE_FROM = "2024-01-01"
DATE_TO = "2026-12-31"
YEARS = [2024, 2025, 2026]
OVERLAP = timedelta(days=1)
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_DSN = "postgresql://zakup:zakup@localhost:5433/zakup"
REST_URL = "https://ows.goszakup.gov.kz/v3"
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
REFS = {
    "contract_status": "ref_contract_status",
    "subject_type": "ref_subject_type",
    "trade_method": "ref_trade_methods",
    "lot_status": "ref_lots_status",
    "buy_status": "ref_buy_status",
}


class ApiError(RuntimeError):
    pass


def num(value):
    return None if value in (None, "") else value


def flag(value):
    return None if value is None else bool(int(value))


def code(value):
    if isinstance(value, list):
        value = value[0] if value else None
    return None if value in (None, "", 0) else str(value)


def as_list(value):
    if isinstance(value, list):
        return value
    return [] if value in (None, "") else [value]


def parse_ts(text):
    return datetime.fromisoformat(text.replace("T", " ")[:19])


def unique_by_code(items):
    return list({item["code"]: item for item in items if item and item.get("code")}.values())


def upsert(cur, table, columns, rows, key=("id",)):
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column not in key)
    cur.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}",
        rows,
    )


def save_raw(cur, entity, rows):
    now = datetime.now(timezone.utc)
    upsert(cur, "raw.record", ("entity", "id", "payload", "fetched_at"),
           [(entity, r["id"], Jsonb(r), now) for r in rows], key=("entity", "id"))


def save_subjects(cur, subjects):
    names = {bin_: (name_ru, name_kz) for bin_, name_ru, name_kz in subjects if bin_ and name_ru}
    if names:
        cur.executemany(
            "INSERT INTO subject (bin, name_ru, name_kz) VALUES (%s, %s, %s) ON CONFLICT (bin) DO UPDATE SET "
            "name_ru = EXCLUDED.name_ru, name_kz = COALESCE(EXCLUDED.name_kz, subject.name_kz)",
            [(bin_, name_ru, name_kz) for bin_, (name_ru, name_kz) in names.items()])


def save_plans(cur, rows, bin_):
    save_raw(cur, "plan", rows)
    upsert(cur, "plan",
           ("id", "root_id", "customer_bin", "name_ru", "extra_desc", "enstru_code", "units_code",
            "price", "count", "amount", "year", "is_active", "is_deleted", "subject_type_id", "trade_method_id"),
           [(r["id"], r.get("rootrecordId"), r.get("subjectBiin") or bin_, r.get("nameRu"), r.get("extraDescRu"),
             code(r.get("refEnstruCode")), code(r.get("refUnitsCode")), num(r.get("price")),
             num(r.get("count")), num(r.get("amount")), r.get("plnPointYear"),
             flag(r.get("isActive")), flag(r.get("isDeleted")),
             r.get("refSubjectTypeId"), r.get("refTradeMethodsId")) for r in rows])
    save_subjects(cur, [(r.get("subjectBiin"), r.get("subjectNameRu"), r.get("subjectNameKz")) for r in rows])

    upsert(cur, "ref_units", ("code", "name_ru", "alpha_code"),
           [(u["code"], u.get("nameRu"), u.get("alphaCode"))
            for u in unique_by_code(r.get("RefUnits") for r in rows)], key=("code",))

    upsert(cur, "ref_enstru", ("code", "name_ru", "edizm", "mkei"),
           [(e["code"], e.get("nameRu"), e.get("edizm"), e.get("mkei"))
            for e in unique_by_code(r.get("RefEnstru") for r in rows)], key=("code",))

    places = [(r["id"], p) for r in rows for p in r.get("PlansKato") or [] if p.get("refKatoCode")]

    upsert(cur, "ref_kato", ("code", "name_ru", "full_name_ru", "parent_code", "level"),
           [(k["code"], k.get("nameRu"), k.get("fullNameRu"), k.get("parentCode") or None, k.get("level"))
            for k in unique_by_code(p.get("RefKato") for _, p in places)], key=("code",))

    upsert(cur, "plan_kato", ("plan_id", "kato_code", "place_name", "count"),
           [(plan_id, p["refKatoCode"], p.get("fullDeliveryPlaceNameRu"), num(p.get("count")))
            for plan_id, p in places], key=("plan_id", "kato_code"))


def save_trd_buy(cur, rows, bin_=None):
    save_raw(cur, "trd_buy", rows)
    upsert(cur, "trd_buy",
           ("id", "number_anno", "org_bin", "customer_bin", "name_ru", "publish_date", "fin_year", "status_id",
            "trade_method_id", "subject_type_id"),
           [(r["id"], r.get("numberAnno"), r.get("orgBin") or bin_, r.get("customerBin"), r.get("nameRu"),
             r.get("publishDate"), Jsonb(as_list(r.get("finYear"))), r.get("refBuyStatusId"),
             r.get("refTradeMethodsId"), r.get("refSubjectTypeId")) for r in rows])
    save_subjects(cur, [(r.get("orgBin") or bin_, r.get("orgNameRu"), r.get("orgNameKz")) for r in rows])


def save_lots(cur, rows, bin_):
    rows = [r for r in rows if ((r.get("TrdBuy") or {}).get("publishDate") or "") >= DATE_FROM]
    save_trd_buy(cur, list({r["TrdBuy"]["id"]: r["TrdBuy"] for r in rows}.values()))
    save_raw(cur, "lot", rows)
    upsert(cur, "lot",
           ("id", "customer_bin", "trd_buy_id", "name_ru", "count", "amount",
            "plan_ids", "enstru_list", "kato_list", "lot_number", "status_id", "trade_method_id"),
           [(r["id"], bin_, r.get("trdBuyId"), r.get("nameRu"), num(r.get("count")), num(r.get("amount")),
             Jsonb(as_list(r.get("pointList"))), Jsonb(as_list(r.get("enstruList"))),
             Jsonb(as_list(r.get("plnPointKatoList"))), r.get("lotNumber"), r.get("refLotStatusId"),
             r.get("refTradeMethodsId")) for r in rows])
    save_subjects(cur, [(bin_, r.get("customerNameRu"), r.get("customerNameKz")) for r in rows])


def nested_plans(contracts):
    plans = (plan for c in contracts for u in c.get("ContractUnits") or [] for plan in as_list(u.get("Plans")))
    return list({plan["id"]: plan for plan in plans if plan and plan.get("id")}.values())


def contract_subjects(rows):
    for r in rows:
        for role in ("Supplier", "Customer"):
            for s in as_list(r.get(role)):
                yield s.get("bin") or s.get("iin"), s.get("nameRu"), s.get("nameKz")


def save_contracts(cur, rows, bin_):
    save_plans(cur, nested_plans(rows), bin_)
    save_raw(cur, "contract", rows)
    upsert(cur, "contract",
           ("id", "customer_bin", "supplier_bin", "trd_buy_id", "sign_date", "sum", "fin_year",
            "status_id", "parent_id", "root_id", "deleted",
            "contract_number", "trd_buy_number", "subject_type_id", "trade_method_id"),
           [(r["id"], bin_, r.get("supplierBiin"), r.get("trdBuyId"), r.get("signDate"),
             num(r.get("contractSum")), r.get("finYear"), r.get("refContractStatusId"),
             r.get("parentId"), r.get("rootId"), flag(r.get("deleted")),
             r.get("contractNumber") or r.get("contractNumberSys"), r.get("trdBuyNumberAnno"),
             r.get("refSubjectTypeId") or r.get("refSubjectTypesId"), r.get("faktTradeMethodsId"))
            for r in rows])
    save_subjects(cur, contract_subjects(rows))

    upsert(cur, "contract_unit",
           ("id", "contract_id", "lot_id", "plan_id", "enstru_code", "item_price",
            "item_price_wnds", "quantity", "total_sum", "deleted", "cr_deleted"),
           [(u["id"], r["id"], u.get("lotId"), u.get("plnPointId"), code(u.get("refEnstruCode")),
             num(u.get("itemPrice")), num(u.get("itemPriceWnds")), num(u.get("quantity")),
             num(u.get("totalSum")), flag(u.get("deleted")), flag(u.get("crDeleted")))
            for r in rows for u in r.get("ContractUnits") or []])


TRD_BUY_FIELDS = """id numberAnno nameRu publishDate finYear refBuyStatusId refTradeMethodsId refSubjectTypeId
    orgBin orgNameRu orgNameKz customerBin"""

PLAN_FIELDS = """id rootrecordId subjectBiin subjectNameRu subjectNameKz nameRu extraDescRu refEnstruCode
    refUnitsCode price count amount plnPointYear isActive isDeleted indexDate refSubjectTypeId refTradeMethodsId
    RefUnits { code nameRu alphaCode }
    RefEnstru { code nameRu edizm mkei }
    PlansKato { refKatoCode fullDeliveryPlaceNameRu count
                RefKato { code nameRu fullNameRu parentCode level } }"""

CONTRACT_FIELDS = (
    "id contractSum signDate finYear trdBuyId supplierBiin refContractStatusId parentId rootId deleted indexDate "
    "contractNumber contractNumberSys trdBuyNumberAnno refSubjectTypeId refSubjectTypesId faktTradeMethodsId "
    "Supplier { bin iin nameRu nameKz } Customer { bin nameRu nameKz } "
    "ContractUnits { id lotId plnPointId itemPrice itemPriceWnds quantity totalSum refEnstruCode deleted crDeleted "
    "Plans { " + PLAN_FIELDS + " } }"
)


@dataclass
class Source:
    name: str
    query: str
    fields: str
    bin_filter: str
    date_filter: str
    save: Callable


SOURCES = [
    Source(
        name="plans",
        query="Plans",
        fields=PLAN_FIELDS,
        bin_filter='subjectBiin: "{bin}"',
        date_filter=f"plnPointYear: {YEARS}",
        save=save_plans,
    ),
    Source(
        name="trd_buy",
        query="TrdBuy",
        fields=TRD_BUY_FIELDS,
        bin_filter='orgBin: "{bin}"',
        date_filter=f'publishDate: ["{DATE_FROM}", "{DATE_TO}"]',
        save=save_trd_buy,
    ),
    Source(
        name="lots",
        query="Lots",
        fields="""id lotNumber nameRu count amount pointList enstruList plnPointKatoList trdBuyId
                  refLotStatusId refTradeMethodsId customerNameRu customerNameKz
                  TrdBuy { """ + TRD_BUY_FIELDS + " }",
        bin_filter='customerBin: "{bin}"',
        date_filter="",
        save=save_lots,
    ),
    Source(
        name="contracts",
        query="Contract",
        fields=CONTRACT_FIELDS,
        bin_filter='customerBin: "{bin}"',
        date_filter=f'signDate: ["{DATE_FROM}", "{DATE_TO}"]',
        save=save_contracts,
    ),
]


def build_filter(source, bin_, since):
    parts = [source.bin_filter.format(bin=bin_), source.date_filter]
    if since:
        parts.append(f'indexDate: ["{parse_ts(since) - OVERLAP:{TS_FORMAT}}"]')
    return ", ".join(part for part in parts if part)


def fetch_pages(session, source, filter_text):
    after = None
    while True:
        after_arg = f", after: {after}" if after else ""
        query = (f"{{ {source.query}(limit: {PAGE_SIZE}{after_arg}, filter: {{{filter_text}}}) "
                 f"{{ {source.fields} }} }}")
        result = graphql(session, query)
        body = result["body"] or {}
        if result["status"] != 200 or body.get("errors"):
            message = (body.get("errors") or [{}])[0].get("message") or result["text"]
            raise ApiError(f"{source.query}: HTTP {result['status']} {message[:200]}")

        rows = (body.get("data") or {}).get(source.query) or []
        page_info = (body.get("extensions") or {}).get("pageInfo") or {}
        yield rows, page_info

        if not rows or not page_info.get("hasNextPage") or not page_info.get("lastId"):
            return
        after = page_info["lastId"]


def newest_index_date(rows, page_info, current):
    stamps = [r["indexDate"] for r in rows if r.get("indexDate")] or [page_info.get("lastIndexDate")]
    dates = [parse_ts(s) for s in stamps if s] + ([current] if current else [])
    return max(dates, default=None)


def load_state(conn, entity, bin_):
    row = conn.execute(
        "SELECT last_index_date FROM etl_state WHERE entity = %s AND bin = %s", (entity, bin_)
    ).fetchone()
    return row[0] if row else None


def save_state(conn, entity, bin_, newest, loaded):
    conn.execute(
        """INSERT INTO etl_state (entity, bin, last_index_date, rows_loaded) VALUES (%s, %s, %s, %s)
           ON CONFLICT (entity, bin) DO UPDATE SET
               last_index_date = COALESCE(EXCLUDED.last_index_date, etl_state.last_index_date),
               rows_loaded = etl_state.rows_loaded + EXCLUDED.rows_loaded,
               updated_at = now()""",
        (entity, bin_, f"{newest:{TS_FORMAT}}" if newest else None, loaded),
    )


def load_source(conn, session, source, bin_, full):
    since = None if full else load_state(conn, source.name, bin_)
    loaded, newest = 0, None
    for rows, page_info in fetch_pages(session, source, build_filter(source, bin_, since)):
        with conn.cursor() as cur:
            source.save(cur, rows, bin_)
        conn.commit()
        loaded += len(rows)
        newest = newest_index_date(rows, page_info, newest)
    save_state(conn, source.name, bin_, newest, loaded)
    conn.commit()
    return loaded, since


def prepare_database(conn, selected_bins):
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO subject (bin, is_target) VALUES (%s, true) "
                        "ON CONFLICT (bin) DO UPDATE SET is_target = true", [(b,) for b in selected_bins])
    conn.commit()


def fetch_rest(session, path):
    items, url = [], f"{REST_URL}/{path}"
    while url:
        response = session.get(url, timeout=60)
        if response.status_code != 200:
            raise ApiError(f"{path}: HTTP {response.status_code}")
        body = response.json()
        items.extend(body.get("items") or [])
        url = body.get("next_page")
        if url and url.startswith("/"):
            url = REST_URL.removesuffix("/v3") + url
    return items


def load_refs(conn, session):
    for kind, path in REFS.items():
        items = fetch_rest(session, f"refs/{path}")
        with conn.cursor() as cur:
            upsert(cur, "ref_status", ("kind", "id", "code", "name_ru", "name_kz"),
                   [(kind, i["id"], i.get("code"), i.get("name_ru"), i.get("name_kz")) for i in items],
                   key=("kind", "id"))
        conn.commit()
        log(f"{path}: {len(items)}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-bins", type=int, default=0, help="взять только первые N БИНов")
    parser.add_argument("--only", default="", help="через запятую: plans,trd_buy,lots,contracts")
    parser.add_argument("--full", action="store_true", help="загрузить всё заново, игнорируя прошлые запуски")
    return parser.parse_args()


def main():
    args = parse_args()
    selected_bins = bins[: args.limit_bins] if args.limit_bins else bins

    known = [s.name for s in SOURCES]
    names = args.only.split(",") if args.only else known
    unknown = set(names) - set(known)
    if unknown:
        sys.exit(f"Неизвестные источники: {', '.join(unknown)}. Доступны: {', '.join(known)}")
    sources = [s for s in SOURCES if s.name in names]

    session = make_session(load_token())
    failures = []

    with psycopg.connect(os.getenv("DATABASE_URL", DEFAULT_DSN)) as conn:
        prepare_database(conn, selected_bins)
        log("=== справочники ===")
        try:
            load_refs(conn, session)
        except (ApiError, requests.RequestException, psycopg.Error) as error:
            conn.rollback()
            failures.append(f"справочники: {error}")
            log(f"ОШИБКА {error}")
        for source in sources:
            log(f"\n=== {source.name} ===")
            for bin_ in selected_bins:
                try:
                    loaded, since = load_source(conn, session, source, bin_, args.full)
                    log(f"{bin_}: {loaded} записей" + (f" (изменения с {since})" if since else ""))
                except (ApiError, psycopg.Error) as error:
                    conn.rollback()
                    failures.append(f"{source.name} {bin_}: {error}")
                    log(f"{bin_}: ОШИБКА {error}")

    if failures:
        log(f"\nОшибок: {len(failures)}")
        for failure in failures:
            log(f"  {failure}")
        sys.exit(1)
    log("\nГотово.")


if __name__ == "__main__":
    main()