"""Shared helpers for grammar-planner tests."""
import math

from app.plan.build import build_sql
from app.plan.checks import apply_defaults, consistency_error
from app.plan.dictionary import load_dictionary
from app.plan.model import parse_plan
from app.plan.validate import validate_plan

D = load_dictionary()


def prepare(plan: dict, question: str = "", catalog=None):
    p = validate_plan(parse_plan(plan), D, catalog)
    return apply_defaults(p, D, question)


def run_plan(db, resolver, plan: dict, question: str = "", catalog=None):
    p = prepare(plan, question, catalog)
    err = consistency_error(p, D, question)
    assert err is None, err
    built = build_sql(p, D, resolver.variant_names)
    return db.query(built.sql, max_rows=10_000), built, p


def same_value(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return a == b


def assert_rows(got, expected):
    got, expected = [tuple(r) for r in got], [tuple(r) for r in expected]
    assert len(got) == len(expected), (got[:5], expected[:5])
    for g, e in zip(got, expected):
        assert len(g) == len(e), (g, e)
        assert all(same_value(x, y) for x, y in zip(g, e)), (g, e)


def cols(res, *names):
    idx = [res.columns.index(n) for n in names]
    return [tuple(r[i] for i in idx) for r in res.rows]
