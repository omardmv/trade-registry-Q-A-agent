"""One-line, deterministic description of what a compiled query computed."""
from app.compiler.spec import DATA_END, DealsAggregate, Filters, RegistryCount, Spec, UsdRate
from app.compiler.sql import SCOPE_FREE

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
        "broker_side_split": "отдельно как брокер продавца и как брокер покупателя",
        "volume_by_unit": "объём по единицам измерения", "ranked_top": "топ-{n}",
        "cancelled_share": "доля аннулированных — от всех сделок",
        "data_until": " (данные до 30.10.2026)",
        "usd_day": "курс доллара на {d}", "usd_period": "курс доллара {p} (среднее, минимум и максимум по дням)",
        "usd_year": "за {y} год", "usd_month": "за {m}",
        "usd_extreme": "{e} курс доллара {p} и дата(ы), когда он был", "usd_min": "минимальный",
        "usd_max": "максимальный",
        "category": "категория: {v}", "industry": "отрасль: {v}", "product_group": "группа товара: {v}",
        "delivery_basis": "базис поставки: {v}", "packaging": "упаковка: {v}", "hs_code": "код ТН ВЭД: {v}",
        "product_variant": "наименование товара в реестре: {v}",
        "buyer_region": "регион покупателя (по реестру участников): {v}",
        "seller_region": "регион продавца (по реестру участников): {v}",
        "buyer_activity": "вид деятельности покупателя: {v}", "seller_activity": "вид деятельности продавца: {v}",
        "unit": "единица измерения: {v}",
        "order_asc": "по возрастанию (наименьшие значения)",
        "share": "доля сделок, где {c}, — от всех сделок выборки, включая аннулированные, если состояние не задано",
        "share_field_status": "состояние: {v}",
        "compare": "сравнение периодов: {p1} и {p2}; изменение в % к первому периоду",
        "price_both": "средняя цена двумя способами: взвешенная (сумма ÷ объём, сум за кг) и простое среднее цен "
                      "отдельных сделок (сум за тонну); только товары в тоннах и килограммах",
        "m_avg_price_uzs_per_kg": "средняя цена взвешенная по объёму (сумма ÷ объём), сум за кг",
        "m_mean_deal_price_uzs_per_t": "простое среднее цен отдельных сделок, сум за тонну",
        "m_median_price_uzs_per_kg": "медиана цен отдельных сделок, сум за кг",
        "m_min_unit_price_uzs": "цена сделки за единицу измерения в сумах, по единицам измерения",
        "m_max_unit_price_uzs": "цена сделки за единицу измерения в сумах, по единицам измерения",
        "m_avg_price_change_pct": "среднее отклонение цены сделки от стартовой, %",
        "m_avg_days_to_payment": "среднее число дней от сделки до оплаты (оплаченные сделки)",
        "m_avg_days_to_delivery": "среднее число дней от оплаты до поставки (поставленные сделки)",
        "m_avg_delivery_delay_days": "средняя просрочка поставки в днях — только поставки позже срока "
                                     "(дата оплаты + срок поставки)",
        "m_late_delivery_count": "поставка позже срока (дата оплаты + срок поставки)",
        "m_late_payment_count": "просрочка оплаты: оплачено позже срока или не оплачено, а срок оплаты истёк "
                                "до 30.10.2026; по всем состояниям",
        "m_undelivered_lots": "не поставлено лотов = куплено − поставлено",
        "m_delivered_volume": "поставленный объём = поставлено лотов × лот, по единицам измерения",
        "m_undelivered_volume": "не поставлено = (куплено − поставлено лотов) × лот, по единицам измерения",
        "m_distinct_brokers": "число различных брокеров с любой стороны сделки",
        "m_distinct_buyers": "число различных покупателей", "m_distinct_sellers": "число различных продавцов",
        "m_distinct_products": "число различных товаров", "m_distinct_regions": "число различных регионов",
        "m_distinct_seller_brokers": "число различных брокеров продавцов",
        "m_distinct_buyer_brokers": "число различных брокеров покупателей",
        "reg_participants": "участники реестра", "reg_brokers": "брокеры реестра", "reg_contracts": "контракты",
        "reg_activity": "вид деятельности: {v}", "reg_role": "роль: {v}", "reg_region": "регион: {v}",
        "reg_vat": "плательщик НДС: {v}", "yes": "да", "no": "нет", "reg_active_on": "действует на {v}",
        "reg_year": "зарегистрирован в {v} году",
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
        "broker_side_split": "sotuvchi brokeri va xaridor brokeri sifatida alohida",
        "volume_by_unit": "hajm o‘lchov birliklari bo‘yicha", "ranked_top": "eng yaxshi {n} ta",
        "cancelled_share": "bekor qilinganlar ulushi — barcha bitimlardan",
        "data_until": " (ma’lumotlar 30.10.2026 gacha)",
        "usd_day": "{d} dagi dollar kursi",
        "usd_period": "{p} dollar kursi (kunlar bo‘yicha o‘rtacha, minimal va maksimal)",
        "usd_year": "{y}-yil uchun", "usd_month": "{m} uchun",
        "usd_extreme": "{p} {e} dollar kursi va uning sanasi (sanalari)", "usd_min": "minimal",
        "usd_max": "maksimal",
        "category": "toifa: {v}", "industry": "sanoat tarmog‘i: {v}", "product_group": "tovar guruhi: {v}",
        "delivery_basis": "yetkazib berish bazisi: {v}", "packaging": "qadoq: {v}", "hs_code": "TN VED kodi: {v}",
        "product_variant": "reyestrdagi tovar nomi: {v}",
        "buyer_region": "xaridor hududi (ishtirokchilar reyestri bo‘yicha): {v}",
        "seller_region": "sotuvchi hududi (ishtirokchilar reyestri bo‘yicha): {v}",
        "buyer_activity": "xaridor faoliyat turi: {v}", "seller_activity": "sotuvchi faoliyat turi: {v}",
        "unit": "o‘lchov birligi: {v}",
        "order_asc": "o‘sish tartibida (eng kichik qiymatlar)",
        "share": "{c} bo‘lgan bitimlar ulushi — tanlovdagi barcha bitimlardan, holat berilmagan bo‘lsa bekor "
                 "qilinganlar ham",
        "share_field_status": "holati: {v}",
        "compare": "davrlar taqqoslanadi: {p1} va {p2}; birinchi davrga nisbatan o‘zgarish, %",
        "price_both": "o‘rtacha narx ikki usulda: hajm bo‘yicha tortilgan (summa ÷ hajm, 1 kg uchun so‘m) va alohida "
                      "bitimlar narxlarining oddiy o‘rtachasi (1 tonna uchun so‘m); faqat tonna va kilogrammdagi tovarlar",
        "m_avg_price_uzs_per_kg": "hajm bo‘yicha tortilgan o‘rtacha narx (summa ÷ hajm), 1 kg uchun so‘m",
        "m_mean_deal_price_uzs_per_t": "alohida bitimlar narxlarining oddiy o‘rtachasi, 1 tonna uchun so‘m",
        "m_median_price_uzs_per_kg": "alohida bitimlar narxlarining medianasi, 1 kg uchun so‘m",
        "m_min_unit_price_uzs": "o‘lchov birligi uchun bitim narxi so‘mda, birliklar bo‘yicha",
        "m_max_unit_price_uzs": "o‘lchov birligi uchun bitim narxi so‘mda, birliklar bo‘yicha",
        "m_avg_price_change_pct": "bitim narxining boshlang‘ich narxdan o‘rtacha farqi, %",
        "m_avg_days_to_payment": "bitimdan to‘lovgacha o‘rtacha kunlar (to‘langan bitimlar)",
        "m_avg_days_to_delivery": "to‘lovdan yetkazib berishgacha o‘rtacha kunlar (yetkazilgan bitimlar)",
        "m_avg_delivery_delay_days": "yetkazib berishning o‘rtacha kechikishi, kun — faqat muddatidan kech "
                                     "yetkazilganlar",
        "m_late_delivery_count": "muddatidan kech yetkazib berish (to‘lov sanasi + yetkazish muddati)",
        "m_late_payment_count": "to‘lov kechikishi: muddatidan keyin to‘langan yoki to‘lanmagan va muddat "
                                "30.10.2026 gacha o‘tgan; barcha holatlar",
        "m_undelivered_lots": "yetkazilmagan lotlar = sotib olingan − yetkazilgan",
        "m_delivered_volume": "yetkazilgan hajm = yetkazilgan lotlar × lot, birliklar bo‘yicha",
        "m_undelivered_volume": "yetkazilmagan hajm = (sotib olingan − yetkazilgan lotlar) × lot, birliklar bo‘yicha",
        "m_distinct_brokers": "bitimning istalgan tomonidagi turli brokerlar soni",
        "m_distinct_buyers": "turli xaridorlar soni", "m_distinct_sellers": "turli sotuvchilar soni",
        "m_distinct_products": "turli tovarlar soni", "m_distinct_regions": "turli hududlar soni",
        "m_distinct_seller_brokers": "turli sotuvchi brokerlari soni",
        "m_distinct_buyer_brokers": "turli xaridor brokerlari soni",
        "reg_participants": "reyestr ishtirokchilari", "reg_brokers": "reyestr brokerlari",
        "reg_contracts": "shartnomalar", "reg_activity": "faoliyat turi: {v}", "reg_role": "roli: {v}",
        "reg_region": "hudud: {v}", "reg_vat": "QQS to‘lovchi: {v}", "yes": "ha", "no": "yo‘q",
        "reg_active_on": "{v} da amalda", "reg_year": "{v}-yilda ro‘yxatdan o‘tgan",
    },
}

