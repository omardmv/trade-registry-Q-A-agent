"""One deterministic sentence describing what a plan computed, from the plan and the dictionary labels."""
import datetime as dt
import re

from app.plan.dictionary import Dictionary, Subject
from app.plan.dictionary import label as _dict_label
from app.text import to_cyrillic
from app.plan.model import Filter, Measure, Period, Plan

_T = {
    "ru": {
        "prefix": "Расчёт:", "all_time": "за весь период ({d1}–{d2})", "year": "за {y} год", "years": "за {y1}–{y2} годы",
        "years_in": "за {ys} годы", "month": "за {m}", "months": "за {m1}–{m2}", "quarter": "за {q} квартал {y} года",
        "dates": "с {d1} по {d2}", "date_from": "с {d1}", "date_to": "по {d2}", "date_eq": "на {d}",
        "data_until": " (данные до {d})", "by": "по полю: {g}", "and": " и ",
        "count": "{v}", "sum": "сумма: {v}", "avg": "среднее: {v}", "median": "медиана: {v}", "min": "минимум: {v}",
        "max": "максимум: {v}", "distinct": "число различных: {v}",
        "share": "доля ({b}), где {c}, — от всей выборки", "share_count": "по числу", "share_all": ", включая аннулированные",
        "share_valid": ", без аннулированных",
        "top": "топ-{n}", "top_per": "топ-{n} в каждой группе ({g})", "default_limit": "показаны первые {n}",
        "asc": "по возрастанию (наименьшие значения)",
        "compare": "сравнение периодов: {p1} и {p2}; изменение в % к первому периоду",
        "compare_points": "доли — изменение в процентных пунктах",
        "yes": "да", "no": "нет", "not": "не {v}", "between": "от {a} до {b}", "ge": "≥ {v}", "le": "≤ {v}",
        "variant": "{v} ({n} написаний)",
        "excluded_cancelled": "без аннулированных сделок", "all_statuses": "все состояния сделок",
        "status_split": "все сделки и отдельно без аннулированных",
        "uzs_converted": "суммы в сумах (экспортные сделки пересчитаны по курсу на дату сделки)",
        "usd_converted": "суммы в долларах (сумовые сделки пересчитаны по курсу на дату сделки)",
        "broker_both_sides": "брокер учитывается и со стороны продавца, и со стороны покупателя (сделка — один раз)",
        "role_split": "отдельно как покупатель и как продавец",
        "broker_side_split": "отдельно как брокер продавца и как брокер покупателя",
        "ties_kept": "при равных значениях показаны все",
        "volume_by_unit": "по единицам измерения",
        "per_kg": "цена за тонну = цена за кг × 1000",
        "record": "записи",
    },
    "uz": {
        "prefix": "Hisob:", "all_time": "butun davr uchun ({d1}–{d2})", "year": "{y}-yil", "years": "{y1}–{y2}-yillar",
        "years_in": "{ys}-yillar", "month": "{m}", "months": "{m1}–{m2}", "quarter": "{y}-yil {q}-chorak",
        "dates": "{d1} dan {d2} gacha", "date_from": "{d1} dan", "date_to": "{d2} gacha", "date_eq": "{d}",
        "data_until": " (ma’lumotlar {d} gacha)", "by": "bo‘yicha: {g}", "and": " va ",
        "count": "{v}", "sum": "jami: {v}", "avg": "o‘rtacha: {v}", "median": "mediana: {v}", "min": "eng kichik: {v}",
        "max": "eng katta: {v}", "distinct": "turli qiymatlar soni: {v}",
        "share": "ulush ({b}), bunda {c}, — butun tanlovdan", "share_count": "soni bo‘yicha",
        "share_all": ", bekor qilinganlar bilan", "share_valid": ", bekor qilinganlarsiz",
        "top": "eng yaxshi {n} ta", "top_per": "har bir guruhda ({g}) eng yaxshi {n} ta", "default_limit": "birinchi {n} tasi ko‘rsatilgan",
        "asc": "o‘sish tartibida (eng kichik qiymatlar)",
        "compare": "davrlar taqqoslanadi: {p1} va {p2}; birinchi davrga nisbatan o‘zgarish, %",
        "compare_points": "ulushlar — o‘zgarish foiz punktlarida",
        "yes": "ha", "no": "yo‘q", "not": "{v} emas", "between": "{a} dan {b} gacha", "ge": "≥ {v}", "le": "≤ {v}",
        "variant": "{v} ({n} xil yozilishi)",
        "excluded_cancelled": "bekor qilingan bitimlar hisobga olinmagan", "all_statuses": "barcha holatdagi bitimlar",
        "status_split": "barcha bitimlar va alohida bekor qilinganlarsiz",
        "uzs_converted": "summalar so‘mda (eksport bitimlari bitim sanasidagi kurs bo‘yicha)",
        "usd_converted": "summalar dollarda (so‘mdagi bitimlar bitim sanasidagi kurs bo‘yicha)",
        "broker_both_sides": "broker sotuvchi va xaridor tomonida ham hisobga olingan (bitim bir marta)",
        "role_split": "xaridor va sotuvchi sifatida alohida",
        "broker_side_split": "sotuvchi brokeri va xaridor brokeri sifatida alohida",
        "ties_kept": "teng qiymatlarda hammasi ko‘rsatilgan",
        "volume_by_unit": "o‘lchov birliklari bo‘yicha",
        "per_kg": "tonna narxi = kg narxi × 1000",
        "record": "yozuvlar",
    },
}


