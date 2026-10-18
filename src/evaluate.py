import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from psycopg.rows import dict_row

from agent.agent import answer
from agent.db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval"
YEAR = 2025
VALID = ("active", "transferred")

def one(cur, sql, params=None):
    cur.execute(sql, params or {})
    row = cur.fetchone()
    return row and list(row.values())[0]


def column(cur, sql, params=None):
    cur.execute(sql, params or {})
    return [list(r.values())[0] for r in cur.fetchall()]


def pick_entities(cur):
    customers = column(cur, """
        SELECT c.customer_bin FROM mart.contract_current c JOIN subject s ON s.bin = c.customer_bin AND s.is_target
        WHERE extract(year FROM c.sign_date) = %(y)s GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 3""", {"y": YEAR})
    top_supplier = """SELECT supplier_bin FROM mart.contract_current
                      WHERE customer_bin = %(b)s AND extract(year FROM sign_date) = %(y)s AND supplier_bin IS NOT NULL
                      GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"""
    codes = column(cur, """SELECT enstru_code FROM mart.price_fact WHERE is_benchmark
                           GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 3""")
    code_buyers = column(cur, """SELECT customer_bin FROM mart.price_fact WHERE is_benchmark AND enstru_code = %(c)s
                                 GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 2""", {"c": codes[0]})
    lot_units = """
        SELECT l.lot_number, count(*) AS n,
               count(*) FILTER (WHERE f.flag = 'high') AS high,
               bool_and(f.flag = %(flag)s AND f.confidence IN ('high', 'medium')) AS all_flag
        FROM contract_unit cu
        JOIN mart.contract_current c ON c.id = cu.contract_id
        JOIN lot l ON l.id = cu.lot_id
        LEFT JOIN mart.fair_price f ON f.unit_id = cu.id
        WHERE NOT coalesce(cu.deleted, false) AND NOT coalesce(cu.cr_deleted, false)
          AND l.lot_number ~ '^[0-9]{5,}(-[^0-9-]{2,}[0-9]*)+$'
        GROUP BY l.lot_number"""
    lot_multi = one(cur, f"""SELECT lot_number FROM ({lot_units}) x WHERE n BETWEEN 3 AND 10 AND high >= 1
                             ORDER BY n DESC, lot_number LIMIT 1""", {"flag": "high"})
    lot_high = one(cur, f"SELECT lot_number FROM ({lot_units}) x WHERE n = 1 AND all_flag ORDER BY lot_number LIMIT 1",
                   {"flag": "high"}) or one(cur, f"SELECT lot_number FROM ({lot_units}) x WHERE n = 1 AND high = 1 "
                                                 f"ORDER BY lot_number LIMIT 1", {"flag": "high"})
    lot_normal = one(cur, f"SELECT lot_number FROM ({lot_units}) x WHERE n = 1 AND all_flag ORDER BY lot_number LIMIT 1",
                     {"flag": "normal"})
    contract = one(cur, """
        SELECT cu.contract_id FROM contract_unit cu
        JOIN mart.contract_current c ON c.id = cu.contract_id
        LEFT JOIN mart.fair_price f ON f.unit_id = cu.id
        WHERE NOT coalesce(cu.deleted, false) AND NOT coalesce(cu.cr_deleted, false)
        GROUP BY 1 HAVING count(*) BETWEEN 2 AND 5 AND count(f.unit_id) = count(*)
                    AND count(*) FILTER (WHERE f.flag = 'high') >= 1
        ORDER BY 1 LIMIT 1""")
    volume = """SELECT customer_bin FROM mart.volume_anomaly WHERE year = %(y)s AND flag <> 'no_history'
                GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 2"""
    v2026, v2025 = column(cur, volume, {"y": 2026}), column(cur, volume, {"y": 2025})
    return {
        "c1": customers[0], "c2": customers[1], "c3": customers[2],
        "s1": one(cur, top_supplier, {"b": customers[0], "y": YEAR}),
        "s2": one(cur, top_supplier, {"b": customers[1], "y": YEAR}),
        "e1": codes[0], "e2": codes[1], "e3": codes[2],
        "e1_buyer": code_buyers[0], "e1_buyer2": code_buyers[1],
        "lot_multi": lot_multi, "lot_high": lot_high, "lot_normal": lot_normal, "contract": contract,
        "v1": v2026[0], "v2": v2026[1], "v3": v2025[0],
    }


