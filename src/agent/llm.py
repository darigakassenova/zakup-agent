import json
import os
import sys
import time
from datetime import date

import requests

from agent import settings

SYSTEM = (
    "Ты маршрутизатор вопросов по государственным закупкам Республики Казахстан (вопросы на русском или казахском). "
    "Твоя единственная задача — вызвать ровно один подходящий инструмент с параметрами из текста вопроса. "
    "Не отвечай по существу и не придумывай значения: БИН, коды ЕНСТРУ, номера лотов и договоров бери только из вопроса. "
    "Если вопрос не относится к закупкам, вызови out_of_scope. "
    f"Сегодня {date.today():%Y-%m-%d}: «прошлый год», «былтыр», «в этом году» переводи в номер года."
)

YEAR = {"type": "integer", "description": "год, только если он назван в вопросе (числом или словами «прошлый год», «биыл»)"}
PARAMS = {
    "query": {"type": "string", "description": "название товара или услуги на русском языке (названия в базе русские); "
                                               "если вопрос на казахском — переведи название на русский"},
    "customer_bin": {"type": "string", "description": "БИН заказчика (12 цифр), только из вопроса"},
    "supplier_bin": {"type": "string", "description": "БИН поставщика (12 цифр), только из вопроса"},
    "enstru_code": {"type": "string", "description": "код ЕНСТРУ, только из вопроса"},
    "year_from": YEAR, "year_to": YEAR,
    "top_k": {"type": "integer", "description": "сколько примеров показать"},
}


def spec(name, description, extra=None, required=None, base=True):
    properties = {**(PARAMS if base else {}), **(extra or {})}
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required or []}}


TOOL_SPECS = [
    spec("search_contracts", "Поиск договоров по товару, организации, поставщику, ЕНСТРУ и периоду."),
    spec("spend_summary", "Суммы договоров: сколько потрачено, с группировкой.",
         {"group_by": {"type": "string", "enum": ["customer", "supplier", "enstru", "year", "month"]},
          "include_terminated": {"type": "boolean"}}),
    spec("compare_prices", "Сравнение цен на товар (ЕНСТРУ): средневзвешенная, медиана, разрез по заказчикам, городам, поставщикам, годам.",
         {"by": {"type": "string", "enum": ["customer", "supplier", "city", "year"]},
          "customer_bins": {"type": "array", "items": {"type": "string"}, "description": "БИНы заказчиков для сравнения"}}),
    spec("price_anomalies", "Закупки с отклонением цены от средневзвешенной по ЕНСТРУ больше порога (по умолчанию 30%).",
         {"threshold": {"type": "number", "description": "доля, например 0.3 для 30%, только если назван в вопросе"},
          "direction": {"type": "string", "enum": ["both", "up", "down"]}}),
    spec("fair_price_check", "Оценка справедливости цены конкретного лота или договора относительно аналогичных контрактов других заказчиков.",
         {"lot_number": {"type": "string", "description": "номер лота, например 13655264-ЗЦП1"},
          "contract_id": {"type": "integer"}, "top_k": PARAMS["top_k"]}, base=False),
    spec("volume_anomalies", "Нетипичное завышение количества товаров по сравнению с предыдущими годами.",
         {"customer_bin": PARAMS["customer_bin"], "enstru_code": PARAMS["enstru_code"], "query": PARAMS["query"],
          "year": YEAR, "min_ratio": {"type": "number"}, "top_k": PARAMS["top_k"]}, base=False),
    spec("out_of_scope", "Вопрос не про государственные закупки или не может быть решён по данным.", base=False),
]


class LLMUnavailable(RuntimeError):
    pass


NO_KEY = "нет ключа"


def post(url, headers, payload, attempts=3):
    for attempt in range(attempts):
        response = requests.post(url, headers=headers, json=payload, timeout=settings.LLM_TIMEOUT)
        if response.status_code == 429 and attempt < attempts - 1:
            time.sleep(min(float(response.headers.get("retry-after") or 20), 60))
            continue
        if response.status_code >= 400:
            raise LLMUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")
        return response.json()


def anthropic_call(question):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise LLMUnavailable(f"{NO_KEY} ANTHROPIC_API_KEY")
    body = post((settings.LLM_BASE_URL or "https://api.anthropic.com") + "/v1/messages",
                {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                {"model": settings.LLM_MODEL, "max_tokens": 400, "system": SYSTEM, "tools": TOOL_SPECS,
                 "tool_choice": {"type": "any"}, "messages": [{"role": "user", "content": question}]})
    for block in body.get("content", []):
        if block.get("type") == "tool_use":
            return block["name"], block.get("input") or {}
    raise LLMUnavailable("модель не выбрала инструмент")


def openai_call(question):
    if not settings.LLM_BASE_URL:
        raise LLMUnavailable(f"{NO_KEY}: не задан LLM_BASE_URL")
    tools = [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                                 "parameters": s["input_schema"]}} for s in TOOL_SPECS]
    payload = {"model": settings.LLM_MODEL, "tools": tools, "tool_choice": "required",
               "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]}
    if settings.LLM_REASONING_EFFORT:
        payload["reasoning_effort"] = settings.LLM_REASONING_EFFORT
    body = post(settings.LLM_BASE_URL.rstrip("/") + "/chat/completions",
                {"Authorization": f"Bearer {os.getenv('LLM_API_KEY', 'none')}"}, payload)
    calls = body["choices"][0]["message"].get("tool_calls") or []
    if not calls:
        raise LLMUnavailable("модель не выбрала инструмент")
    return calls[0]["function"]["name"], json.loads(calls[0]["function"].get("arguments") or "{}")


def responses_call(question):
    if not settings.LLM_BASE_URL:
        raise LLMUnavailable(f"{NO_KEY}: не задан LLM_BASE_URL")
    tools = [{"type": "function", "name": s["name"], "description": s["description"],
              "parameters": s["input_schema"]} for s in TOOL_SPECS]
    payload = {"model": settings.LLM_MODEL, "instructions": SYSTEM, "input": question,
               "tools": tools, "tool_choice": "required"}
    if settings.LLM_REASONING_EFFORT:
        payload["reasoning"] = {"effort": settings.LLM_REASONING_EFFORT}
    body = post(settings.LLM_BASE_URL.rstrip("/") + "/responses",
                {"Authorization": f"Bearer {os.getenv('LLM_API_KEY', 'none')}"}, payload)
    for item in body.get("output", []):
        if item.get("type") == "function_call":
            return item["name"], json.loads(item.get("arguments") or "{}")
    raise LLMUnavailable("модель не выбрала инструмент")


def choose_tool(question):
    # Если модель недоступна или не выбрала инструмент, агент переходит на правила
    call = {"openai": openai_call, "openai_responses": responses_call}.get(settings.LLM_PROVIDER, anthropic_call)
    try:
        return call(question)
    except LLMUnavailable as error:
        if not str(error).startswith(NO_KEY):
            print(f"[LLM: {error}; вопрос разобран правилами]", file=sys.stderr)
        raise
    except (requests.RequestException, KeyError, IndexError, ValueError) as error:
        print(f"[LLM недоступна: {error}; вопрос разобран правилами]", file=sys.stderr)
        raise LLMUnavailable(str(error)) from error