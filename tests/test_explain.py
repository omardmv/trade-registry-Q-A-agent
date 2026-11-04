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