def build_questions(e):
    y = YEAR
    q = [
        # поиск договоров
        ("ru", "search_contracts", f"Покажи договоры заказчика {e['c1']} за {y} год", "search_customer", {"bin": e["c1"], "year": y}),
        ("kk", "search_contracts", f"{e['c2']} тапсырыс берушінің {y} жылғы шарттарын көрсет", "search_customer", {"bin": e["c2"], "year": y}),
        ("ru", "search_contracts", f"Покажи договоры поставщика {e['s1']}", "search_supplier", {"bin": e["s1"]}),
        ("kk", "search_contracts", f"{e['s2']} жеткізушінің шарттарын көрсет", "search_supplier", {"bin": e["s2"]}),
        ("ru", "search_contracts", f"Найди договоры по ЕНСТРУ {e['e2']}", "search_enstru", {"code": e["e2"]}),
        # суммы
        ("ru", "spend_summary", f"Сколько потратил заказчик {e['c1']} в {y} году?", "spend_customer", {"bin": e["c1"], "year": y}),
        ("ru", "spend_summary", f"Сколько потратил заказчик {e['c2']} в {y} году по поставщикам?", "spend_top_supplier", {"bin": e["c2"], "year": y}),
        ("kk", "spend_summary", f"{e['c3']} тапсырыс беруші {y} жылы қанша жұмсады?", "spend_customer", {"bin": e["c3"], "year": y}),
        ("kk", "spend_summary", f"{e['c1']} тапсырыс беруші {y} жылы жеткізушілер бойынша қанша жұмсады?", "spend_top_supplier", {"bin": e["c1"], "year": y}),
        ("ru", "spend_summary", f"Сколько всего потратил заказчик {e['c3']} по годам?", "spend_customer", {"bin": e["c3"], "year": None}),
        ("ru", "spend_summary", f"Какая сумма закупок по ЕНСТРУ {e['e1']} у заказчика {e['e1_buyer']}?", "spend_enstru", {"bin": e["e1_buyer"], "code": e["e1"]}),
        # сравнение цен
        ("ru", "compare_prices", f"Сравни цены на ЕНСТРУ {e['e1']} по заказчикам", "compare", {"code": e["e1"], "bins": None}),
        ("ru", "compare_prices", f"Сравни цены на ЕНСТРУ {e['e2']} по городам", "compare", {"code": e["e2"], "bins": None}),
        ("kk", "compare_prices", f"ЕНСТРУ {e['e3']} бағаларын салыстыр", "compare", {"code": e["e3"], "bins": None}),
        ("ru", "compare_prices", f"Сравни цены на ЕНСТРУ {e['e1']} у заказчиков {e['e1_buyer']} и {e['e1_buyer2']}", "compare",
         {"code": e["e1"], "bins": [e["e1_buyer"], e["e1_buyer2"]]}),
        # отклонения цен (класс «аномалии» из ТЗ)
        ("ru", "price_anomalies", f"Найди ценовые аномалии больше 30% по ЕНСТРУ {e['e1']}", "anomalies", {"code": e["e1"], "threshold": 0.3}),
        ("kk", "price_anomalies", f"ЕНСТРУ {e['e2']} бойынша бағасы 30%-дан артық ауытқыған сатып алуларды тап", "anomalies", {"code": e["e2"], "threshold": 0.3}),
        ("ru", "price_anomalies", f"Найди закупки с отклонением цены более 50% по ЕНСТРУ {e['e3']}", "anomalies", {"code": e["e3"], "threshold": 0.5}),
        ("ru", "price_anomalies", f"Покажи завышенные цены по ЕНСТРУ {e['e2']}", "anomalies", {"code": e["e2"], "threshold": 0.3, "up": True}),
        ("ru", "price_anomalies", f"Найди ценовые аномалии больше 30% у заказчика {e['c1']}", "anomalies", {"bin": e["c1"], "threshold": 0.3}),
        # справедливость цены (класс «справедливость» из ТЗ)
        ("ru", "fair_price_check", f"Оцени справедливость цены лота {e['lot_multi']}", "fair_lot", {"lot": e["lot_multi"]}),
        ("kk", "fair_price_check", f"{e['lot_high']} лотының бағасы әділ ме?", "fair_lot", {"lot": e["lot_high"]}),
        ("ru", "fair_price_check", f"Цена лота {e['lot_normal']} адекватна?", "fair_lot", {"lot": e["lot_normal"]}),
        ("ru", "fair_price_check", f"Оцени справедливость цены договора {e['contract']}", "fair_contract", {"contract": e["contract"]}),
        # объёмы (класс «объёмы» из ТЗ)
        ("ru", "volume_anomalies", f"Есть ли необычный рост объемов у заказчика {e['v1']} в 2026 году?", "volume", {"bin": e["v1"], "year": 2026}),
        ("kk", "volume_anomalies", f"{e['v2']} тапсырыс берушіде 2026 жылы көлемнің әдеттен тыс өсуі бар ма?", "volume", {"bin": e["v2"], "year": 2026}),
        ("ru", "volume_anomalies", f"Нетипичное увеличение количества у заказчика {e['v3']} в 2025 году", "volume", {"bin": e["v3"], "year": 2025}),
        # вопросы не по теме: агент должен отказаться
        ("ru", None, "Какая погода завтра в Астане?", "refusal", {}),
        ("kk", None, "Ертең Астанада ауа райы қандай?", "refusal", {}),
        ("ru", None, "Сколько будет 2+2?", "refusal", {}),
    ]
    return numbered(q, "q")


