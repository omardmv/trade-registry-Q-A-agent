import pytest

from app import config
from app.agent.guard import build_index, mentioned_values, missing_condition_error
from app.catalog import load_catalog
from app.compiler.spec import parse_spec
from app.examples import ExampleStore


@pytest.fixture(scope="module")
def index(db):
    return build_index(load_catalog(db))


def err(index, question, spec, names=()):
    return missing_condition_error(question, parse_spec(spec), index, names)


AGG = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2024}}


def test_registry_activity_dropped_is_rejected(index):
    q = "Сколько компаний с видом деятельности «Транспортная компания»?"
    e = err(index, q, {"kind": "registry_count", "entity": "participants"})
    assert "Транспортная компания" in e and "activity" in e and "declare_unanswerable" in e
    assert err(index, q, {"kind": "registry_count", "entity": "participants",
                          "activity": "Транспортная компания"}) is None


def test_inflected_contract_form(index):
    q = "Сколько форвардных сделок было в 2024 году?"
    assert "Форвард" in err(index, q, AGG)
    assert err(index, q, {**AGG, "filters": {"year": 2024, "contract_form": "Форвард"}}) is None
    assert err(index, q, {**AGG, "group_by": "contract_form"}) is None


def test_all_forms_named_means_no_form_filter(index):
    q = "Сколько продано хлопка-сырца во всех формах (спот, форвард, фьючерс) в 2024?"
    assert err(index, q, {**AGG, "filters": {"year": 2024, "products": ["Хлопок-сырец"]}}) is None


def test_category_and_uzbek_export(index):
    assert "Нефтепродукты" in err(index, "Сколько сделок с нефтепродуктами в 2024?", AGG)
    assert "Экспортный" in err(index, "2024 yilda nechta eksport bitimi bo'lgan?", AGG)


def test_complement_filter_on_same_field_counts(index):
    q = "Сколько сделок кроме экспортных было в 2024?"
    assert err(index, q, {**AGG, "filters": {"year": 2024, "contract_type": "Внутренний"}}) is None


def test_company_name_words_are_not_conditions(index):
    q = "Сколько сделок у компании Mo'jotat Klaster в 2024?"
    assert mentioned_values(q, index) and err(index, q, AGG) is not None
    assert err(index, q, AGG, names=["\"Mo'jotat Klaster\" MChJ"]) is None


def test_unit_word_with_volume_measure_is_output_unit(index):
    q = "Сколько тонн карбамида продано в 2024?"
    assert err(index, q, {"kind": "deals_aggregate", "measures": ["volume_tonnes"], "filters": {"year": 2024}}) is None
    q2 = "Сколько сделок с товарами в литрах было в 2024?"
    assert "литр" in err(index, q2, AGG)
    assert err(index, q2, {**AGG, "filters": {"year": 2024, "units": ["литр"]}}) is None


def test_metr_inside_kub_metr_not_reported_separately(index):
    vals = {v.value for v in mentioned_values("сколько куб.метр леса продано", index)}
    assert "куб.метр" in vals and "метр" not in vals


def test_generic_status_words_ignored(index):
    assert err(index, "Сколько сделок было заключено в 2024 году? Сколько новых?", AGG) is None


@pytest.mark.parametrize("q", ["Какой продавец продал меньше всего цемента в 2024?",
                               "Кто самый дешёвый продавец цемента?", "eng arzon sotgan sotuvchi kim?",
                               "Сделка с наименьшей суммой в 2024?"])
def test_least_requires_asc(index, q):
    grouped = {"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "seller", "filters": {"year": 2024}}
    assert "asc" in err(index, q, grouped)
    assert err(index, q, {**grouped, "order": "asc"}) is None
    assert err(index, q, {"kind": "deals_aggregate", "measures": ["amount_uzs"], "filters": {"year": 2024}}) is None


@pytest.mark.parametrize("q", ["Сколько разных покупателей было в 2024?", "Сколько уникальных товаров продано?",
                               "2024 yilda nechta turli xaridor bo'lgan?", "nechta xil tovar sotilgan?"])
def test_distinct_requires_distinct_measure(index, q):
    assert "distinct" in err(index, q, AGG)
    assert err(index, q, {**AGG, "measures": ["distinct_buyers"]}) is None


@pytest.mark.parametrize("q", ["Какая доля сделок по дизелю в 2024?", "Какой процент сделок по дизелю?",
                               "dizel bitimlari ulushi necha foiz?", "Сколько % сделок по дизелю?"])
def test_share_requires_share_measure(index, q):
    assert "share_pct" in err(index, q, AGG)
    ok = {**AGG, "measures": ["share_pct"], "share_of": {"field": "status", "value": "Исполнен"}}
    assert err(index, q, ok) is None
    assert err(index, q, {**AGG, "measures": ["cancelled_pct"]}) is None


def test_percent_change_satisfied_by_compare_periods(index):
    q = "На сколько процентов изменилось число сделок в 2025 по сравнению с 2024?"
    assert err(index, q, {"kind": "deals_aggregate", "measures": ["deal_count"],
                          "compare_periods": [{"year": 2024}, {"year": 2025}]}) is None


def test_every_example_passes_the_guard(index):
    for ex in ExampleStore.from_yaml(config.EXAMPLES_PATH).examples:
        if ex.spec is not None:
            assert err(index, ex.question, ex.spec) is None, ex.question


@pytest.mark.parametrize("q", ["Когда была самая дешёвая сделка по цементу?", "Дизель бўйича неча фоиз битим?"])
def test_more_cue_spellings(index, q):
    grouped = {"kind": "deals_aggregate", "measures": ["mean_deal_price_uzs_per_t"], "group_by": "deal"}
    assert err(index, q, grouped) is not None


def test_broker_side_is_not_a_ranking(index):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"], "group_by": "broker_side",
            "filters": {"brokers": ["BK045"]}}
    assert err(index, "Минимально сколько сделок у BK045 как брокера продавца и покупателя?", spec) is None


