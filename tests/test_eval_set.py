import yaml

from app import config


def load():
    with open(config.ROOT / "eval" / "questions.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_eval_set_shape():
    qs = load()
    assert len(qs) == 60
    assert len({q["id"] for q in qs}) == 60
    assert sum(not q["answerable"] for q in qs) == 12
    assert {q["lang"] for q in qs} == {"ru", "uz"}


def test_gold_sql_runs_and_has_key_columns(db):
    for q in load():
        if not q["answerable"]:
            continue
        res = db.query(q["gold_sql"])
        assert res.rows, q["id"]
        assert set(q["key_columns"]) <= set(res.columns), q["id"]
        assert all(dict(zip(res.columns, r))[k] is not None for r in res.rows for k in q["key_columns"]), q["id"]