def build_free_questions(e):
    y = YEAR
    q = [
        ("ru", "spend_summary", f"Кому организация {e['c1']} заплатила больше всего в {y} году?", "spend_top_supplier", {"bin": e["c1"], "year": y}),
        ("ru", "spend_summary", f"Сколько денег ушло у {e['c2']} за прошлый год?", "spend_customer", {"bin": e["c2"], "year": y}),
        ("ru", "search_contracts", f"{e['c1']} — что закупали в {y}-м?", "search_customer", {"bin": e["c1"], "year": y}),
        ("ru", "fair_price_check", f"Не переплатили ли по лоту {e['lot_high']}?", "fair_lot", {"lot": e["lot_high"]}),
        ("ru", "compare_prices", f"Где дешевле всего покупают товары с кодом {e['e2']}?", "compare", {"code": e["e2"], "bins": None}),
        ("kk", "spend_summary", f"{e['c3']} ұйымы былтыр қанша ақша жұмсады?", "spend_customer", {"bin": e["c3"], "year": y}),
        ("ru", "price_anomalies", f"Есть ли подозрительно дорогие закупки по коду {e['e1']}?", "anomalies", {"code": e["e1"], "threshold": 0.3, "up": True}),
        ("ru", "volume_anomalies", f"Покупает ли {e['v1']} в 2026 году намного больше, чем раньше?", "volume", {"bin": e["v1"], "year": 2026}),
        ("kk", "fair_price_check", f"{e['lot_multi']} лоты бойынша артық төленген жоқ па?", "fair_lot", {"lot": e["lot_multi"]}),
        ("ru", "spend_summary", f"сколко патратил {e['c1']} в {y} году", "spend_customer", {"bin": e["c1"], "year": y}),
    ]
    return numbered(q, "f")


def numbered(q, prefix):
    missing = [text for _, _, text, _, params in q if any(v is None for k, v in params.items() if k not in ("year", "bins"))]
    for text in missing:
        print(f"Пропущен вопрос (в базе не нашлось подходящего объекта): {text}")
    q = [item for item in q if item[2] not in missing]
    return [{"id": f"{prefix}{i:02d}", "lang": lang, "tool": tool, "question": text, "gold": gold, "params": params}
            for i, (lang, tool, text, gold, params) in enumerate(q, start=1)]

UNITS = """FROM contract_unit cu
           JOIN mart.contract_current c ON c.id = cu.contract_id
           WHERE NOT coalesce(cu.deleted, false) AND NOT coalesce(cu.cr_deleted, false)"""


def year_filter(year, column="c.sign_date"):
    return f" AND extract(year FROM {column}) = {int(year)}" if year else ""


def gold_search_customer(cur, bin, year):
    n = one(cur, f"SELECT count(DISTINCT c.id) {UNITS} AND c.customer_bin = %(b)s" + year_filter(year), {"b": bin})
    return [("найдено договоров", n, "int"), ("БИН", bin, "text"), ("год", year, "text")]