# Compiler note codes (app/compiler/sql.py) that are rendered as fixed phrases.
_NOTE_KEYS = ("excluded_cancelled", "all_statuses", "status_split", "uzs_converted", "usd_converted",
              "broker_both_sides", "role_split", "volume_by_unit", "broker_side_split")
# Status phrases describe the row set; a cancelled_pct-only query always runs over all deals.
_STATUS_NOTES = ("excluded_cancelled", "all_statuses", "status_split")
# list filter -> phrase key
_LIST_PHRASES = (("product", "products"), ("product_variant", "product_variants"), ("region", "regions"),
                 ("buyer", "buyer_inns"), ("seller", "seller_inns"), ("participant", "participant_inns"),
                 ("broker", "brokers"), ("seller_broker", "seller_brokers"), ("buyer_broker", "buyer_brokers"),
                 ("status", "statuses"), ("category", "categories"), ("industry", "industries"),
                 ("product_group", "product_groups"), ("delivery_basis", "delivery_bases"),
                 ("packaging", "packagings"), ("hs_code", "hs_codes"), ("buyer_region", "buyer_regions"),
                 ("seller_region", "seller_regions"), ("buyer_activity", "buyer_activities"),
                 ("seller_activity", "seller_activities"), ("unit", "units"))
_SHARE_KEYS = {"contract_form": "contract_form", "contract_type": "contract_type", "trade_type": "trade_type",
               "status": "share_field_status"}


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


