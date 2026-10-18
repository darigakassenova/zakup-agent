import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bins import bins  # noqa: E402

URL = "https://ows.goszakup.gov.kz/v3/graphql"
OUT = ROOT / "data" / "raw"

PAUSE = 0.3
TIMEOUT = 60
RETRIES = 4
SAMPLE_SIZE = 3
MAX_SAMPLE_FIELDS = 60

ENTITIES = {"trdbuy", "lots", "contract", "contractunits", "plans", "plnpoint", "subject", "rnulist"}
BIN_FIELD = re.compile(r"b(i)?in", re.IGNORECASE)
RETRY_STATUSES = {429, 500, 502, 503, 504}

INTROSPECTION = """
query {
  __schema {
    queryType { name }
    types {
      kind name
      fields(includeDeprecated: true) { name args { name type { ...T } } type { ...T } }
      inputFields { name type { ...T } }
    }
  }
}
fragment T on __Type {
  kind name ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
}
"""


def log(message=""):
    print(message, flush=True)


def load_token():
    load_dotenv(ROOT / ".env")
    token = (os.getenv("OWS_TOKEN") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        sys.exit("Токен не найден")
    return token


def make_session(token):
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    return session


def graphql(session, query, variables=None):
    payload = {"query": query, "variables": variables or {}}
    result = {"status": None, "body": None, "text": ""}
    for attempt in range(1, RETRIES + 1):
        try:
            response = session.post(URL, json=payload, timeout=TIMEOUT)
        except requests.RequestException as error:
            result["text"] = type(error).__name__
            time.sleep(1.5 * attempt)
            continue
        try:
            body = response.json()
        except ValueError:
            body = None
        result = {"status": response.status_code, "body": body, "text": response.text[:300]}
        if response.status_code not in RETRY_STATUSES:
            break
        time.sleep(2 * attempt)
    time.sleep(PAUSE)
    return result


def leaf(type_ref):
    while type_ref.get("ofType"):
        type_ref = type_ref["ofType"]
    return type_ref


def type_repr(type_ref):
    if type_ref["kind"] == "NON_NULL":
        return type_repr(type_ref["ofType"]) + "!"
    if type_ref["kind"] == "LIST":
        return f"[{type_repr(type_ref['ofType'])}]"
    return type_ref["name"]


def is_scalar(type_ref):
    return leaf(type_ref)["kind"] in ("SCALAR", "ENUM")


def save_json(name, payload):
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_schema(session):
    result = graphql(session, INTROSPECTION)
    schema = ((result["body"] or {}).get("data") or {}).get("__schema")
    if not schema:
        sys.exit(f"Не удалось получить схему. HTTP {result['status']}: {result['text']}")
    save_json("graphql_schema", schema)
    return schema


def find_entities(schema):
    types = {t["name"]: t for t in schema["types"]}
    query_type = types[schema["queryType"]["name"]]
    entities = []
    for field in query_type["fields"]:
        if field["name"].lower() not in ENTITIES:
            continue
        args = {arg["name"]: arg for arg in field["args"]}
        filter_arg = next((args[name] for name in ("filter", "filters") if name in args), None)
        if filter_arg is None:
            continue
        filter_type = types.get(leaf(filter_arg["type"])["name"], {})
        record_type = types.get(leaf(field["type"])["name"], {})
        scalars = [f["name"] for f in record_type.get("fields") or [] if is_scalar(f["type"])]
        entities.append({
            "name": field["name"],
            "record_type": record_type.get("name"),
            "filter_arg": filter_arg["name"],
            "filter_type": type_repr(filter_arg["type"]),
            "has_limit": "limit" in args,
            "bin_fields": [f["name"] for f in filter_type.get("inputFields") or [] if BIN_FIELD.search(f["name"])],
            "scalars": scalars,
        })
    return query_type, types, entities


def build_query(entity, selection, limit):
    limit_arg = f"limit: {limit}, " if entity["has_limit"] else ""
    return (
        f"query($f: {entity['filter_type']}) "
        f"{{ {entity['name']}({limit_arg}{entity['filter_arg']}: $f) {{ {selection} }} }}"
    )


def count_records(session, entity, bin_field, bin_):
    selection = "id" if "id" in entity["scalars"] else entity["scalars"][0]
    result = graphql(session, build_query(entity, selection, 1), {"f": {bin_field: bin_}})
    body = result["body"] or {}
    page_info = (body.get("extensions") or {}).get("pageInfo") or {}
    errors = body.get("errors") or []
    error = errors[0].get("message", "")[:120] if errors else ("" if result["status"] == 200 else result["text"][:120])
    return result["status"], page_info.get("totalCount"), error


def fetch_sample(session, entity, bin_field, bin_):
    selection = " ".join(entity["scalars"][:MAX_SAMPLE_FIELDS])
    result = graphql(session, build_query(entity, selection, SAMPLE_SIZE), {"f": {bin_field: bin_}})
    return result["body"]


def collect_counts(session, entities, selected_bins):
    rows = []
    for entity in entities:
        for bin_field in entity["bin_fields"]:
            column = f"{entity['name']}.{bin_field}"
            log(f"\n{column}")
            for bin_ in selected_bins:
                status, total, error = count_records(session, entity, bin_field, bin_)
                log(f"  {bin_}: HTTP {status}, записей: {total} {error}")
                rows.append({"column": column, "entity": entity["name"], "bin_field": bin_field,
                             "bin": bin_, "http": status, "total": total, "error": error})
    return rows


def save_samples(session, entities, rows):
    by_entity = {e["name"]: e for e in entities}
    saved = set()
    for row in rows:
        key = (row["entity"], row["bin_field"])
        if key in saved or not row["total"]:
            continue
        sample = fetch_sample(session, by_entity[row["entity"]], row["bin_field"], row["bin"])
        save_json(f"sample_{row['entity']}_{row['bin_field']}", sample)
        saved.add(key)


def write_counts_csv(rows):
    with open(OUT / "_counts.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_report(query_type, types, entities, rows, selected_bins):
    lines = ["Query"]
    for field in query_type["fields"]:
        args = ", ".join(f"{a['name']}: {type_repr(a['type'])}" for a in field["args"])
        lines.append(f"{field['name']}({args}) -> {type_repr(field['type'])}")

    lines.append("\n ПОЛЯ ОСНОВНЫХ СУЩНОСТЕЙ")
    for entity in entities:
        lines.append(f"\n{entity['name']} (поля фильтра по БИНу: {', '.join(entity['bin_fields']) or 'нет'})")
        for field in types.get(entity["record_type"], {}).get("fields") or []:
            lines.append(f"  {field['name']}: {type_repr(field['type'])}")

    lines.append("\n КОЛИЧЕСТВО ЗАПИСЕЙ ПО БИНАМ")
    columns = list(dict.fromkeys(row["column"] for row in rows))
    totals = {(row["bin"], row["column"]): row["total"] for row in rows}
    lines.append(" | ".join(["БИН"] + columns))
    for bin_ in selected_bins:
        lines.append(" | ".join([bin_] + [str(totals.get((bin_, column))) for column in columns]))

    failed = [row for row in rows if row["http"] != 200 or row["error"]]
    lines.append(f"\nЗапросов с ошибками: {len(failed)} из {len(rows)}")
    for row in failed[:10]:
        lines.append(f"  {row['column']} {row['bin']}: HTTP {row['http']} {row['error']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-bins", type=int, default=0, help="взять только первые N БИНов")
    args = parser.parse_args()

    selected_bins = bins[: args.limit_bins] if args.limit_bins else bins
    session = make_session(load_token())
    OUT.mkdir(parents=True, exist_ok=True)

    log("Загружаю схему данных")
    query_type, types, entities = find_entities(load_schema(session))
    log(f"Найдено сущностей с фильтром: {len(entities)}")

    rows = collect_counts(session, entities, selected_bins)
    if not rows:
        sys.exit("Не нашлось полей фильтрации по БИНу. Пришлите файл data/raw/graphql_schema.json")

    write_counts_csv(rows)
    save_samples(session, entities, rows)
    (OUT / "_report.txt").write_text(build_report(query_type, types, entities, rows, selected_bins), encoding="utf-8")
    log("\nГотово. Отчет: data/raw/_report.txt")


if __name__ == "__main__":
    main()