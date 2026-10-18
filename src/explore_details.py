import json
import time

from explore import OUT, PAUSE, graphql, load_token, log, make_session, save_json
from bins import bins

SAMPLE_BIN = "020440003656"
YEARS = [2024, 2025, 2026]
DATE_RANGE = ["2024-01-01", "2026-12-31"]
PAGE_SIZES = [50, 200, 1000]

CONTRACT_SAMPLE = """
{ Contract(limit: 2, filter: {customerBin: "%(bin)s", signDate: ["2025-01-01", "2025-12-31"]}) {
    id contractSum signDate finYear trdBuyId customerBin supplierBiin refContractStatusId parentId rootId deleted
    ContractUnits {
      id lotId plnPointId itemPrice itemPriceWnds quantity totalSum refEnstruCode deleted crDeleted
      Plans { id nameRu refEnstruCode refUnitsCode price count }
    }
} }
"""

PLANS_SAMPLE = """
{ Plans(limit: 2, filter: {subjectBiin: "%(bin)s", plnPointYear: [2025]}) {
    id rootrecordId nameRu extraDescRu refEnstruCode refUnitsCode price count amount plnPointYear isActive isDeleted
    RefUnits { code nameRu alphaCode }
    RefEnstru { code nameRu edizm mkei }
    PlansKato { refKatoCode fullDeliveryPlaceNameRu count RefKato { code nameRu fullNameRu parentCode level } }
} }
"""

LOTS_SAMPLE = """
{ Lots(limit: 2, filter: {customerBin: "%(bin)s"}) {
    id nameRu count amount pointList enstruList plnPointKatoList trdBuyId
    TrdBuy { id publishDate finYear refBuyStatusId }
} }
"""

SAMPLES = {
    "contract_with_units": CONTRACT_SAMPLE,
    "plans_with_refs": PLANS_SAMPLE,
    "lots_with_trdbuy": LOTS_SAMPLE,
}

DATED_COUNTS = {
    "Plans": 'subjectBiin: "%(bin)s", plnPointYear: ' + json.dumps(YEARS),
    "Contract": 'customerBin: "%(bin)s", signDate: ' + json.dumps(DATE_RANGE),
    "TrdBuy": 'orgBin: "%(bin)s", publishDate: ' + json.dumps(DATE_RANGE),
}


def run(session, query):
    started = time.time()
    result = graphql(session, query)
    body = result["body"] or {}
    errors = body.get("errors") or []
    if errors:
        error = errors[0].get("message", "")[:150]
    else:
        error = "" if result["status"] == 200 else result["text"][:150]
    return {
        "body": body,
        "data": body.get("data") or {},
        "total": ((body.get("extensions") or {}).get("pageInfo") or {}).get("totalCount"),
        "error": error,
        "seconds": max(time.time() - started - PAUSE, 0),
    }


def check_page_sizes(session):
    lines = ["=== РАЗМЕР СТРАНИЦЫ (Plans) ==="]
    for size in PAGE_SIZES:
        query = f'{{ Plans(limit: {size}, filter: {{subjectBiin: "{SAMPLE_BIN}"}}) {{ id }} }}'
        result = run(session, query)
        received = len(result["data"].get("Plans") or [])
        lines.append(f"limit={size}: получено {received} записей за {result['seconds']:.1f} с {result['error']}")
        log(lines[-1])
    return lines


def save_samples(session):
    lines = ["\n=== ОБРАЗЦЫ ВЛОЖЕННЫХ ДАННЫХ ==="]
    for name, template in SAMPLES.items():
        result = run(session, template % {"bin": SAMPLE_BIN})
        save_json(f"probe_{name}", result["body"])
        status = result["error"] or "ok"
        lines.append(f"{name}: {status}")
        log(lines[-1])
    return lines


def count_with_dates(session, selected_bins):
    lines = ["\n=== ЗАПИСЕЙ ЗА 2024-2026 ПО БИНАМ ===", "БИН | " + " | ".join(DATED_COUNTS)]
    totals = dict.fromkeys(DATED_COUNTS, 0)
    errors = []
    for bin_ in selected_bins:
        counts = []
        for entity, filter_template in DATED_COUNTS.items():
            query = f"{{ {entity}(limit: 1, filter: {{{filter_template % {'bin': bin_}}}}) {{ id }} }}"
            result = run(session, query)
            counts.append(result["total"])
            totals[entity] += result["total"] or 0
            if result["error"]:
                errors.append(f"{entity} {bin_}: {result['error']}")
        lines.append(f"{bin_} | " + " | ".join(str(count) for count in counts))
        log(lines[-1])
    lines.append("ИТОГО | " + " | ".join(str(totals[entity]) for entity in DATED_COUNTS))
    lines.append(f"\nОшибок: {len(errors)}")
    lines.extend(f"  {error}" for error in errors[:10])
    return lines


def main():
    session = make_session(load_token())
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    report += check_page_sizes(session)
    report += save_samples(session)
    report += count_with_dates(session, bins)
    (OUT / "_details_report.txt").write_text("\n".join(report), encoding="utf-8")
    log("\nГотово. Отчёт: data/raw/_details_report.txt")


if __name__ == "__main__":
    main()