def _usd(spec: UsdRate, t: dict) -> str:
    if spec.date is not None:
        return t["usd_day"].format(d=spec.date.strftime("%d.%m.%Y"))
    p = (t["usd_month"].format(m=f"{spec.month:02d}.{spec.year}") if spec.month is not None
         else t["usd_year"].format(y=spec.year))
    if spec.extreme is not None:
        return t["usd_extreme"].format(e=t[f"usd_{spec.extreme}"], p=p)
    return t["usd_period"].format(p=p)


def _registry(spec: RegistryCount, t: dict) -> str:
    parts = [t[f"reg_{spec.entity}"]]
    if spec.activity:
        parts.append(t["reg_activity"].format(v=spec.activity))
    if spec.role:
        parts.append(t["reg_role"].format(v=spec.role))
    if spec.products:
        parts.append(t["product"].format(v=", ".join(spec.products)))
    if spec.contract_form:
        parts.append(t["contract_form"].format(v=spec.contract_form))
    if spec.region:
        parts.append(t["reg_region"].format(v=spec.region))
    if spec.vat_payer is not None:
        parts.append(t["reg_vat"].format(v=t["yes"] if spec.vat_payer else t["no"]))
    if spec.seller_inn:
        parts.append(t["seller"].format(v=spec.seller_inn))
    if spec.active_on:
        parts.append(t["reg_active_on"].format(v=spec.active_on.strftime("%d.%m.%Y")))
    if spec.registered_year is not None:
        parts.append(t["reg_year"].format(v=spec.registered_year))
    return "; ".join(parts)


