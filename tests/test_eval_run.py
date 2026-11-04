from app.pipeline import AskResult
from eval.run import evaluate, value_in_rows, value_in_text


def test_value_in_rows():
    assert value_in_rows(120, [[120.0]])
    assert value_in_rows(1000000, [[1000000.5]])
    assert not value_in_rows(120, [[121]])
    assert value_in_rows("BK001", [["BK001", 3]])
    assert not value_in_rows("BK001", [[None, "BK002"]])
    assert not value_in_rows(1, [[True]])


def test_value_in_text():
    assert value_in_text(5854, "Сделок в статусе «Оплачен»: 5 854.")
    assert value_in_text(1844729.92, "Продано около 1,84 млн тонн.")
    assert not value_in_text(5854, "Сделок было 5 000.")
    assert value_in_text("Хлопок-сырец", "Больше всего принёс хлопок-сырец.")
    assert not value_in_text("Карбамид", "Хлопок-сырец")


class Stub:
    def __init__(self, results):
        self.results = results

    def ask(self, question):
        r = self.results[question]
        if isinstance(r, Exception):
            raise r
        return r


class FakeDb:
    def query(self, sql):
        class R:
            columns = ["paid"]
            rows = [[5854]]
        return R()


def _q(i, answerable, question):
    q = {"id": i, "lang": "ru", "question": question, "answerable": answerable}
    if answerable:
        q.update(gold_sql="x", key_columns=["paid"])
    return q


def test_evaluate_scoring():
    pipe = Stub({
        "good": AskResult("Оплачено 5 854 сделки.", "ru", True, "compiled", columns=["paid"], rows=[[5854]]),
        "bad": AskResult("Оплачено 7 000 сделок.", "ru", True, "agent", columns=["paid"], rows=[[7000]], verified=False),
        "refuse": AskResult("В данных нет ответа на этот вопрос.", "ru", False, "no_data"),
        "invent": AskResult("Их было 12.", "ru", True, "agent"),
        "boom": RuntimeError("kaput"),
    })
    db = FakeDb()
    good = evaluate(_q("a", True, "good"), pipe, db)
    assert (good["sql_ok"], good["answer_ok"], good["hallucination"]) == (True, True, False)
    bad = evaluate(_q("b", True, "bad"), pipe, db)
    assert (bad["sql_ok"], bad["answer_ok"], bad["hallucination"]) == (False, False, True)
    refuse = evaluate(_q("c", False, "refuse"), pipe, db)
    assert (refuse["sql_ok"], refuse["answer_ok"], refuse["hallucination"]) == (None, True, False)
    invent = evaluate(_q("d", False, "invent"), pipe, db)
    assert (invent["answer_ok"], invent["hallucination"]) == (False, True)
    boom = evaluate(_q("e", True, "boom"), pipe, db)
    assert (boom["answer_ok"], boom["hallucination"]) == (False, False)
    assert "kaput" in boom["error"]


def test_inflected_string_match():
    assert value_in_text("Хлопок-сырец", "больше всего хлопка-сырца")
    assert not value_in_text("Хлопок-сырец", "больше всего карбамида")
    assert not value_in_text("Карбамид", "Хлопок-сырец")


def _ranked(rows, cols=("ranking", "buyer_inn")):
    return AskResult("ответ", "ru", True, "compiled", columns=list(cols), rows=rows)


class OneColDb:
    def __init__(self, rows, cols=("broker_code",), fail=False):
        self.rows, self.cols, self.fail = rows, cols, fail

    def query(self, sql):
        if self.fail:
            raise ValueError("bad sql")
        outer = self

        class R:
            columns = list(outer.cols)
            rows = outer.rows
        return R()


def _tq(i, keys, top1=True):
    return {"id": i, "lang": "ru", "question": i, "answerable": True, "gold_sql": "x", "key_columns": keys, "top1": top1}


def test_top1_first_row_of_partition():
    db = OneColDb([("BK1",)])
    ok = evaluate(_tq("t", ["broker_code"]), Stub({"t": _ranked([["by_amount", "BK1"], ["by_amount", "BK2"]])}), db)
    assert ok["sql_ok"] is True
    # at row 2 of its only partition -> rejected
    bad = evaluate(_tq("t", ["broker_code"]), Stub({"t": _ranked([["by_amount", "BK2"], ["by_amount", "BK1"]])}), db)
    assert bad["sql_ok"] is False
    # non-top1 keeps the any-cell rule
    anyc = evaluate(_tq("t", ["broker_code"], top1=False), Stub({"t": _ranked([["x", "BK2"], ["x", "BK1"]])}), db)
    assert anyc["sql_ok"] is True


def test_top1_two_row_gold_requires_both():
    db = OneColDb([("BK1",), ("BK2",)])
    both = evaluate(_tq("t", ["broker_code"]),
                    Stub({"t": _ranked([["tonnes", "BK1"], ["tonnes", "BKx"], ["amount", "BK2"], ["amount", "BKy"]])}), db)
    assert both["sql_ok"] is True
    one = evaluate(_tq("t", ["broker_code"]), Stub({"t": _ranked([["tonnes", "BK1"], ["tonnes", "BK3"]])}), db)
    assert one["sql_ok"] is False


def test_answered_wrong_counted_and_gold_error(monkeypatch, tmp_path, capsys):
    import json
    import eval.run as run

    qs = [_q("a", True, "good"), _q("b", True, "wrong"), _q("c", False, "refuse")]
    pipe = Stub({"good": AskResult("5 854", "ru", True, "compiled", columns=["paid"], rows=[[5854]]),
                 "wrong": AskResult("Оплачено 7 000.", "ru", True, "compiled", columns=["paid"], rows=[[7000]]),
                 "refuse": AskResult("нет", "ru", False, "no_data")})
    monkeypatch.setattr(run, "build_pipeline", lambda: pipe)
    monkeypatch.setattr(run, "Database", lambda path: FakeDb())
    monkeypatch.setattr(run.yaml, "safe_load", lambda f: qs)
    monkeypatch.setattr(run.config, "ROOT", tmp_path)
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "questions.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["eval.run"])
    assert run.main() == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index("{"):])
    assert summary["answered_wrong"] == 1 and summary["hallucinations"] == 0


def test_gold_sql_error_recorded():
    pipe = Stub({"good": AskResult("5 854", "ru", True, "compiled", columns=["paid"], rows=[[5854]])})
    r = evaluate(_q("a", True, "good"), pipe, OneColDb([], fail=True))
    assert "gold SQL failed" in r["error"] and r["answer_ok"] is False
