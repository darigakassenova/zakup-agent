from explore import make_session, load_token, log
from explore_details import run

PLANS_BIN = "020440003656"
BUY_BIN = "000740001307"

PLANS_FILTERS = {
    "без indexDate": f'subjectBiin: "{PLANS_BIN}", plnPointYear: [2024, 2025, 2026]',
    "indexDate от 2026-10-01": f'subjectBiin: "{PLANS_BIN}", plnPointYear: [2024, 2025, 2026], indexDate: ["2026-10-01 00:00:00"]',
    "indexDate от 2026-10-12": f'subjectBiin: "{PLANS_BIN}", plnPointYear: [2024, 2025, 2026], indexDate: ["2026-10-12 00:00:00"]',
    "indexDate за 2025 год": f'subjectBiin: "{PLANS_BIN}", plnPointYear: [2024, 2025, 2026], indexDate: ["2025-01-01 00:00:00", "2025-12-31 23:59:59"]',
}

BUY_FILTER = f'orgBin: "{BUY_BIN}", publishDate: ["2024-01-01", "2026-12-31"]'


def count(session, entity, filter_text):
    result = run(session, f"{{ {entity}(limit: 1, filter: {{{filter_text}}}) {{ id }} }}")
    return result["total"], result["error"]


def walk_pages(session, filter_text):
    after, page, rows = None, 0, 0
    while True:
        after_arg = f", after: {after}" if after else ""
        query = f"{{ TrdBuy(limit: 200{after_arg}, filter: {{{filter_text}}}) {{ id }} }}"
        body = run(session, query)["body"]
        page_info = (body.get("extensions") or {}).get("pageInfo") or {}
        got = len((body.get("data") or {}).get("TrdBuy") or [])
        page, rows = page + 1, rows + got
        log(f"  страница {page}: {got} записей, hasNextPage={page_info.get('hasNextPage')}, lastId={page_info.get('lastId')}")
        if not got or not page_info.get("hasNextPage") or not page_info.get("lastId"):
            return rows
        after = page_info["lastId"]


def main():
    session = make_session(load_token())

    log("=== Plans: сколько записей возвращает API ===")
    for title, filter_text in PLANS_FILTERS.items():
        total, error = count(session, "Plans", filter_text)
        log(f"{title}: totalCount={total} {error}")

    log("\n=== TrdBuy: постраничная выгрузка ===")
    log(f"без indexDate: totalCount={count(session, 'TrdBuy', BUY_FILTER)[0]}")
    log("постранично, без indexDate:")
    log(f"  всего получено {walk_pages(session, BUY_FILTER)}")

    since = f'{BUY_FILTER}, indexDate: ["2026-03-11 12:20:52"]'
    log(f"с indexDate от 2026-03-11: totalCount={count(session, 'TrdBuy', since)[0]}")
    log("постранично, с indexDate:")
    log(f"  всего получено {walk_pages(session, since)}")


if __name__ == "__main__":
    main()