def _measure_phrases(spec: DealsAggregate, t: dict) -> list[str]:
    ms = spec.measures
    out = []
    if "avg_price_uzs_per_kg" in ms and "mean_deal_price_uzs_per_t" in ms:
        out.append(t["price_both"])
        ms = [m for m in ms if m not in ("avg_price_uzs_per_kg", "mean_deal_price_uzs_per_t")]
    for m in ms:
        phrase = t.get(f"m_{m}")
        if phrase and phrase not in out:
            out.append(phrase)
    if spec.share_of is not None:
        key = _SHARE_KEYS[spec.share_of.field]
        out.append(t["share"].format(c=t[key].format(v=", ".join(spec.share_of.values))))
    return out


def explain(spec: Spec, notes: list[str], lang: str) -> str:
    """Return one sentence describing the computation; empty for lookups.

    Unknown languages fall back to Russian.
    """
    t = _T["uz"] if lang in ("uz", "uz-Cyrl") else _T["ru"]
    if isinstance(spec, UsdRate):
        return f"{t['prefix']} {_usd(spec, t)}."
    if isinstance(spec, RegistryCount):
        return f"{t['prefix']} {_registry(spec, t)}."
    if not isinstance(spec, DealsAggregate):
        return ""
    f = spec.filters
    if spec.compare_periods:
        p1, p2 = (_period_text(Filters(year=p.year, month_from=p.month_from, month_to=p.month_to), t)
                  for p in spec.compare_periods)
        parts = [t["deals"], t["compare"].format(p1=p1, p2=p2)]
    else:
        parts = [f"{t['deals']} {_period(f, t)}"]
    for key, attr in _LIST_PHRASES:
        values = getattr(f, attr)
        if values:
            parts.append(t[key].format(v=", ".join(values)))
    for key, value in (("family", f.product_family), ("contract", f.contract_id),
                       ("contract_type", f.contract_type), ("contract_form", f.contract_form),
                       ("trade_type", f.trade_type)):
        if value is not None:
            parts.append(t[key].format(v=value))
    parts += _measure_phrases(spec, t)
    scope_free_only = all(m in SCOPE_FREE for m in spec.measures)
    for note in notes:
        if note.startswith("ranked_top_"):
            parts.append(t["ranked_top"].format(n=note.removeprefix("ranked_top_")))
        elif note == "order_asc":
            parts.append(t["order_asc"])
        elif note not in _NOTE_KEYS:
            continue
        elif scope_free_only and note in _STATUS_NOTES:
            continue
        elif note == "all_statuses" and f.statuses:
            continue  # the status filter phrase already describes the scope
        elif note == "volume_by_unit" and not any(m in ("volume", "delivered_volume", "undelivered_volume")
                                                  for m in spec.measures):
            continue  # unit-price measures say "by unit" themselves
        else:
            parts.append(t[note])
    if "cancelled_pct" in spec.measures:
        parts.append(t["cancelled_share"])
    return f"{t['prefix']} " + "; ".join(parts) + "."
