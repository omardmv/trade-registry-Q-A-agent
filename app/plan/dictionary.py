"""Load app/fields.yaml (the field dictionary of the grammar planner) into typed objects."""
import datetime as dt
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

FIELDS_PATH = Path(__file__).resolve().parent.parent / "fields.yaml"
TYPES = ("dimension", "number", "date", "bool", "grain", "record")
GROUNDS = ("company", "broker", "product", "product_family", "product_variant", "region", "deal", "contract",
           "catalog", "enum", "none")
FIELD_AGGS = ("sum", "avg", "median", "min", "max", "distinct")
DEFAULT_AGGS = {"number": ("sum", "avg", "median", "min", "max"), "dimension": ("distinct",),
                "record": ("distinct",), "date": ("min", "max"), "bool": (), "grain": ()}


@dataclass(frozen=True)
class Display:
    sql: str
    col: str
    join: str


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    sql: str = ""
    col: str = ""
    label: dict = field(default_factory=dict)
    display: Display | None = None
    ground: str = "none"
    aggs: tuple[str, ...] = ()
    many: bool = False
    per_unit: bool = False
    values: tuple[str, ...] = ()
    catalog: str = ""
    either: dict | None = None          # side -> column
    mode: str = "once"                  # either: once | per_side
    columns: tuple[str, ...] = ()       # record
    joins: tuple[str, ...] = ()         # record joins
    filter_sql: str = ""
    filter_only: bool = False
    range: tuple[int, int] | None = None
    with_year: bool = False
    side_of: str = ""                   # group-only: the side (seller / buyer) of this either-side field
    per_kg: bool = False                # a price in so'm per kg: answers also show it per tonne (× 1000)
    status_values: tuple[str, ...] = () # bool: true exactly for these values of the `status` field


@dataclass(frozen=True)
class Named:
    name: str
    sql: str
    label: dict
    where: str = ""
    scope_free: bool = False
    per_unit: bool = False
    per_kg: bool = False


@dataclass(frozen=True)
class Subject:
    name: str
    table: str
    alias: str
    label: dict
    count_col: str
    count_label: dict
    fields: dict[str, Field]
    measures: dict[str, Named]
    joins: dict[str, str]
    date_field: str = ""
    data_range: tuple[dt.date, dt.date] | None = None
    unit_field: str = ""
    cancelled: str = ""                 # SQL of the "annulled" flag: status defaults apply when set
    status_fields: tuple[str, ...] = ()

    def lbl(self, lang: str) -> str:
        return _label(self.label, lang, self.name)


def _label(d: dict, lang: str, default: str) -> str:
    return d.get("uz" if lang.startswith("uz") else "ru") or d.get("ru") or default


def label(f, lang: str) -> str:
    return _label(f.label, lang, f.name)


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def _field(name: str, d: dict) -> Field:
    ftype = d["type"]
    if ftype not in TYPES:
        raise ValueError(f"field {name}: unknown type {ftype}")
    disp = d.get("display")
    ground = d.get("ground", "none")
    if ground not in GROUNDS:
        raise ValueError(f"field {name}: unknown ground {ground}")
    aggs = tuple(d.get("aggs") or DEFAULT_AGGS[ftype])
    for a in aggs:
        if a not in FIELD_AGGS:
            raise ValueError(f"field {name}: unknown agg {a}")
    return Field(name=name, type=ftype, sql=d.get("sql", ""), col=d.get("col") or name, label=d.get("label") or {},
                 display=Display(**disp) if disp else None, ground=ground, aggs=aggs, many=bool(d.get("many")),
                 per_unit=bool(d.get("per_unit")), values=tuple(str(v) for v in d.get("values") or ()),
                 catalog=d.get("catalog", ""), either=d.get("either"), mode=d.get("mode", "once"),
                 columns=tuple(d.get("columns") or ()), joins=tuple(d.get("joins") or ()),
                 filter_sql=d.get("filter_sql", ""), filter_only=bool(d.get("filter_only")),
                 range=tuple(d["range"]) if d.get("range") else None, with_year=bool(d.get("with_year")),
                 side_of=d.get("side_of", ""), per_kg=bool(d.get("per_kg")),
                 status_values=tuple(str(v) for v in d.get("status_values") or ()))


def _subject(name: str, d: dict) -> Subject:
    fields = {k: _field(k, v) for k, v in (d.get("fields") or {}).items()}
    measures = {k: Named(k, v["sql"], v.get("label") or {}, v.get("where", ""), bool(v.get("scope_free")),
                         bool(v.get("per_unit")), bool(v.get("per_kg"))) for k, v in (d.get("measures") or {}).items()}
    joins = d.get("joins") or {}
    for f in fields.values():
        for j in ([f.display.join] if f.display else []) + list(f.joins):
            if j not in joins:
                raise ValueError(f"{name}.{f.name}: unknown join {j}")
    defaults = d.get("defaults") or {}
    rng = d.get("data_range")
    return Subject(name=name, table=d["table"], alias=d["alias"], label=d.get("label") or {},
                   count_col=d.get("count_col", "n"), count_label=d.get("count_label") or {}, fields=fields,
                   measures=measures, joins=joins, date_field=d.get("date_field", ""),
                   data_range=(_date(rng[0]), _date(rng[1])) if rng else None, unit_field=d.get("unit_field", ""),
                   cancelled=defaults.get("cancelled", ""), status_fields=tuple(defaults.get("status_fields") or ()))


@dataclass(frozen=True)
class Dictionary:
    subjects: dict[str, Subject]

    def subject(self, name: str) -> Subject:
        return self.subjects[name]


def parse_dictionary(raw: dict) -> Dictionary:
    return Dictionary({k: _subject(k, v) for k, v in raw.items()})


@lru_cache(maxsize=4)
def load_dictionary(path: Path = FIELDS_PATH) -> Dictionary:
    return parse_dictionary(yaml.safe_load(path.read_text(encoding="utf-8")))
