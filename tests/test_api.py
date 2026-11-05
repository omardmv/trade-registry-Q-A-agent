from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import api
from app.api import app, get_pipeline


@pytest.fixture(autouse=True)
def empty_answer_cache():
    api._CACHE.clear()
    yield
    api._CACHE.clear()
from app.pipeline import AskResult


class FakePipeline:
    def __init__(self):
        self.calls = []

    def ask(self, question, previous=None):
        self.calls.append((question, previous))
        return AskResult("Заключено 9 912 сделок.", "ru", True, "llm", sql="SELECT 1", columns=["n"],
                         rows=[[9912]], row_count=1)


class CrashingPipeline:
    def ask(self, question, previous=None):
        raise RuntimeError("boom: secret internals")


def client(fake, raise_server_exceptions=True):
    app.dependency_overrides[get_pipeline] = lambda: fake
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_ask_returns_answer_and_evidence():
    fake = FakePipeline()
    r = client(fake).post("/ask", json={"question": "Сколько сделок?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Заключено 9 912 сделок." and body["rows"] == [[9912]] and body["sql"] == "SELECT 1"
    assert fake.calls == [("Сколько сделок?", None)]


def test_previous_is_passed():
    fake = FakePipeline()
    client(fake).post("/ask", json={"question": "А в 2024?", "previous": {"question": "Сколько?", "sql": "SELECT 1"}})
    assert fake.calls[0][1] == {"question": "Сколько?", "sql": "SELECT 1"}


def test_validation():
    c = client(FakePipeline())
    assert c.post("/ask", json={"question": ""}).status_code == 422
    assert c.post("/ask", json={"question": "x" * 1001}).status_code == 422


def test_health_and_ui():
    c = client(FakePipeline())
    assert c.get("/health").json() == {"status": "ok"}
    page = c.get("/")
    assert page.status_code == 200 and "<form" in page.text


def test_response_with_date_string_float_and_none_is_serializable():
    class JsonRowsPipeline:
        def ask(self, question, previous=None):
            return AskResult("ok", "ru", True, "llm", sql="SELECT 1", columns=["d", "f", "n"],
                             rows=[[date(2025, 10, 1).isoformat(), 1.5, None]], row_count=1)

    r = client(JsonRowsPipeline()).post("/ask", json={"question": "Q?"})
    assert r.status_code == 200
    assert r.json()["rows"] == [["2025-10-01", 1.5, None]]


def test_pipeline_exception_returns_generic_500(caplog):
    r = client(CrashingPipeline(), raise_server_exceptions=False).post("/ask", json={"question": "Q?"})
    assert r.status_code == 500
    assert r.json() == {"detail": "internal error"}
    assert "Traceback" not in r.text and "boom" not in r.text


def test_internal_fields_not_exposed():
    class Leaky:
        def ask(self, question, previous=None):
            return AskResult("В данных нет ответа.", "ru", False, "failed", reason="llm error: secret internals",
                             unsupported=["12345"], trace=[{"tool": "compile_query", "args": {}}])

    body = client(Leaky()).post("/ask", json={"question": "Q?"}).json()
    assert "reason" not in body and "unsupported" not in body and "trace" not in body
    assert "secret" not in str(body) and body["answer"] == "В данных нет ответа."


def test_repeated_question_is_answered_from_the_cache():
    fake = FakePipeline()
    c = client(fake)
    first = c.post("/ask", json={"question": "Сколько  сделок?"}).json()
    second = c.post("/ask", json={"question": "сколько сделок?"}).json()
    assert len(fake.calls) == 1 and second["answer"] == first["answer"] and second["cached"] is True
    c.post("/ask", json={"question": "сколько сделок?", "previous": {"question": "x", "sql": "SELECT 1"}})
    assert len(fake.calls) == 2      # a follow-up with context is never served from the cache
