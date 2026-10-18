import re

from agent.texts import TEXT


def num(value, digits=2):
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text.rstrip("0").rstrip(",") if "," in text else text


def money(value):
    return num(value, 0 if abs(value or 0) >= 1000 else 2)


def pct(value):
    return f"{value * 100:+.1f}".replace(".", ",")


def ratio_text(value):
    return num(value, 1)


def t(lang, key, **values):
    template = TEXT[lang][key]
    return template.format(**values) if values else template


KAZAKH_LETTERS = set("әіңғүұқөһӘІҢҒҮҰҚӨҺ")
# Частые казахские слова без особых букв («сатып алу», «шарт», «бойынша», «жыл»).
KAZAKH_WORDS = ("сатып", "алу", "шарт", "бойынша", "жыл", "тапсырыс", "тауар", "бар", "ма", "ме", "бе", "па", "тап", "және")


def is_kazakh(word):
    lowered = word.lower()
    return bool(KAZAKH_LETTERS & set(word)) or lowered in KAZAKH_WORDS or lowered.startswith(("сатып", "шарт", "бойынша", "жыл", "тапсырыс"))


def detect_language(question):
    """Казахский, если казахских слов хотя бы 30%: одно казахское название в русском вопросе не переключает язык."""
    words = re.findall(r"[^\W\d_]+", question)
    kazakh = sum(1 for word in words if is_kazakh(word))
    return "kk" if words and kazakh / len(words) >= 0.3 else "ru"


def filter_lines(lang, filters):
    names = TEXT[lang]["filter_names"]
    parts = []
    for key, value in filters.items():
        if key == "threshold":
            shown = pct(value)[1:] + "%"
        elif key == "min_ratio":
            shown = "×" + ratio_text(value)
        elif key in ("by", "group_by"):
            shown = TEXT[lang]["groups"].get(value, value)
        elif isinstance(value, list):
            shown = ", ".join(map(str, value))
        else:
            shown = value
        parts.append(f"{names.get(key, key)}: {shown}")
    return "; ".join(parts) or TEXT[lang]["all"]


def verdict(lang, r):
    tool, f = r["tool"], r["facts"]
    if tool == "search_contracts":
        return t(lang, "v_search", **f) if f["found"] else t(lang, "v_search_none")
    if tool == "spend_summary":
        if not f["contracts"]:
            return t(lang, "v_spend_none")
        text = t(lang, "v_spend", amount=money(f["amount"]), contracts=f["contracts"])
        if r.get("segments") and f["group_by"] in ("customer", "supplier", "enstru"):
            top = r["segments"][0]
            text += t(lang, "v_spend_top", label=top["label"], top=money(top["amount"]))
        return text
    if tool == "compare_prices":
        if not f["found"]:
            return t(lang, "v_compare_none")
        text = t(lang, "v_compare", name=f["enstru_name"] or f["enstru_code"], code=f["enstru_code"], unit=f["unit"],
                 wavg=money(f["weighted_avg"]), median=money(f["median"]), n=r["n_objects"], customers=f["customers"])
        if f["median"] and f["weighted_avg"] and max(f["weighted_avg"], f["median"]) / min(f["weighted_avg"], f["median"]) > 1.5:
            text += t(lang, "v_compare_skew", ratio=ratio_text(max(f["weighted_avg"], f["median"]) / min(f["weighted_avg"], f["median"])))
        return text
    if tool == "price_anomalies":
        thr = f"{f['threshold'] * 100:.0f}"
        if not f["matches"]:
            return t(lang, "v_anom_none", thr=thr, analyzed=f["analyzed"])
        return t(lang, "v_anom", thr=thr, **{k: f[k] for k in ("matches", "analyzed", "groups", "confirmed", "suspect")})
    if tool == "fair_price_check":
        return verdict_fair(lang, r)
    if tool == "volume_anomalies":
        if not f["compared"]:
            return t(lang, "v_vol_nodata", year=f["year"])
        if not f["matches"]:
            return t(lang, "v_vol_none", year=f["year"], compared=f["compared"])
        ratio = ratio_text(f["min_ratio"])
        return t(lang, "v_vol", year=f["year"], matches=f["matches"], ratio=ratio, strong=f["strong"], compared=f["compared"])
    return ""


def verdict_fair(lang, r):
    top = r["top"]
    if not top:
        lot = r["filters"].get("lot_number")
        if lot and r["facts"]["lot_found"] is False:
            return t(lang, "v_fair_nolot", lot=lot)
        return t(lang, "v_fair_nocontract")
    f = r["facts"]
    if f["positions"] > 1:
        counts = f["counts"]
        named = {flag: counts.get(flag, 0) for flag in ("high", "low", "normal", "suspect_error")}
        text = t(lang, "v_fair_multi", positions=f["positions"], high=named["high"], low=named["low"],
                 normal=named["normal"], suspect=named["suspect_error"], other=f["positions"] - sum(named.values()))
        return text + (t(lang, "v_fair_shown", shown=f["shown"]) if f["shown"] < f["positions"] else "")
    p = top[0]
    if p["flag"] == "not_comparable":
        return t(lang, "v_fair_not_comparable")
    values = dict(price=money(p["price"]), unit=p["unit"], fair=money(p["fair_price"]),
                  conf=TEXT[lang]["conf"][p["confidence"]])
    if p["flag"] in ("high", "low", "normal"):
        return t(lang, f"v_fair_{p['flag']}", dev=pct(p["deviation"])[1:] if p["flag"] != "normal" else pct(p["deviation"]),
                 **values)
    if p["flag"] == "suspect_error":
        return t(lang, "v_fair_error", ratio=num(p["price"] / p["fair_price"] if p["price"] > p["fair_price"]
                                                  else p["fair_price"] / p["price"], 0), **values)
    return t(lang, "v_fair_unknown", price=values["price"], unit=values["unit"])


