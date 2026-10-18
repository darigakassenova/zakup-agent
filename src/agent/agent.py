import json

from agent import llm, render, router
from agent.db import Context, connect
from agent.tools import TOOLS, known_words

ANCHORS = ("enstru_code", "customer_bin", "customer_bins", "supplier_bin", "lot_number", "contract_id")


def pick_tool(question, use_llm):
    # Выбор инструмента: LLM or rules
    if use_llm:
        try:
            tool, args = llm.choose_tool(question)
            if tool == "out_of_scope":
                return None, {}, "llm"
            if tool in TOOLS:
                return tool, router.sanitize(question, router.entities(question), tool, args), "llm"
        except llm.LLMUnavailable:
            pass
    tool, args = router.route_by_rules(question)
    return tool, (router.clean_args(tool, args) if tool else {}), "rules"


def reproduce_line(tool, args):
    shown = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    return f"{tool}({shown})"


def drop_unknown_words(ctx, args):
    words = args.get("query")
    if not words:
        return None
    kept = known_words(ctx, words)
    if kept:
        args["query"] = kept
        return None
    args.pop("query")
    has_anchor = any(args.get(key) for key in ANCHORS)
    return None if has_anchor else words


def reply(text, tool=None, args=None, source=None, result=None):
    return {"text": text, "tool": tool, "args": args or {}, "source": source, "result": result}


def answer(question, conn=None, use_llm=True):
    lang = render.detect_language(question)
    own_conn = conn is None
    conn = conn or connect()
    try:
        tool, args, source = pick_tool(question, use_llm)
        if tool is None:
            key = "out_of_scope" if source == "llm" else "not_understood"
            return reply(render.render_plain(lang, key), source=source)

        ctx = Context(conn, lang)
        unknown = drop_unknown_words(ctx, args)
        if unknown:
            return reply(render.render_plain(lang, "no_match").format(words=unknown), tool, args, source)

        result = TOOLS[tool](ctx, **args)
        text = render.render(result, lang) + f"\n\n`{reproduce_line(tool, args)}`"
        return reply(text, tool, args, source, result)
    finally:
        if own_conn:
            conn.close()