def _cyr_template(text: str) -> str:
    """Template words to Uzbek Cyrillic; {placeholders} stay as they are."""
    return "".join(part if part.startswith("{") else to_cyrillic(part) for part in re.split(r"(\{[^}]*\})", text))


_T["uz-Cyrl"] = {k: _cyr_template(v) for k, v in _T["uz"].items()}


def label(f, lang: str) -> str:
    """Dictionary label; for uz-Cyrl the (Latin) Uzbek label in Cyrillic. Data values are never converted."""
    text = _dict_label(f, lang)
    return to_cyrillic(text) if lang == "uz-Cyrl" else text


def _lbl(s: Subject, lang: str) -> str:
    text = s.lbl(lang)
    return to_cyrillic(text) if lang == "uz-Cyrl" else text
_NOTE_KEYS = ("excluded_cancelled", "all_statuses", "status_split", "uzs_converted", "usd_converted",
              "broker_both_sides", "role_split", "broker_side_split", "volume_by_unit", "ties_kept")
_TIME = ("year", "quarter", "month", "day")


def _d(x: dt.date) -> str:
    return x.strftime("%d.%m.%Y")


def _v(x, t) -> str:
    if isinstance(x, bool):
        return t["yes"] if x else t["no"]
    if isinstance(x, dt.date):
        return _d(x)
    return str(x)


def _value(flt: Filter, t) -> str:
    v = flt.value
    if flt.op == "in":
        return ", ".join(_v(x, t) for x in v)
    if flt.op == "between":
        return t["between"].format(a=_v(v[0], t), b=_v(v[1], t))
    if flt.op == "!=":
        return t["not"].format(v=_v(v, t))
    if flt.op == ">=":
        return t["ge"].format(v=_v(v, t))
    if flt.op == "<=":
        return t["le"].format(v=_v(v, t))
    return _v(v, t)


def _one(plan_filters, name):
    return next((f for f in plan_filters if f.field == name), None)


def _period(s: Subject, filters, t) -> tuple[str, bool, list]:
    """(period phrase, reaches the end of the data, the time filters the phrase states)."""
    lo, hi = s.data_range
    y, m, q = _one(filters, "year"), _one(filters, "month"), _one(filters, "quarter")
    dflt = _one(filters, s.date_field) or _one(filters, "day")
    if dflt is not None:
        v = dflt.value
        if dflt.op == "=":
            return t["date_eq"].format(d=_d(v)), False, [dflt]
        if dflt.op == "between":
            return t["dates"].format(d1=_d(v[0]), d2=_d(v[1])), v[1] >= hi, [dflt]
        if dflt.op == ">=":
            return t["date_from"].format(d1=_d(v)), True, [dflt]
        if dflt.op == "<=":
            return t["date_to"].format(d2=_d(v)), False, [dflt]
    if y is None:
        return t["all_time"].format(d1=_d(lo), d2=_d(hi)), False, []
    if y.op == "=":
        yy = y.value
        if q is not None and q.op == "=":
            return t["quarter"].format(q=q.value, y=yy), yy == hi.year and 3 * q.value >= hi.month, [y, q]
        if m is not None and m.op in ("=", "between"):
            m1, m2 = (m.value, m.value) if m.op == "=" else m.value
            text = (t["month"].format(m=f"{m1:02d}.{yy}") if m1 == m2
                    else t["months"].format(m1=f"{m1:02d}.{yy}", m2=f"{m2:02d}.{yy}"))
            return text, yy == hi.year and m2 >= hi.month, [y, m]
        return t["year"].format(y=yy), yy == hi.year, [y]
    if y.op == "between":
        return t["years"].format(y1=y.value[0], y2=y.value[1]), y.value[1] >= hi.year, [y]
    if y.op == "in":
        return t["years_in"].format(ys=", ".join(str(v) for v in y.value)), hi.year in y.value, [y]
    return f"{label(s.fields['year'], next(k for k, v in _T.items() if v is t))} {_value(y, t)}", True, [y]


def _period_text(s: Subject, p: Period, t) -> str:
    if p.day is not None:
        return t["date_eq"].format(d=_d(p.date()))
    months = p.months()
    if p.quarter is not None:
        return t["quarter"].format(q=p.quarter, y=p.year)
    if months is None:
        return t["year"].format(y=p.year)
    if months[0] == months[1]:
        return t["month"].format(m=f"{months[0]:02d}.{p.year}")
    return t["months"].format(m1=f"{months[0]:02d}.{p.year}", m2=f"{months[1]:02d}.{p.year}")