@pytest.mark.parametrize("q,family", [("Сколько сделок с химической продукцией в 2024?", "Химическая продукция"),
                                       ("Сколько строительных материалов продано в 2024?", "Строительные материалы"),
                                       ("Сколько сделок с нефтепродуктами в 2024?", "Нефтепродукты"),
                                       ("Сколько сделок по металлопродукции в 2024?", "Металлопродукция"),
                                       ("Сколько сделок по машиностроению в 2024?", "Машиностроение")])
def test_product_family_covers_same_named_category(index, q, family):
    spec = {**AGG, "filters": {"year": 2024, "product_family": family}}
    assert err(index, q, spec, names=[family]) is None
    assert err(index, q, spec) is None


def test_category_word_requires_category_filter(index):
    q = "Сколько сделок в категории «Нефтепродукты» в 2024?"
    assert "categories" in err(index, q, {**AGG, "filters": {"year": 2024, "product_family": "Нефтепродукты"}},
                               names=["Нефтепродукты"])


def test_strip_names_tolerates_inflection(index):
    q = "Сколько сделок у компании Транспорт Кластера в 2024?"
    assert err(index, q, AGG, names=["Transport Klaster MChJ"]) is None


@pytest.mark.parametrize("q,value", [
    ("2024 yilda qurilish materiallari bo'yicha nechta bitim bo'lgan?", "Строительные материалы"),
    ("Nechta transport kompaniyasi ro'yxatdan o'tgan?", "Транспортная компания"),
    ("Нечта транспорт компанияси бор?", "Транспортная компания"),
    ("oziq-ovqat sanoati bitimlari soni 2024 yilda?", "Пищевая промышленность"),
    ("qoplarda sotilgan tovarlar bitimlari soni?", "в мешках"),
    ("Ingliz auksionida nechta bitim tuzilgan?", "Английский аукцион"),
    ("Сколько спотовых сделок было в 2024?", "Спот"),
    ("Сколько компаний-производителей в реестре?", "Производитель"),
    ("Сколько сделок со статусом «Оплачен» в 2024?", "Оплачен"),
])
def test_synonyms_and_short_forms_detected(index, q, value):
    assert value in {v.value for v in mentioned_values(q, index)}, q


def test_generic_words_without_context_still_ignored(index):
    assert err(index, "Кто крупнейший производитель цемента в 2024?",
               {"kind": "deals_aggregate", "measures": ["amount_uzs"], "group_by": "seller",
                "filters": {"year": 2024}}) is None
    assert err(index, "Сколько сделок оплачено в 2024 году?", AGG) is None


@pytest.mark.parametrize("q", ["Минимальная и максимальная цена по каждому товару в 2024?",
                               "eng past va eng yuqori narx har bir tovar bo'yicha"])
def test_least_cue_skipped_for_each_and_min_max(index, q):
    spec = {"kind": "deals_aggregate", "measures": ["min_unit_price_uzs", "max_unit_price_uzs"], "group_by": "product",
            "top": 20, "filters": {"year": 2024}}
    assert err(index, q, spec) is None
    assert err(index, "Минимальная цена у какого продавца?", {**spec, "measures": ["min_unit_price_uzs"]}) is None


def test_named_value_requires_that_value(index):
    q = "Сколько форвардных сделок было в 2024 году?"
    assert "Форвард" in err(index, q, {**AGG, "filters": {"year": 2024, "contract_form": "Спот"}})
    # two values of a single-valued field named: setting one of them is a valid reading
    q2 = "Сколько сделок по хлопку-сырцу (спот) в форме «Форвард»?"
    assert err(index, q2, {**AGG, "filters": {"products": ["Хлопок-сырец"], "contract_form": "Форвард"}}) is None


@pytest.mark.parametrize("q", ["Какая доля экспорта в обороте 2024 года?", "eksportning summadagi ulushi necha foiz?",
                               "Какой процент объёма в тоннах приходится на форварды?"])
def test_share_of_money_or_volume_needs_basis(index, q):
    spec = {**AGG, "measures": ["share_pct"], "share_of": {"field": "contract_type", "value": "Экспортный"}}
    if "форвард" in q:
        spec["share_of"] = {"field": "contract_form", "value": "Форвард"}
    assert "basis" in err(index, q, spec)
    basis = "volume_t" if "тонн" in q else "amount_uzs"
    assert err(index, q, {**spec, "share_of": {**spec["share_of"], "basis": basis}}) is None


def test_share_on_registry_rejected(index):
    assert "declare_unanswerable" in err(index, "Какой процент компаний-продавцов не платит НДС?",
                                         {"kind": "registry_count", "entity": "participants", "role": "Продавец",
                                          "vat_payer": False})
