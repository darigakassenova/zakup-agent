import inspect
import re
from datetime import date

from agent.tools import TOOLS

BIN = re.compile(r"(?<!\d)\d{12}(?!\d)")
ENSTRU = re.compile(r"(?<!\d)\d{4,6}(?:\.\d{1,6}){1,3}(?!\d)")

LOT = re.compile(r"(?<!\d)\d{5,}(?:-[^\W\d_]{2,}\d*)+", re.UNICODE)

CONTRACT = re.compile(r"(?:договор\w*|шарт\w*|contract)\D{0,20}?(\d{5,11})(?![\d-])", re.IGNORECASE)
YEAR = re.compile(r"(?<!\d)(20[2-3]\d)(?!\d)")
PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

RELATIVE_YEARS = (
    (r"позапрошл\w*\s+год|алдыңғы\s+жылы", -2),
    (r"прошл\w*\s+год|былтыр|өткен\s+жыл", -1),
    (r"(?:этот|этом|этого|текущ\w*|нынешн\w*)\s+год|биыл|осы\s+жыл|ағымдағы\s+жыл", 0),
)
TOP = re.compile(r"(?:топ|top)[\s-]*(\d{1,2})", re.IGNORECASE)

STOP_WORDS = {"тру", "енс", "цен", "цена", "цены", "цене", "ценам", "ценах", "ценой", "ценами", "мне", "нам",
              "бар", "алу", "тап", "тыс", "жоқ", "мен"}
COMMAND_PREFIXES = (
    "найд", "найт", "покаж", "показ", "поиск", "договор", "закуп", "лот", "сколько", "потрат", "сумм", "ценов", "отклонен",
    "аномал", "сравн", "оцен", "адекват", "справедлив", "объем", "объём", "количеств", "завыш", "занижен", "нетипич",
    "выяв", "предыдущ", "прошл", "год", "средневзвеш", "относительно", "аналогичн", "други", "ведомств", "город",
    "организац", "заказчик", "поставщик", "енстру", "есть", "были", "было", "какие", "какой", "каждо",
    "іздеу", "табыл", "көрсет", "шарт", "сатып", "баға", "ауыт", "салыстыр", "көлем", "жыл", "қанша", "сома", "тапсырыс",
    "жеткізуш", "әділ", "мөлшер", "бойынша", "арасында", "барлық", "және",
    "больш", "меньш", "выше", "ниже", "более", "менее", "свыш", "процент", "нужн", "пожалуйста",
    "дай", "выведи", "определи", "проверь", "также", "всех", "всем", "между", "кто", "где", "когда", "какая",
    "артық", "кем", "көп", "пайыз", "анықта", "тексер", "айт",
    "беруш", "жұмса", "әдет", "алдың", "қалай", "өст", "өск", "келісім", "ұйым", "берілген", "қандай", "тауып",
    "негізд", "дұрыс", "сәйкес", "кезең", "соңғы", "бойын",
    "прошл", "позапрошл", "этом", "этот", "этого", "текущ", "нынешн", "былтыр", "биыл", "өткен", "ағымдағы",
    "денег", "деньг", "ақша", "ушл",
    "потрач", "расход", "месяц", "топ", "top", "всего", "итого", "среди",
    "рост", "прирост", "увелич", "необыч", "резк", "аномальн", "подозрит", "өсім", "өсу", "артт",
)

KEYWORDS = {
    "fair": r"справедлив|адекватн|fair|әділ|баға\w*\s+(?:дұрыс|сәйкес)|обоснован",
    "volume": r"объ[её]м|количеств|көлем|мөлшер|нетипич|әдеттен тыс",
    "anomaly": r"отклонен|аномал|завышен|занижен|выброс|ауытқу|ауытқ|қымбат",
    "compare": r"сравн|салыстыр|разниц|медиан|средн\w*\s+цен|орташа",
    "spend": r"сколько|сумм|потрат|итого|расход|бюджет|қанша|сома|жалпы|жұмсал",
    "search": r"найд|найти|покаж|показать|поиск|іздеу|табыл|көрсет|договор|шарт",
}


PROCUREMENT = (r"договор|контракт|закуп|лот|тендер|поставщик|заказчик|организац|енстру|цен|стоимост|потрат|расход|сумм|бюджет|"
               r"объ[её]м|количеств|аномал|отклонен|найд|найти|покаж|поиск|"
               r"шарт|сатып|баға|жеткізуш|тапсырыс|сома|көлем|мөлшер|ауытқ|іздеу|табыл|көрсет")


def match(kind, text):
    return re.search(KEYWORDS[kind], text, re.IGNORECASE) is not None


def entities(question):
    found = {}
    bins = BIN.findall(question)
    if bins:
        found["bins"] = list(dict.fromkeys(bins))
    code = ENSTRU.findall(question)
    if code:
        found["enstru_code"] = code[0]
    lot = LOT.findall(question)
    if lot:
        found["lot_number"] = lot[0]
    contract = CONTRACT.findall(question)
    if contract:
        found["contract_id"] = int(contract[0])
    years = sorted({int(y) for y in YEAR.findall(question)}) or relative_year(question)
    if years:
        found["year_from"], found["year_to"] = years[0], years[-1]
    percent = PERCENT.findall(question)
    if percent:
        found["threshold"] = float(percent[0].replace(",", ".")) / 100
    top = TOP.findall(question)
    if top:
        found["top_k"] = int(top[0])
    return found


def relative_year(question):
    for pattern, shift in RELATIVE_YEARS:
        if re.search(pattern, question, re.IGNORECASE):
            return [date.today().year + shift]
    return []


