"""One-line, deterministic description of what a compiled query computed."""
from app.compiler.spec import DATA_END, DealsAggregate, Filters, Spec

_T = {
    "ru": {
        "prefix": "Расчёт:", "deals": "сделки", "all_time": "за весь период (03.01.2022–30.10.2026)",
        "period_year": "за {y} год", "period_month": "за {m}", "period_months": "за {m1}–{m2}",
        "period_dates": "с {d1} по {d2}", "product": "товар: {v}", "family": "группа товаров: {v}",
        "region": "регион: {v}", "buyer": "покупатель ИНН {v}", "seller": "продавец ИНН {v}",
        "participant": "участник ИНН {v}", "broker": "брокер {v} (любая сторона)",
        "seller_broker": "брокер продавца {v}", "buyer_broker": "брокер покупателя {v}",
        "contract": "контракт {v}", "contract_type": "тип контракта: {v}", "contract_form": "форма: {v}",
        "trade_type": "тип торгов: {v}", "status": "состояние: {v}",
        "excluded_cancelled": "без аннулированных сделок", "all_statuses": "все состояния сделок",
        "status_split": "все сделки и отдельно без аннулированных",
        "uzs_converted": "суммы в сумах (экспортные сделки пересчитаны по курсу на дату сделки)",
        "usd_converted": "суммы в долларах (сумовые сделки пересчитаны по курсу на дату сделки)",
        "broker_both_sides": "брокер учитывается и со стороны продавца, и со стороны покупателя",
        "role_split": "отдельно как покупатель и как продавец",
        "volume_by_unit": "объём по единицам измерения", "ranked_top": "топ-{n}",
        "cancelled_share": "доля аннулированных — от всех сделок",
        "data_until": " (данные до 30.10.2026)",
    },
    "uz": {
        "prefix": "Hisob:", "deals": "bitimlar", "all_time": "butun davr uchun (03.01.2022–30.10.2026)",
        "period_year": "{y}-yil", "period_month": "{m}", "period_months": "{m1}–{m2}",
        "period_dates": "{d1} dan {d2} gacha", "product": "tovar: {v}", "family": "tovar guruhi: {v}",
        "region": "hudud: {v}", "buyer": "xaridor STIR {v}", "seller": "sotuvchi STIR {v}",
        "participant": "ishtirokchi STIR {v}", "broker": "broker {v} (istalgan tomon)",
        "seller_broker": "sotuvchi brokeri {v}", "buyer_broker": "xaridor brokeri {v}",
        "contract": "shartnoma {v}", "contract_type": "shartnoma turi: {v}", "contract_form": "shakli: {v}",
        "trade_type": "savdo turi: {v}", "status": "holati: {v}",
        "excluded_cancelled": "bekor qilingan bitimlar hisobga olinmagan", "all_statuses": "barcha holatdagi bitimlar",
        "status_split": "barcha bitimlar va alohida bekor qilinganlarsiz",
        "uzs_converted": "summalar so‘mda (eksport bitimlari bitim sanasidagi kurs bo‘yicha)",
        "usd_converted": "summalar dollarda (so‘mdagi bitimlar bitim sanasidagi kurs bo‘yicha)",
        "broker_both_sides": "broker sotuvchi va xaridor tomonida ham hisobga olingan",
        "role_split": "xaridor va sotuvchi sifatida alohida",
        "volume_by_unit": "hajm o‘lchov birliklari bo‘yicha", "ranked_top": "eng yaxshi {n} ta",
        "cancelled_share": "bekor qilinganlar ulushi — barcha bitimlardan",
        "data_until": " (ma’lumotlar 30.10.2026 gacha)",
    },
}

# Compiler note codes (app/compiler/sql.py) that are rendered as fixed phrases.
_NOTE_KEYS = ("excluded_cancelled", "all_statuses", "status_split", "uzs_converted", "usd_converted",
              "broker_both_sides", "role_split", "volume_by_unit")
# Status phrases describe the row set; a cancelled_pct-only query always runs over all deals.
_STATUS_NOTES = ("excluded_cancelled", "all_statuses", "status_split")


def _past_data_end(f: Filters) -> bool:
    if f.date_to is not None and f.date_to > DATA_END:
        return True
    if f.date_from is not None and f.date_to is None and f.date_from > DATA_END:
        return True
    if f.year == 2026:
        return f.month_from is None or (f.month_to or 0) >= 10
    return False


def _period(f: Filters, t: dict) -> str:
    text = _period_text(f, t)
    return text + t["data_until"] if _past_data_end(f) else text


def _period_text(f: Filters, t: dict) -> str:
    if f.date_from or f.date_to:
        d1 = f.date_from.strftime("%d.%m.%Y") if f.date_from else "03.01.2022"
        d2 = f.date_to.strftime("%d.%m.%Y") if f.date_to else "30.10.2026"
        return t["period_dates"].format(d1=d1, d2=d2)
    if f.year is None and f.month_from is None:
        return t["all_time"]
    if f.month_from is None:
        return t["period_year"].format(y=f.year)
    suffix = f".{f.year}" if f.year is not None else ""
    if f.month_from == f.month_to:
        return t["period_month"].format(m=f"{f.month_from:02d}{suffix}")
    return t["period_months"].format(m1=f"{f.month_from:02d}{suffix}", m2=f"{f.month_to:02d}{suffix}")


def explain(spec: Spec, notes: list[str], lang: str) -> str:
    """Return one sentence describing the computation; empty for non-aggregate specs.

    Unknown languages fall back to Russian.
    """
    if not isinstance(spec, DealsAggregate):
        return ""
    t = _T["uz"] if lang in ("uz", "uz-Cyrl") else _T["ru"]
    f = spec.filters
    parts = [f"{t['deals']} {_period(f, t)}"]
    for key, values in (("product", f.products), ("region", f.regions), ("buyer", f.buyer_inns),
                        ("seller", f.seller_inns), ("participant", f.participant_inns), ("broker", f.brokers),
                        ("seller_broker", f.seller_brokers), ("buyer_broker", f.buyer_brokers),
                        ("status", f.statuses)):
        if values:
            parts.append(t[key].format(v=", ".join(values)))
    for key, value in (("family", f.product_family), ("contract", f.contract_id),
                       ("contract_type", f.contract_type), ("contract_form", f.contract_form),
                       ("trade_type", f.trade_type)):
        if value is not None:
            parts.append(t[key].format(v=value))
    cancelled_only = spec.measures == ["cancelled_pct"]
    for note in notes:
        if note.startswith("ranked_top_"):
            parts.append(t["ranked_top"].format(n=note.removeprefix("ranked_top_")))
        elif note not in _NOTE_KEYS:
            continue
        elif cancelled_only and note in _STATUS_NOTES:
            continue
        elif note == "all_statuses" and f.statuses:
            continue  # the status filter phrase already describes the scope
        else:
            parts.append(t[note])
    if "cancelled_pct" in spec.measures:
        parts.append(t["cancelled_share"])
    return f"{t['prefix']} " + "; ".join(parts) + "."