def _filter_phrase(s: Subject, flt: Filter, notes, lang, t) -> str:
    f = s.fields[flt.field]
    val = _value(flt, t)
    if f.ground == "product_variant":
        n = next((x.removeprefix("variant_spellings_") for x in notes if x.startswith("variant_spellings_")), "?")
        val = t["variant"].format(v=val, n=n)
    return f"{label(f, lang)}: {val}"


def _measure_phrase(s: Subject, plan: Plan, m: Measure, lang, t) -> str:
    if m.agg == "count":
        text = s.count_label.get("uz" if lang.startswith("uz") else "ru") or s.count_col
        return to_cyrillic(text) if lang == "uz-Cyrl" else text
    if m.agg in s.measures:
        return label(s.measures[m.agg], lang)
    if m.agg == "share":
        cond = "; ".join(_filter_phrase(s, w, [], lang, t) for w in m.where)
        basis = t["share_count"] if m.of is None else label(s.fields[m.of], lang)
        text = t["share"].format(b=basis, c=cond, s=_lbl(s, lang))
        if s.cancelled:
            touches = any(w.field in s.status_fields for w in m.where) or any(
                f.field in s.status_fields for f in plan.filters)
            if m.of is None or touches:
                text += t["share_all"] if not any(f.field in s.status_fields for f in plan.filters) else ""
            else:
                text += t["share_valid"]
        return text
    return t[m.agg].format(v=label(s.fields[m.of], lang))


def _per_kg(s: Subject, m: Measure) -> bool:
    if m.agg in s.measures:
        return s.measures[m.agg].per_kg
    return bool(m.of) and m.of in s.fields and s.fields[m.of].per_kg and m.agg != "distinct"


def explain_plan(plan: Plan, d: Dictionary, notes: list[str], lang: str) -> str:
    lang = "uz-Cyrl" if lang == "uz-Cyrl" else ("uz" if lang.startswith("uz") else "ru")
    t = _T[lang]
    s = d.subject(plan.subject)
    parts = [_lbl(s, lang)]
    time_names = {"year", "month", "quarter", "day", s.date_field} if s.date_field else set()
    if plan.compare:
        p1, p2 = (_period_text(s, p, t) for p in plan.compare)
        text = t["compare"].format(p1=p1, p2=p2)
        hi = s.data_range[1]
        if any(p.year == hi.year and p.day is None for p in plan.compare):
            text += t["data_until"].format(d=_d(hi))
        parts.append(text)
        if any(m.agg in ("share", "cancelled_share") for m in plan.measures):
            parts.append(t["compare_points"])
    elif s.data_range is not None:
        text, to_end, used = _period(s, plan.filters, t)
        if to_end:
            text += t["data_until"].format(d=_d(s.data_range[1]))
        parts.append(text)
        # time filters the period phrase does not state (a month list, a quarter with a year range, ...)
        parts += [_filter_phrase(s, f, notes, lang, t) for f in plan.filters
                  if f.field in time_names and not any(f is u for u in used)]
    lookup = any(s.fields[f.field].type == "record" and f.op in ("=", "in") for f in plan.filters)
    if lookup:
        parts = parts[:1]
    for flt in plan.filters:
        if flt.field not in time_names:
            parts.append(_filter_phrase(s, flt, notes, lang, t))
    if lookup:
        return f"{t['prefix']} " + "; ".join(parts) + "."
    for m in plan.measures:
        phrase = _measure_phrase(s, plan, m, lang, t)
        if phrase not in parts:
            parts.append(phrase)
    if any(_per_kg(s, m) for m in plan.measures):
        parts.append(t["per_kg"])
    if plan.group_by:
        parts.append(t["by"].format(g=t["and"].join(label(s.fields[g], lang) for g in plan.group_by)))
    if plan.limit is not None:
        if plan.default_limit:
            parts.append(t["default_limit"].format(n=plan.limit))
        elif len(plan.group_by) == 2:
            parts.append(t["top_per"].format(n=plan.limit, g=label(s.fields[plan.group_by[0]], lang)))
        else:
            parts.append(t["top"].format(n=plan.limit))
    if plan.order == "asc":
        parts.append(t["asc"])
    scope_free_only = bool(plan.measures) and all(
        m.agg == "share" or (m.agg in s.measures and s.measures[m.agg].scope_free) for m in plan.measures)
    for note in notes:
        if note in _NOTE_KEYS:
            if scope_free_only and note in ("excluded_cancelled", "all_statuses", "status_split"):
                continue      # the measure phrase states its own row set
            if note == "all_statuses" and any(f.field in s.status_fields for f in plan.filters) and not any(
                    f.field == "cancelled" and f.value is False for f in plan.filters):
                continue
            if note == "all_statuses" and any(f.field == "cancelled" and f.value is False for f in plan.filters):
                continue      # the filter phrase says it
            parts.append(t[note])
    return f"{t['prefix']} " + "; ".join(parts) + "."
