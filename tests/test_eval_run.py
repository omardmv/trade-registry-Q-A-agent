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
