from app import config
from app.compiler.spec import parse_spec
from app.compiler.sql import compile_spec
from app.examples import ExampleStore
from app.semantic import load_semantic, render_schema_prompt


def test_schema_prompt_mentions_every_table_and_rule():
    text = render_schema_prompt(load_semantic())
    for table in ["deals", "contracts", "participants", "brokers", "usd_rates"]:
        assert f"## {table}" in text
    assert "is_cancelled" in text and "amount_uzs" in text
    assert "status_scope" in text and "product_scope" in text


def test_schema_prompt_columns_exist(db):
    sem = load_semantic()
    for table, info in sem["tables"].items():
        actual = set(db.query(f"SELECT * FROM {table} LIMIT 0").columns)
        assert set(info["columns"]) <= actual, table


def test_every_example_spec_parses():
    store = ExampleStore.from_yaml(config.EXAMPLES_PATH)
    assert len(store.examples) >= 20
    for ex in store.examples:
        if ex.spec is not None:
            parse_spec(ex.spec)
        else:
            assert ex.sql or ex.action == "declare_unanswerable"


def test_example_sql_runs(db):
    for ex in ExampleStore.from_yaml(config.EXAMPLES_PATH).examples:
        if ex.sql:
            db.explain(ex.sql)


def test_search_finds_similar_question():
    store = ExampleStore.from_yaml(config.EXAMPLES_PATH)
    top = store.search("Сколько сделок было в мае 2023 года?", k=1)[0]
    assert top.spec["measures"] == ["deal_count"]
    top_uz = store.search("paxtani eng ko‘p sotib olgan kim?", k=1)[0]
    assert top_uz.spec["group_by"] == "buyer"


ANSWERABLE_COUNT_QUESTIONS = [
    "Сколько сделок было в 2022 году?",
    "Сколько сделок было в январе 2022 года?",
    "Сколько сделок было в 2026 году?",
    "Сколько сделок было в мае 2023 года?",
    "2025-yilda nechta bitim tuzilgan?",
]


def test_in_range_count_questions_retrieve_answerable_example():
    store = ExampleStore.from_yaml(config.EXAMPLES_PATH)
    for question in ANSWERABLE_COUNT_QUESTIONS:
        top = store.search(question, k=1)[0]
        assert top.action != "declare_unanswerable", question
        assert top.spec is not None, question


def test_every_example_spec_compiles_and_explains(db):
    store = ExampleStore.from_yaml(config.EXAMPLES_PATH)
    for ex in store.examples:
        if ex.spec is not None:
            compiled = compile_spec(parse_spec(ex.spec))
            db.explain(compiled.sql)