def gold_search_supplier(cur, bin):
    n = one(cur, f"SELECT count(DISTINCT c.id) {UNITS} AND c.supplier_bin = %(b)s", {"b": bin})
    return [("найдено договоров", n, "int"), ("БИН поставщика", bin, "text")]


def gold_search_enstru(cur, code):
    n = one(cur, f"SELECT count(DISTINCT c.id) {UNITS} AND cu.enstru_code = %(c)s", {"c": code})
    return [("найдено договоров", n, "int"), ("ЕНСТРУ", code, "text")]


def gold_spend_customer(cur, bin, year):
    cur.execute("SELECT count(*) AS n, sum(sum) AS amount FROM mart.contract_current c "
                "WHERE c.customer_bin = %(b)s AND c.status_group = ANY(%(s)s)" + year_filter(year), {"b": bin, "s": list(VALID)})
    row = cur.fetchone()
    facts = [("сумма", float(row["amount"] or 0), "money"), ("договоров", row["n"], "int"), ("БИН", bin, "text")]
    return facts + ([("год", year, "text")] if year else [])


def gold_spend_top_supplier(cur, bin, year):
    facts = gold_spend_customer(cur, bin, year)
    cur.execute("SELECT supplier_bin, sum(sum) AS amount FROM mart.contract_current c "
                "WHERE c.customer_bin = %(b)s AND c.status_group = ANY(%(s)s)" + year_filter(year) +
                " GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 1", {"b": bin, "s": list(VALID)})
    top = cur.fetchone()
    return facts + [("крупнейший поставщик", top["supplier_bin"], "text"), ("его сумма", float(top["amount"] or 0), "money")]


def gold_spend_enstru(cur, bin, code):
    cur.execute(f"""SELECT count(DISTINCT c.id) AS n,
                           sum(coalesce(cu.total_sum, coalesce(nullif(cu.item_price_wnds, 0), cu.item_price) * cu.quantity)) AS amount
                    {UNITS} AND c.customer_bin = %(b)s AND cu.enstru_code = %(c)s AND c.status_group = ANY(%(s)s)""",
                {"b": bin, "c": code, "s": list(VALID)})
    row = cur.fetchone()
    return [("сумма", float(row["amount"] or 0), "money"), ("договоров", row["n"], "int"), ("БИН", bin, "text")]


def gold_compare(cur, code, bins):
    # Сравнение идёт внутри самой частой базовой единицы этого кода (цены разных единиц несопоставимы).
    extra = " AND customer_bin = ANY(%(bins)s)" if bins else ""
    params = {"c": code, "bins": bins}
    unit = one(cur, f"""SELECT base_unit FROM mart.price_fact WHERE is_benchmark AND enstru_code = %(c)s{extra}
                        GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""", params)
    cur.execute(f"""SELECT count(*) AS n, sum(price_per_base * qty_base) / sum(qty_base) AS wavg,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_base) AS median
                    FROM mart.price_fact WHERE is_benchmark AND enstru_code = %(c)s AND base_unit = %(u)s{extra}""",
                {**params, "u": unit})
    row = cur.fetchone()
    return [("позиций", row["n"], "int"), ("медиана", float(row["median"]), "money"),
            ("средневзвешенная", float(row["wavg"]), "money"), ("ЕНСТРУ", code, "text")]


def gold_anomalies(cur, threshold, code=None, bin=None, up=False):
    conditions, params = ["d.n >= 5"], {"t": threshold}
    if code:
        conditions.append("d.enstru_code = %(c)s")
        params["c"] = code
    if bin:
        conditions.append("d.customer_bin = %(b)s")
        params["b"] = bin
    deviates = "d.deviation > %(t)s" if up else "abs(d.deviation) > %(t)s"
    cur.execute(f"""SELECT count(*) AS analyzed,
                           count(*) FILTER (WHERE {deviates}) AS matches,
                           count(*) FILTER (WHERE {deviates} AND f.flag IN ('high', 'low')) AS confirmed
                    FROM mart.price_deviation d LEFT JOIN mart.fair_price f ON f.unit_id = d.unit_id
                    WHERE {' AND '.join(conditions)}""", params)
    row = cur.fetchone()
    return [("проанализировано", row["analyzed"], "int"), ("с отклонением", row["matches"], "int"),
            ("подтверждено справедливой ценой", row["confirmed"], "int")]