def metric(lang, r):
    method = r["method"]
    f = r["facts"]
    key = {"sql_filter": "m_sql_filter", "sql_sum": "m_sql_sum", "weighted_avg_median": "m_weighted_avg_median",
           "weighted_avg_leave_one_out": "m_anom", "median_iqr_peers_adjusted": "m_fair",
           "plan_qty_vs_history_mad": "m_volume"}[method]
    if method == "weighted_avg_leave_one_out":
        return t(lang, key, thr=f"{f['threshold'] * 100:.0f}")
    if method == "plan_qty_vs_history_mad":
        return t(lang, key, ratio=ratio_text(f["min_ratio"]))
    return t(lang, key)


def comparison(lang, r):
    tool, f, lines = r["tool"], r["facts"], []
    tx = TEXT[lang]
    if tool == "search_contracts":
        return [f"{tx['contracts']}: {f['found']}"]
    if tool == "spend_summary":
        lines.append(f"{tx['segments']} ({tx['groups'][f['group_by']]}):")
        lines += [f"  {s['label']}: {money(s['amount'])} {tx['tg']} ({s['contracts']})" for s in r["segments"]]
        return lines
    if tool == "compare_prices":
        if not f["found"]:
            return [tx["none"]]
        lines.append(f"{tx['wavg']}: {money(f['weighted_avg'])} {tx['tg']}; {tx['median']}: {money(f['median'])} {tx['tg']}; "
                     f"{tx['q']}: {money(f['q1'])}–{money(f['q3'])}; {tx['side_min']} {money(f['min_price'])}, {tx['side_max']} {money(f['max_price'])}")
        for s in r["segments"]:
            lines.append(f"  {s['label']}: {tx['wavg']} {money(s['weighted_avg'])}, {tx['median']} {money(s['median'])}, N={s['n']}")
        return lines
    if tool == "price_anomalies":
        thr = f"{f['threshold'] * 100:.0f}"
        return [t(lang, "c_analyzed", n=f["analyzed"], groups=f["groups"]), t(lang, "c_matches", thr=thr, n=f["matches"]),
                "  " + t(lang, "c_confirmed", n=f["confirmed"]), "  " + t(lang, "c_suspect", n=f["suspect"]),
                "  " + t(lang, "c_unchecked", n=f["unchecked"])]
    if tool == "fair_price_check":
        for p in r["top"]:
            if p["fair_price"] is None:
                lines.append(f"ID {p['unit_id']} {p['item'] or ''}: {tx['flags'][p['flag']]}")
                continue
            lines.append(f"ID {p['unit_id']} {p['item'] or ''}: "
                         + t(lang, "cmp_fair", price=f"{money(p['price'])} {tx['tg']}", fair=f"{money(p['fair_price'])} {tx['tg']}",
                             extra=f"{tx['q']} {money(p['q1'])}–{money(p['q3'])}")
                         + f", N={p['n_peers']}, "
                         f"{tx['levels'].get(p['peer_level'], '')}, {pct(p['deviation'])}%")
        return lines
    if tool == "volume_anomalies":
        return [t(lang, "c_volume", compared=f["compared"], ratio=ratio_text(f["min_ratio"]), matches=f["matches"], strong=f["strong"])]
    return lines


