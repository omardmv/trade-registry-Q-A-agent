"""Per-request trace: every stage records what it got, what it decided and what it produced.

    from agent import trace
    t = trace.start(question)            # in answer()
    trace.step("llm_plan", plan=..., ms=...)
    a.trace = t.data                     # attached to every Answer

Stages used: input, rule_entities, llm_plan, llm_error, verify, refuse, spec, filters, sql, answer.
"""
import contextvars, time

_cur = contextvars.ContextVar("uzex_trace", default=None)


class Trace:
    def __init__(self, question):
        self.t0 = time.perf_counter()
        self.data = {"question": question, "steps": []}

    def step(self, stage, **kw):
        self.data["steps"].append({"stage": stage, "t_ms": round((time.perf_counter() - self.t0) * 1000, 1), **_clean(kw)})


def _clean(x):
    """Make values JSON-safe and bounded."""
    if isinstance(x, dict): return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)): return [_clean(v) for v in list(x)[:50]]
    if isinstance(x, (str, int, float, bool)) or x is None: return x if not isinstance(x, str) else x[:4000]
    return str(x)[:1000]


def start(question):
    t = Trace(question); _cur.set(t); return t


def step(stage, **kw):
    t = _cur.get()
    if t is not None: t.step(stage, **kw)


def current():
    return _cur.get()
