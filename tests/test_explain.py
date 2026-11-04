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
