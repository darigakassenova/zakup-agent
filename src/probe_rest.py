import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from explore import load_token, log, make_session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bins import bins  # noqa: E402

BASE = "https://ows.goszakup.gov.kz"
OUT = ROOT / "data" / "raw" / "rest"
REFS = ["ref_contract_status", "ref_subject_type", "ref_trade_methods", "ref_lots_status", "ref_buy_status"]
MAX_PAGES = 20


def get(session, path):
    response = session.get(path if path.startswith("http") else BASE + path, timeout=60)
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}: {response.text[:200]}"
    return response.json(), None


def fetch_all(session, path, max_pages=MAX_PAGES):
    body, error = get(session, path)
    if error:
        return None, None, error
    items = list(body.get("items", []))
    total = body.get("total")
    pages = 1
    while body.get("next_page") and pages < max_pages:
        body, error = get(session, body["next_page"])
        if error:
            break
        items.extend(body.get("items", []))
        pages += 1
    return items, total, error


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def show_refs(session):
    for ref in REFS:
        items, total, error = fetch_all(session, f"/v3/refs/{ref}")
        if items is None:
            log(f"\n{ref}: ошибка {error}")
            continue
        save(ref, items)
        log(f"\n{ref}: записей {len(items)} (total={total})")
        for item in items[:40]:
            log("  " + json.dumps(item, ensure_ascii=False)[:160])


def show_subject(session):
    for bin_ in bins[:2]:
        body, error = get(session, f"/v3/subject/biin/{bin_}")
        log(f"\nУчастник {bin_}: {'ошибка ' + error if error else ''}")
        if body:
            save(f"subject_{bin_}", body)
            log("  поля: " + ", ".join(sorted(body.keys())))
            for key in ("name_ru", "name_kz", "customer", "supplier", "organizer", "kato_list"):
                if key in body:
                    log(f"  {key}: {json.dumps(body[key], ensure_ascii=False)[:120]}")


def show_journal(session):
    today = date.today()
    path = f"/v3/journal?date_from={today - timedelta(days=1)}&date_to={today}"
    items, total, error = fetch_all(session, path, max_pages=5)
    log(f"\nЖурнал за сутки: total={total}, получено {len(items or [])} {('ошибка ' + error) if error else ''}")
    if not items:
        return
    save("journal_sample", items)
    log("  поля: " + ", ".join(sorted(items[0].keys())))
    counts = Counter((i.get("service_name"), i.get("action")) for i in items)
    for (service, action), n in counts.most_common(20):
        log(f"  {service} {action}: {n}")
    for item in items[:5]:
        log("  " + json.dumps(item, ensure_ascii=False)[:200])


def main():
    session = make_session(load_token())
    show_refs(session)
    show_subject(session)
    show_journal(session)
    log(f"\nПолные ответы сохранены в {OUT}")


if __name__ == "__main__":
    main()