def examples(lang, r):
    tx, tool, lines = TEXT[lang], r["tool"], []
    for i, p in enumerate(r.get("top") or [], start=1):
        if tool == "search_contracts":
            lines.append(f"{i}. {tx['contract']} {p['number'] or ''} (ID {p['contract_id']}), {p['date']}: {p['customer']} → "
                         f"{p['supplier']}, {tx['contract_sum']}: {money(p['amount'])} {tx['tg']}, {tx['status']}: {p['status'] or '—'}; {p['item'] or ''}\n"
                         f"   {p['link']}" + (f"\n   {tx['ann']}: {p['announce_link']}" if p.get("announce_link") else ""))
        elif tool == "spend_summary":
            lines.append(f"{i}. {tx['contract']} ID {p['contract_id']}, {p['date']}: {p['customer']} → {p['supplier']}, {money(p['amount'])} {tx['tg']}\n   {p['link']}")
        elif tool == "compare_prices":
            lines.append(f"{i}. {tx['side_' + p['side']]}: {money(p['price'])} {tx['tg']} — {p['customer']}, {p['date']}, "
                         f"{tx['contract']} ID {p['contract_id']}\n   {p['link']}")
        elif tool == "price_anomalies":
            lines.append(f"{i}. {p['item']} ({p['enstru']}): {tx['contract']} {p['number'] or ''} (ID {p['contract_id']}), {p['date']}, "
                         f"{p['customer']}; "
                         + t(lang, "cmp_ref", price=f"{money(p['price'])} {tx['tg']}/{p['unit']}", ref=money(p["reference"]),
                             extra=f"{pct(p['deviation'])}%, N={p['group_n']}")
                         + "; "
                         + (f"{tx['fair_short']} {money(p['fair_price'])}; " if p.get("fair_price") else "")
                         + f"{tx['flags'][p['flag']]}, "
                         f"{tx['confidence'].lower()}: {tx['conf'][p['confidence']]}\n   {p['link']}")
        elif tool == "fair_price_check":
            lines += fair_example(tx, p, i, r["top"][i - 2] if i > 1 else None)
        elif tool == "volume_anomalies":
            z = f", z={num(p['robust_z'], 1)}" if p["robust_z"] is not None else ""
            lines.append(f"{i}. {p['customer']}, {p['item']} ({p['enstru']}): {tx['plan_label']} {p['year']} — "
                         + t(lang, "cmp_base", qty=f"{num(p['plan_qty'])} {p['unit']}", years=p["baseline_years"], base=num(p["baseline_qty"]),
                             extra=f"×{ratio_text(p['ratio'])}{z}")
                         + f"; {tx['extra']}: "
                         f"{money(p['extra_amount']) if p['extra_amount'] is not None else '—'} {tx['tg']}; {tx['plan']}: "
                         f"{', '.join(map(str, p['plan_ids']))}"
                         + (f"\n   {tx['recent_contracts']}:" + "".join(f"\n   {link}" for link in p["links"]) if p["links"] else ""))
    return lines or [tx["none"]]


def fair_example(tx, p, i, previous):
    """Шапка договора — один раз на договор; аналоги — только если они отличаются от показанных для предыдущей позиции."""
    lines = []
    if not previous or previous["contract_id"] != p["contract_id"]:
        lines.append(f"{tx['contract']} {p['number'] or ''} (ID {p['contract_id']}), {p['date']}, {p['customer']}\n   {p['link']}"
                     + (f"\n   {tx['ann']}: {p['announce_link']}" if p.get("announce_link") else ""))
    price = f"{money(p['price'])} {tx['tg']}/{p['unit']}" if p["price"] is not None else tx["none"]
    lines.append(f"{i}. {p['item'] or ''} ({p['enstru']}, ID {p['unit_id']}): {price} — {tx['flags'][p['flag']]}, "
                 f"{tx['confidence'].lower()}: {tx['conf'][p['confidence']]}")
    peer_ids = [q["contract_id"] for q in p["peers"]]
    if peer_ids and (not previous or peer_ids != [q["contract_id"] for q in previous["peers"]]):
        lines.append(f"   {tx['peer_title']} ({tx['peers']}):")
        lines += [f"   - {q['date']}, {q['customer']}, {money(q['price'])} {tx['tg']}, {tx['contract']} ID {q['contract_id']}: {q['link']}"
                  for q in p["peers"]]
    return lines


def limitations(lang, r):
    tx, codes = TEXT[lang], r["limitations"]
    lines = []
    for code in codes:
        key = f"l_{code}"
        if code == "several_enstru":
            lines.append(tx[key].format(codes=", ".join(r["facts"].get("other_codes", []))))
        elif code == "same_code_spread":
            spread = r["facts"]["spread"]
            lines.append(tx[key].format(code=spread["code"], unit=spread["unit"], min=money(spread["min"]), max=money(spread["max"])))
        elif code == "bin_not_target":
            lines.append(tx[key].format(bins=", ".join(r.get("outside_bins", []))))
        else:
            lines.append(tx[key])
    price_tools = ("compare_prices", "price_anomalies", "fair_price_check")
    lines.append(tx["l_scope"] if r["tool"] in price_tools else tx["l_scope_data"])
    return lines


def render(result, lang):
    tx = TEXT[lang]
    period = result["period"]
    data = [f"{tx['period']}: {period['from']} — {period['to']}",
            f"{tx['filters']}: {filter_lines(lang, result['filters'])}",
            f"{tx['source']}: {tx['source_text']}" + (f"; {tx['as_of']}: {result['data_as_of']}" if result.get("data_as_of") else ""),
            f"{tx['n']}: {result['n_objects']}"]
    parts = [
        (tx["h_verdict"], [verdict(lang, result)]),
        (tx["h_data"], data),
        (tx["h_compare"], comparison(lang, result)),
        (tx["h_metric"], [metric(lang, result)]),
        (tx["h_limits"], [f"{tx['confidence']}: {tx['conf'][result['confidence']]}"] + limitations(lang, result)),
        (tx["h_top"], examples(lang, result)),
    ]
    return "\n\n".join(f"{title.upper()}\n" + "\n".join(lines) for title, lines in parts)


def render_plain(lang, key):
    return TEXT[lang][key]