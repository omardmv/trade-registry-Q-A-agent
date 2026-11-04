import pytest

from app.compiler.explain import explain
from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec


def _explain(d, lang):
    spec = parse_spec(d)
    return explain(spec, compile_spec(spec).notes, lang)


def test_ru_count_split():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"year": 2025, "month_from": 10}}, "ru")
    assert text.startswith("Расчёт:")
    assert "10.2025" in text
    assert "все сделки и отдельно без аннулированных" in text


def test_uz_ranking_with_product():
    text = _explain({"kind": "deals_aggregate", "measures": ["volume_tonnes"], "group_by": "buyer",
                     "filters": {"products": ["Хлопок-сырец"]}}, "uz")
    assert text.startswith("Hisob:")
    assert "Хлопок-сырец" in text
    assert "bekor qilingan bitimlar hisobga olinmagan" in text
    assert "eng yaxshi 3 ta" in text


def test_currency_note():
    text = _explain({"kind": "deals_aggregate", "measures": ["amount_usd"], "status_scope": "valid"}, "ru")
    assert "по курсу на дату сделки" in text


def test_lookup_has_no_explanation():
    assert _explain({"kind": "deal_lookup", "deal_id": 1}, "ru") == ""


QUAL_RU = "доля аннулированных — от всех сделок"
QUAL_UZ = "bekor qilinganlar ulushi — barcha bitimlardan"


def test_cancelled_only_skips_status_phrases():
    text = _explain({"kind": "deals_aggregate", "measures": ["cancelled_pct"],
                     "filters": {"year": 2025}}, "ru")
    assert QUAL_RU in text
    assert "отдельно без аннулированных" not in text
    assert "без аннулированных" not in text
    assert "все состояния сделок" not in text


def test_cancelled_only_uz_qualifier():
    text = _explain({"kind": "deals_aggregate", "measures": ["cancelled_pct"],
                     "filters": {"year": 2025}}, "uz")
    assert QUAL_UZ in text
    assert "alohida bekor qilinganlarsiz" not in text


def test_cancelled_among_several_keeps_notes_and_adds_qualifier():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count", "cancelled_pct"],
                     "filters": {"year": 2025}}, "ru")
    assert QUAL_RU in text
    assert "все сделки и отдельно без аннулированных" in text


def test_status_filter_skips_all_statuses():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"statuses": ["Заключен"]}}, "ru")
    assert "состояние: Заключен" in text
    assert "все состояния сделок" not in text


def test_unknown_lang_falls_back_to_ru():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"year": 2025}}, "en")
    assert text.startswith("Расчёт:")


def test_uz_cyrl_returns_latin_uzbek():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"year": 2025}}, "uz-Cyrl")
    assert text.startswith("Hisob:")


def test_month_range_ru():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"year": 2025, "month_from": 4, "month_to": 6}}, "ru")
    assert "04.2025–06.2025" in text


def test_date_range_ru():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "filters": {"date_from": "2024-01-01", "date_to": "2024-03-31"}}, "ru")
    assert "с 01.01.2024 по 31.03.2024" in text


def test_no_period_ru():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"]}, "ru")
    assert "за весь период (03.01.2022–30.10.2026)" in text


def test_participant_role_split_ru():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "participant",
                     "filters": {"year": 2025}}, "ru")
    assert "отдельно как покупатель и как продавец" in text


def test_period_past_data_end_is_flagged():
    ru = _explain({"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2026}}, "ru")
    assert "(данные до 30.10.2026)" in ru
    uz = _explain({"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2026}}, "uz")
    assert "(ma’lumotlar 30.10.2026 gacha)" in uz
    m = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                  "filters": {"year": 2026, "month_from": 9, "month_to": 10}}, "ru")
    assert "(данные до 30.10.2026)" in m
    ok = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                   "filters": {"year": 2026, "month_from": 3, "month_to": 4}}, "ru")
    assert "данные до" not in ok
    assert "данные до" not in _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                                        "filters": {"year": 2025}}, "ru")


def test_usd_rate_explanations():
    from app.compiler.spec import parse_spec
    e = lambda d, lang: explain(parse_spec({"kind": "usd_rate", **d}), [], lang)
    assert e({"year": 2024, "month": 3}, "ru") == \
        "Расчёт: курс доллара за 03.2024 (среднее, минимум и максимум по дням)."
    assert e({"date": "2025-06-15"}, "ru") == "Расчёт: курс доллара на 15.06.2025."
    assert e({"year": 2024}, "ru") == \
        "Расчёт: курс доллара за 2024 год (среднее, минимум и максимум по дням)."
    assert e({"year": 2024, "month": 3}, "uz") == \
        "Hisob: 03.2024 uchun dollar kursi (kunlar bo‘yicha o‘rtacha, minimal va maksimal)."
    assert e({"date": "2025-06-15"}, "uz") == "Hisob: 15.06.2025 dagi dollar kursi."
    assert e({"year": 2024}, "uz") == \
        "Hisob: 2024-yil uchun dollar kursi (kunlar bo‘yicha o‘rtacha, minimal va maksimal)."


def test_contract_lookup_and_registry_have_no_explanation():
    from app.compiler.spec import parse_spec
    assert explain(parse_spec({"kind": "contract_lookup", "contract_id": 1}), [], "ru") == ""


def test_deal_ranking_renders_top_n():
    text = _explain({"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "deal", "top": 1,
                     "status_scope": "valid", "filters": {"year": 2023}}, "ru")
    assert "топ-1" in text


