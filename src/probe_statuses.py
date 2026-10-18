import json

from explore import OUT, is_scalar, load_token, log, make_session
from explore_details import run

STATUS_IDS = [190, 340, 350, 375, 390, 450, 455, 456, 460, 464]


def scalar_fields(type_name):
    schema = json.loads((OUT / "graphql_schema.json").read_text(encoding="utf-8"))
    record_type = next(t for t in schema["types"] if t["name"] == type_name)
    return [f["name"] for f in record_type["fields"] if is_scalar(f["type"])]


def main():
    session = make_session(load_token())
    fields = " ".join(scalar_fields("RefContractStatus"))
    log(f"Поля справочника: {fields}\n")

    for status_id in STATUS_IDS:
        query = (f"{{ Contract(limit: 1, filter: {{refContractStatusId: {status_id}}}) "
                 f"{{ refContractStatusId RefContractStatus {{ {fields} }} }} }}")
        result = run(session, query)
        rows = result["data"].get("Contract") or []
        status = rows[0]["RefContractStatus"] if rows else None
        log(f"{status_id}: {json.dumps(status, ensure_ascii=False) if status else result['error'] or 'не найден'}")


if __name__ == "__main__":
    main()