def keywords(question):
    cleaned = ENSTRU.sub(" ", LOT.sub(" ", BIN.sub(" ", PERCENT.sub(" ", question))))
    words = []
    for word in re.findall(r"[\w-]+", cleaned.lower()):
        if len(word) < 3 or word.isdigit() or word in STOP_WORDS or word.startswith(COMMAND_PREFIXES):
            continue
        words.append(word)
    return " ".join(words) or None


def group_for(question, has_customer):
    lowered = question.lower()
    for pattern, group in ((r"по\s+заказчик|тапсырыс берушілер бойынша", "customer"), (r"по\s+поставщик|жеткізуші\w* бойынша", "supplier"),
                           (r"по\s+месяц|айлар бойынша", "month"), (r"по\s+года|по\s+годам|жылдар бойынша", "year"),
                           (r"по\s+енстру|по\s+товар|тауар\w* бойынша", "enstru")):
        if re.search(pattern, lowered):
            return group
    return "year" if has_customer else "customer"


def segment_for(question):
    lowered = question.lower()
    for pattern, segment in ((r"по\s+город|қала\w* бойынша|по\s+региона", "city"), (r"по\s+поставщик|жеткізуші\w* бойынша", "supplier"),
                             (r"по\s+года|по\s+годам|жылдар бойынша", "year")):
        if re.search(pattern, lowered):
            return segment
    return "customer"


def supplier_bins(question, bins):
    """БИН поставщика: слово «поставщик» перед БИН (русский порядок) или «жеткізуші» сразу после него (казахский порядок)."""
    return [b for b in bins
            if re.search(r"(?:поставщик\w*|жеткізуші\w*|supplier)\D{0,30}" + b, question, re.IGNORECASE)
            or re.search(b + r"\s{0,3}(?:жеткізуші|поставщик)", question, re.IGNORECASE)]


def customer_args(found, question=""):
    bins = found.get("bins") or []
    suppliers = supplier_bins(question, bins)
    customers = [b for b in bins if b not in suppliers]
    args = {}
    if suppliers:
        args["supplier_bin"] = suppliers[0]
    if len(customers) == 1:
        args["customer_bin"] = customers[0]
    return args


def route_by_rules(question):
    found = entities(question)
    has_identifier = any(found.get(k) for k in ("bins", "enstru_code", "lot_number", "contract_id"))
    if not has_identifier and not re.search(PROCUREMENT, question, re.IGNORECASE):
        return None, {}
    common = {k: found[k] for k in ("year_from", "year_to", "top_k") if k in found}
    query, bins = keywords(question), found.get("bins") or []
    if found.get("enstru_code"):
        query = None
    if found.get("lot_number") or (match("fair", question) and found.get("contract_id")):
        return "fair_price_check", {k: found[k] for k in ("lot_number", "contract_id", "top_k") if k in found}
    if match("volume", question):
        args = {**customer_args(found, question), "query": query}
        if "year_to" in found:
            args["year"] = found["year_to"]
        if found.get("enstru_code"):
            args["enstru_code"] = found["enstru_code"]
        if "top_k" in found:
            args["top_k"] = found["top_k"]
        return "volume_anomalies", args

    if match("anomaly", question) or match("fair", question):
        args = {**common, **customer_args(found, question), "query": query, "enstru_code": found.get("enstru_code")}
        if "threshold" in found:
            args["threshold"] = found["threshold"]

        if re.search(r"завышен|выше(?!\s*\d)|дорож|қымбат", question, re.IGNORECASE) and not re.search(r"занижен|ниже", question, re.IGNORECASE):
            args["direction"] = "up"
        return "price_anomalies", args
    if match("compare", question):
        args = {**common, "query": query, "enstru_code": found.get("enstru_code"), "by": segment_for(question)}
        if len(bins) > 1:
            args["customer_bins"] = bins
        else:
            args.update(customer_args(found, question))
        return "compare_prices", args
    if match("spend", question):
        organisations = customer_args(found, question)
        args = {**common, **organisations, "query": query, "enstru_code": found.get("enstru_code"),
                "group_by": group_for(question, "customer_bin" in organisations)}
        return "spend_summary", args
    if match("search", question) or query or bins or found.get("enstru_code"):
        args = {**common, **customer_args(found, question), "query": query, "enstru_code": found.get("enstru_code")}
        return "search_contracts", args
    return None, {}


def clean_args(tool, args):
    allowed = inspect.signature(TOOLS[tool]).parameters
    return {k: v for k, v in args.items() if k in allowed and v not in (None, "", [])}


def sanitize(question, found, tool, args):
    safe = {k: v for k, v in args.items() if not (type(v) in (int, float) and v == 0)}
    text = question.lower()
    bins = found.get("bins") or []
    for key in ("customer_bin", "supplier_bin"):
        if key in safe and str(safe[key]) not in bins:
            safe.pop(key)
    for key in ("enstru_code", "lot_number"):
        if key in safe and str(safe[key]).lower() not in text:
            safe.pop(key)
    if "customer_bins" in safe:
        safe["customer_bins"] = [str(b) for b in safe["customer_bins"] if str(b) in bins]
    if "contract_id" in safe and str(safe["contract_id"]) not in question:
        safe.pop("contract_id")
    known_years = {found.get("year_from"), found.get("year_to")}  # из текста, в том числе «прошлый год»
    for key in ("year_from", "year_to", "year"):
        if key in safe and str(safe[key]) not in question and safe[key] not in known_years:
            safe.pop(key)
    if "threshold" in safe and "threshold" not in found:
        safe.pop("threshold")
    for key, value in {**customer_args(found, question), **{k: found[k] for k in ("lot_number", "contract_id", "enstru_code") if k in found}}.items():
        safe.setdefault(key, value)
    if safe.get("customer_bin") and safe.get("customer_bin") == safe.get("supplier_bin"):
        safe.pop("customer_bin")
    return clean_args(tool, safe)