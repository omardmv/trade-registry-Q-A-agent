"""Catalog of enum-like values that exist in the database (categories, activities, units, ...).

Used to validate spec values (spec.check_catalog), to list allowed values in the planner prompt, and by the
writer's missing-condition guard to notice a known value named in the question but absent from the spec.
"""
from app.compiler.spec import CONTRACT_FORMS, CONTRACT_TYPES, STATUSES, TRADE_TYPES
from app.db import Database

# catalog field -> SQL returning its distinct values
_SOURCES = {
    "category": "SELECT DISTINCT category FROM contracts",
    "industry": "SELECT DISTINCT industry FROM contracts",
    "product_group": "SELECT DISTINCT product_group FROM contracts",
    "delivery_basis": "SELECT DISTINCT delivery_basis FROM contracts",
    "packaging": "SELECT DISTINCT packaging FROM contracts",
    "hs_code": "SELECT DISTINCT hs_code FROM contracts",
    "unit": "SELECT DISTINCT unit FROM contracts",
    "activity": "SELECT DISTINCT activity FROM participants",
}


def load_catalog(db: Database) -> dict[str, set[str]]:
    out = {name: {r[0] for r in db.query(sql, max_rows=10_000).rows if r[0] is not None}
           for name, sql in _SOURCES.items()}
    out["status"] = set(STATUSES)
    out["contract_form"] = set(CONTRACT_FORMS)
    out["contract_type"] = set(CONTRACT_TYPES)
    out["trade_type"] = set(TRADE_TYPES)
    return out


_PRODUCT_ATTRS = ("category", "industry", "product_group", "product_family")


def load_product_attrs(db: Database) -> dict[str, dict[str, set[str]]]:
    """product_canonical -> {category, industry, product_group, product_family: values} from contracts."""
    out: dict[str, dict[str, set[str]]] = {}
    rows = db.query(f"SELECT DISTINCT product_canonical, {', '.join(_PRODUCT_ATTRS)} FROM contracts",
                    max_rows=100_000).rows
    for product, *vals in rows:
        d = out.setdefault(product, {k: set() for k in _PRODUCT_ATTRS})
        for k, v in zip(_PRODUCT_ATTRS, vals):
            if v is not None:
                d[k].add(v)
    return out


def render_catalog(catalog: dict[str, set[str]]) -> str:
    """Prompt section listing the allowed values of every enum-like filter."""
    lines = ["# Value catalog (exact values for enum-like filters)"]
    for name in ("category", "industry", "product_group", "delivery_basis", "packaging", "unit", "activity",
                 "hs_code"):
        if name in catalog:
            lines.append(f"- {name}: {' | '.join(sorted(catalog[name]))}")
    return "\n".join(lines)