def fair_facts(cur, where, params):
    cur.execute(f"""SELECT cu.id, f.flag, f.price_per_base, f.fair_price
                    FROM contract_unit cu
                    JOIN mart.contract_current c ON c.id = cu.contract_id
                    LEFT JOIN mart.fair_price f ON f.unit_id = cu.id
                    WHERE NOT coalesce(cu.deleted, false) AND NOT coalesce(cu.cr_deleted, false) AND {where}""", params)
    rows = cur.fetchall()
    if len(rows) == 1 and rows[0]["fair_price"] is not None:
        return [("цена", float(rows[0]["price_per_base"]), "money"), ("справедливая цена", float(rows[0]["fair_price"]), "money")]
    count = lambda flag: sum(1 for r in rows if r["flag"] == flag)
    return [("позиций", len(rows), "int"), ("завышены", count("high"), "int"), ("в норме", count("normal"), "int")]


def gold_fair_lot(cur, lot):
    return fair_facts(cur, "cu.lot_id IN (SELECT id FROM lot WHERE lot_number = %(l)s)", {"l": lot}) + [("лот", lot, "text")]


def gold_fair_contract(cur, contract):
    return fair_facts(cur, "cu.contract_id = %(c)s", {"c": contract}) + [("договор", contract, "text")]


def gold_volume(cur, bin, year):
    cur.execute("""SELECT count(*) FILTER (WHERE flag <> 'no_history') AS compared,
                          count(*) FILTER (WHERE ratio >= 1.5) AS matches,
                          count(*) FILTER (WHERE flag = 'high') AS strong
                   FROM mart.volume_anomaly WHERE customer_bin = %(b)s AND year = %(y)s""", {"b": bin, "y": year})
    row = cur.fetchone()
    return [("проанализировано", row["compared"], "int"), ("рост ≥ ×1,5", row["matches"], "int"),
            ("статистически значимо", row["strong"], "int"), ("БИН", bin, "text"), ("год", year, "text")]


def gold_refusal(cur):
    return [("отказ от ответа", True, "refusal")]


GOLD = {name[5:]: fn for name, fn in globals().items() if name.startswith("gold_")}


NOTHING = r"не найдено|табылмады|жоқ"
NUMBER = re.compile(r"\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|\d+(?:,\d+)?")


def numbers_in(text):
    return [float(m.replace(" ", "").replace(" ", "").replace(",", ".")) for m in NUMBER.findall(text)]


def fact_ok(value, kind, text, reply):
    if kind == "refusal":
        return reply["tool"] is None
    if value is None:
        return True
    if kind == "text":
        return str(value).lower() in text.lower()
    verdict = text.split("\n\n")[0]
    found = numbers_in(verdict)
    if kind == "int":
        return any(n == value for n in found) or (value == 0 and re.search(NOTHING, verdict, re.IGNORECASE) is not None)
    tolerance = 1.0 if abs(value) >= 1000 else 0.011 
    return any(abs(n - value) <= tolerance for n in found)


def evaluate(questions, use_llm):
    rows = []
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        for q in questions:
            facts = GOLD[q["gold"]](cur, **q["params"])
            reply = answer(q["question"], conn, use_llm=use_llm)
            route_ok = reply["tool"] == q["tool"]
            checked = [{"fact": name, "expected": value, "ok": route_ok and fact_ok(value, kind, reply["text"], reply)}
                       for name, value, kind in facts]
            rows.append({**q, "got_tool": reply["tool"], "args": reply["args"], "route_ok": route_ok, "source": reply["source"],
                         "facts": checked, "answer": reply["text"]})
    return rows


def summary(rows):
    facts = [f for r in rows for f in r["facts"]]
    correct = sum(f["ok"] for f in facts)
    by_kind = {}
    for r in rows:
        kind = by_kind.setdefault(r["tool"] or "refusal", [0, 0])
        kind[0] += sum(f["ok"] for f in r["facts"])
        kind[1] += len(r["facts"])
    return {"questions": len(rows), "route_ok": sum(r["route_ok"] for r in rows),
            "by_llm": sum(r.get("source") == "llm" for r in rows),
            "facts_ok": correct, "facts": len(facts), "accuracy": correct / len(facts) if facts else 0,
            "fully_correct": sum(all(f["ok"] for f in r["facts"]) and r["route_ok"] for r in rows), "by_kind": by_kind}


