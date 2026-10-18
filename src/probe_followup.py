import os
from collections import Counter

import psycopg

from explore import load_token, log, make_session
from bins import bins
from explore_details import run
from ingest import DEFAULT_DSN

BUY_BIN = "000740001307"
SINCE_DATES = ["2026-09-01", "2026-06-01", "2026-03-11", "2026-01-01", "2025-06-01", "2024-01-01"]
SAMPLE = 60

MISSING_SQL = """
SELECT plan_id FROM (
    SELECT DISTINCT plan_id FROM contract_unit
    WHERE plan_id IS NOT NULL AND plan_id NOT IN (SELECT id FROM plan)
) missing
ORDER BY random() LIMIT %s
"""
MISSING_COUNT_SQL = """
SELECT count(DISTINCT plan_id) FROM contract_unit
WHERE plan_id IS NOT NULL AND plan_id NOT IN (SELECT id FROM plan)
"""


def check_since_dates(session):
    log("=== TrdBuy: число записей при разных датах indexDate ===")
    for since in SINCE_DATES:
        filter_text = (f'orgBin: "{BUY_BIN}", publishDate: ["2024-01-01", "2026-12-31"], '
                       f'indexDate: ["{since} 00:00:00"]')
        result = run(session, f"{{ TrdBuy(limit: 1, filter: {{{filter_text}}}) {{ id }} }}")
        log(f"от {since}: totalCount={result['total']} {result['error']}")


def check_missing_plans(session):
    with psycopg.connect(os.getenv("DATABASE_URL", DEFAULT_DSN)) as conn:
        distinct_missing = conn.execute(MISSING_COUNT_SQL).fetchone()[0]
        ids = [row[0] for row in conn.execute(MISSING_SQL, (SAMPLE,))]

    log(f"\n=== Недостающие планы: всего уникальных {distinct_missing}, проверяем выборку {len(ids)} ===")
    query = (f"{{ Plans(limit: 200, filter: {{id: {ids}}}) "
             f"{{ id subjectBiin plnPointYear isActive isDeleted }} }}")
    result = run(session, query)
    if result["error"]:
        log(f"Ошибка API: {result['error']}")
        return

    rows = result["data"].get("Plans") or []
    log(f"API вернул {len(rows)} из {len(ids)}")
    log("по годам плана: " + str(dict(Counter(r["plnPointYear"] for r in rows))))
    log("БИН среди наших 27: " + str(dict(Counter(r["subjectBiin"] in bins for r in rows))))
    log("isActive: " + str(dict(Counter(r["isActive"] for r in rows))))
    log("isDeleted: " + str(dict(Counter(r["isDeleted"] for r in rows))))


def main():
    session = make_session(load_token())
    check_since_dates(session)
    check_missing_plans(session)


if __name__ == "__main__":
    main()