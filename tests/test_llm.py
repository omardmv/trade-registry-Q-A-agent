import time

import pytest

from app.agent.llm import LLMError, ProviderError, call_with_retry, error_status, is_transient


def http(code):
    return ProviderError(f"HTTP {code}", transient=code == 429 or code >= 500, status=code)


def test_is_transient():
    assert is_transient(http(503))
    assert is_transient(http(429))
    assert is_transient(http(500))
    assert is_transient(TimeoutError())
    assert not is_transient(http(400))
    assert not is_transient(LLMError("blocked"))
    assert not is_transient(ValueError("x"))


def test_error_status():
    assert error_status(http(429)) == 429
    assert error_status(ValueError("x")) is None


def test_call_with_retry_once_when_time_fits():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise http(503)
        return "ok"

    assert call_with_retry(fn, time.monotonic() + 10, 1.0) == "ok" and len(calls) == 2


def test_call_with_retry_not_when_no_time_or_not_transient():
    calls = []

    def fn():
        calls.append(1)
        raise http(503)

    with pytest.raises(ProviderError):
        call_with_retry(fn, time.monotonic() + 0.1, 5.0)
    assert len(calls) == 1
    calls.clear()

    def bad():
        calls.append(1)
        raise ValueError("x")

    with pytest.raises(ValueError):
        call_with_retry(bad, time.monotonic() + 10, 1.0)
    assert len(calls) == 1
    calls.clear()

    def twice():
        calls.append(1)
        raise TimeoutError()

    with pytest.raises(TimeoutError):  # at most one retry
        call_with_retry(twice, time.monotonic() + 10, 1.0)
    assert len(calls) == 2


def test_429_waits_one_second_only_if_it_fits(monkeypatch):
    import app.agent.llm as m
    slept = []
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))

    def make():
        calls = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                raise http(429)
            return "ok"
        return fn, calls

    fn, calls = make()
    assert call_with_retry(fn, time.monotonic() + 10, 2.0) == "ok" and slept == [1.0] and len(calls) == 2
    slept.clear()
    fn, calls = make()
    with pytest.raises(ProviderError):  # 2.0 + 1 s does not fit in 2.5 s
        call_with_retry(fn, time.monotonic() + 2.5, 2.0)
    assert slept == [] and len(calls) == 1
    slept.clear()

    def srv():
        calls.append(1)
        if len(calls) == 1:
            raise http(503)
        return "ok"

    calls.clear()
    assert call_with_retry(srv, time.monotonic() + 10, 2.0) == "ok" and slept == []