SETS = {
    "typical": ("Типовые формулировки", "questions.json", build_questions),
    "free": ("Свободные формулировки", "questions_free.json", build_free_questions),
}


def report_section(title, rows, total, required):
    lines = [f"## {title}", "",
             f"- Точность фактов (числа, даты, BIN): **{total['facts_ok']}/{total['facts']} = {total['accuracy']:.1%}**"
             + (" (требование ТЗ: ≥85%)" if required else ""),
             f"- Верный инструмент: {total['route_ok']}/{total['questions']}",
             f"- Вопросов без единой ошибки: {total['fully_correct']}/{total['questions']}",
             f"- Разобрано языковой моделью: {total['by_llm']}/{total['questions']} (остальные — правилами)", "",
             "| Тип вопроса | Факты верно |", "|---|---|"]
    lines += [f"| {kind} | {ok}/{n} |" for kind, (ok, n) in total["by_kind"].items()]
    lines += ["", "| № | Язык | Вопрос | Инструмент | Факты | Ошибки |", "|---|---|---|---|---|---|"]
    for r in rows:
        wrong = "; ".join(f"{f['fact']} = {f['expected']}" for f in r["facts"] if not f["ok"])
        tool = r["got_tool"] or "отказ"
        tool += "" if r["route_ok"] else f" (ожидался {r['tool'] or 'отказ'})"
        lines.append(f"| {r['id']} | {r['lang']} | {r['question']} | {tool} | "
                     f"{sum(f['ok'] for f in r['facts'])}/{len(r['facts'])} | {wrong or '—'} |")
    return lines + [""]


def write_report(results, use_llm):
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({k: rows for k, (rows, _) in results.items()},
                                                 ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = ["# Замер точности агента", "",
             f"Дата: {datetime.now():%Y-%m-%d %H:%M}; режим: {'языковая модель' if use_llm else 'правила (без языковой модели)'}", ""]
    for key, (rows, total) in results.items():
        lines += report_section(SETS[key][0], rows, total, required=key == "typical")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def load_questions(key, rebuild, entities):
    path = OUT / SETS[key][1]
    if rebuild or not path.exists():
        OUT.mkdir(exist_ok=True)
        path.write_text(json.dumps(SETS[key][2](entities()), ensure_ascii=False, indent=1), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def print_rows(title, rows, total):
    print(f"\n=== {title} ===")
    for r in rows:
        mark = "✓" if r["route_ok"] and all(f["ok"] for f in r["facts"]) else "✗"
        print(f"{mark} {r['id']} [{r['lang']}] {r['question'][:70]}")
        if not r["route_ok"]:
            print(f"     инструмент: {r['got_tool']}, ожидался {r['tool']}")
        else:
            for f in r["facts"]:
                if not f["ok"]:
                    print(f"     нет в ответе: {f['fact']} = {f['expected']}")
    print(f"Точность фактов: {total['facts_ok']}/{total['facts']} = {total['accuracy']:.1%}; "
          f"верный инструмент: {total['route_ok']}/{total['questions']}; без ошибок: {total['fully_correct']}/{total['questions']}; "
          f"разобрано моделью: {total['by_llm']}/{total['questions']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", action="store_true", help="разбирать вопросы языковой моделью")
    parser.add_argument("--rebuild", action="store_true", help="заново выбрать объекты для вопросов")
    args = parser.parse_args()

    cache = {}

    def entities():
        if "e" not in cache:
            with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
                cache["e"] = pick_entities(cur)
        return cache["e"]

    results = {}
    for key, (title, _, _) in SETS.items():
        rows = evaluate(load_questions(key, args.rebuild, entities), args.llm)
        results[key] = (rows, summary(rows))
        print_rows(title, *results[key])
    write_report(results, args.llm)
    print(f"\nТребование ТЗ (≥85%) проверяется на типовых формулировках. Отчёт: {OUT / 'report.md'}")


if __name__ == "__main__":
    main()