def test_new_filters_rendered_ru():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {
        "categories": ["Нефтепродукты"], "industries": ["Металлургия"], "product_groups": ["Прочие товары"],
        "delivery_bases": ["FCA"], "packagings": ["навалом"], "hs_codes": ["520100"],
        "product_variants": ["Сахар белый"], "buyer_regions": ["Buxoro viloyati"],
        "seller_regions": ["Toshkent shahri"], "buyer_activities": ["Кластер"],
        "seller_activities": ["Производитель"], "units": ["литр"]}}, "ru")
    for part in ("категория: Нефтепродукты", "отрасль: Металлургия", "группа товара: Прочие товары",
                 "базис поставки: FCA", "упаковка: навалом", "код ТН ВЭД: 520100",
                 "наименование товара в реестре: Сахар белый",
                 "регион покупателя (по реестру участников): Buxoro viloyati",
                 "регион продавца (по реестру участников): Toshkent shahri",
                 "вид деятельности покупателя: Кластер", "вид деятельности продавца: Производитель",
                 "единица измерения: литр"):
        assert part in text, part


def test_new_filters_rendered_uz():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {
        "categories": ["Нефтепродукты"], "buyer_regions": ["Buxoro viloyati"]}}, "uz")
    assert "toifa: Нефтепродукты" in text and "xaridor hududi (ishtirokchilar reyestri bo‘yicha): Buxoro viloyati" in text


def test_order_asc_rendered():
    text = _explain({"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "seller", "order": "asc"},
                    "ru")
    assert "по возрастанию (наименьшие значения)" in text
    assert "eng kichik" in _explain({"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "seller",
                                     "order": "asc"}, "uz")


def test_share_rendered():
    text = _explain({"kind": "deals_aggregate", "measures": ["share_pct"],
                     "share_of": {"field": "contract_form", "value": "Форвард"}, "filters": {"year": 2024}}, "ru")
    assert "доля сделок, где форма: Форвард, — от всех сделок выборки, включая аннулированные" in text
    assert "без аннулированных" not in text


def test_compare_periods_rendered():
    text = _explain({"kind": "deals_aggregate", "measures": ["deal_count"],
                     "compare_periods": [{"year": 2023}, {"year": 2024, "month_from": 1, "month_to": 3}]}, "ru")
    assert "сравнение периодов: за 2023 год и за 01.2024–03.2024" in text
    assert "изменение в % к первому периоду" in text
    assert "за весь период" not in text


def test_price_average_both_rendered():
    text = _explain({"kind": "deals_aggregate", "measures": ["avg_price_uzs_per_kg", "mean_deal_price_uzs_per_t"],
                     "status_scope": "valid", "filters": {"products": ["Портландцемент"]}}, "ru")
    assert "средняя цена двумя способами" in text
    uz = _explain({"kind": "deals_aggregate", "measures": ["avg_price_uzs_per_kg", "mean_deal_price_uzs_per_t"],
                   "status_scope": "valid"}, "uz")
    assert "o‘rtacha narx ikki usulda" in uz


@pytest.mark.parametrize("measure,phrase", [
    ("median_price_uzs_per_kg", "медиана"), ("min_unit_price_uzs", "за единицу измерения"),
    ("avg_price_change_pct", "от стартовой"), ("avg_days_to_payment", "от сделки до оплаты"),
    ("avg_days_to_delivery", "от оплаты до поставки"), ("avg_delivery_delay_days", "только поставки позже срока"),
    ("late_delivery_count", "позже срока"), ("late_payment_count", "срок оплаты истёк"),
    ("undelivered_lots", "не поставлено"), ("distinct_brokers", "с любой стороны"),
    ("distinct_regions", "различных"), ("mean_deal_price_uzs_per_t", "простое среднее")])
def test_measure_phrases(measure, phrase):
    assert phrase in _explain({"kind": "deals_aggregate", "measures": [measure], "status_scope": "valid"}, "ru")


def test_usd_extreme_rendered():
    from app.compiler.spec import parse_spec
    assert explain(parse_spec({"kind": "usd_rate", "year": 2024, "extreme": "max"}), [], "ru") == \
        "Расчёт: максимальный курс доллара за 2024 год и дата(ы), когда он был."
    assert explain(parse_spec({"kind": "usd_rate", "year": 2024, "month": 5, "extreme": "min"}), [], "uz") == \
        "Hisob: 05.2024 uchun minimal dollar kursi va uning sanasi (sanalari)."


def test_registry_count_rendered():
    from app.compiler.spec import parse_spec
    text = explain(parse_spec({"kind": "registry_count", "entity": "participants", "activity": "Кластер",
                               "role": "Продавец", "region": "Toshkent shahri", "vat_payer": True}), [], "ru")
    assert text == ("Расчёт: участники реестра; вид деятельности: Кластер; роль: Продавец; "
                    "регион: Toshkent shahri; плательщик НДС: да.")
    text = explain(parse_spec({"kind": "registry_count", "entity": "contracts", "products": ["Карбамид"],
                               "contract_form": "Спот", "seller_inn": "975923536", "active_on": "2024-05-01",
                               "registered_year": 2023}), [], "ru")
    assert text == ("Расчёт: контракты; товар: Карбамид; форма: Спот; продавец ИНН 975923536; "
                    "действует на 01.05.2024; зарегистрирован в 2023 году.")
