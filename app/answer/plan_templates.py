"""Answer text of the grammar planner, rendered by code from the plan, the result rows and the dictionary labels.

No model writes answer text in grammar mode. Every fact printed comes from a row cell (numbers via
format_number, dates dd.mm.yyyy, names exactly as in the rows in «»), so the answer checker passes by
construction. Shapes: scalar (one or two measures, all / without annulled), ranking (top / least, two-measure
rankings in sections), breakdown and trend (one line per row), compare (two periods and the change), record
listings and lookups (key fields), share, distinct. Anything else -> None, and the caller falls back to
templates.table_answer.
"""
import datetime as dt
from decimal import Decimal

from app.answer.numbers import format_cell, format_number
from app.plan.build import measure_alias
from app.plan.dictionary import Dictionary, Subject, label
from app.plan.model import Measure, Plan
from app.text import to_cyrillic

MAX_LINES = 30

_W = {
    "ru": {"valid": "без аннулированных", "change": "изменение", "pp": "п.п.", "inn": "ИНН", "code": "код",
           "buyer": "как покупатель", "seller": "как продавец", "side_seller": "как брокер продавца",
           "side_buyer": "как брокер покупателя", "by": "По показателю: {m}", "quarter": "{q} квартал {y}",
           "hour": "{h} ч", "yes": "да", "no": "нет", "more": "… (остальные строки — в таблице результата)",
           "share": "доля", "no_value": "нет данных",
           "avg": "среднее — {v}", "median": "медиана — {v}", "min": "минимум — {v}", "max": "максимум — {v}",
           "distinct": "число различных — {v}", "per_kg": "{kg} сум/кг = {t} сум/т"},
    "uz": {"valid": "bekor qilinganlarsiz", "change": "o‘zgarish", "pp": "f.p.", "inn": "STIR", "code": "kod",
           "buyer": "xaridor sifatida", "seller": "sotuvchi sifatida", "side_seller": "sotuvchi brokeri sifatida",
           "side_buyer": "xaridor brokeri sifatida", "by": "Ko‘rsatkich bo‘yicha: {m}",
           "quarter": "{y}-yil {q}-chorak", "hour": "soat {h}", "yes": "ha", "no": "yo‘q",
           "more": "… (qolgan qatorlar — so‘rov natijasi jadvalida)", "share": "ulush", "no_value": "ma’lumot yo‘q",
           "avg": "o‘rtacha — {v}", "median": "mediana — {v}", "min": "eng kichik — {v}", "max": "eng katta — {v}",
           "distinct": "turli qiymatlar soni — {v}", "per_kg": "{kg} so‘m/kg = {t} so‘m/t"},
}
# record / lookup columns that are not dictionary fields of the subject
_COLS = {
    "deal_id": ("сделка", "bitim"), "inn": ("ИНН", "STIR"), "name": ("наименование", "nomi"), "contract_id": ("контракт", "shartnoma"), "deal_date": ("дата сделки", "bitim sanasi"),
    "product": ("товар", "tovar"), "product_variant": ("наименование товара", "tovar nomi"),
    "product_name": ("наименование товара", "tovar nomi"), "volume": ("объём", "hajm"), "unit": ("единица", "birlik"),
    "unit_price": ("цена за единицу (в валюте контракта)", "birlik narxi (shartnoma valyutasida)"),
    "currency": ("валюта", "valyuta"), "amount_uzs": ("сумма в сумах", "summa (so‘m)"), "status": ("состояние", "holat"),
    "seller": ("продавец", "sotuvchi"), "buyer": ("покупатель", "xaridor"), "company": ("компания", "kompaniya"),
    "broker": ("брокер", "broker"), "region": ("регион", "hudud"), "activity": ("вид деятельности", "faoliyat turi"),
    "role": ("роль", "rol"), "vat_payer": ("плательщик НДС", "QQS to‘lovchi"),
    "deals_as_buyer": ("сделок как покупатель (все состояния)", "xaridor sifatida bitimlar (barcha holatlar)"),
    "deals_as_seller": ("сделок как продавец (все состояния)", "sotuvchi sifatida bitimlar (barcha holatlar)"),
    "deals_as_buyer_valid": ("сделок как покупатель без аннулированных", "xaridor sifatida bitimlar (bekor qilinganlarsiz)"),
    "deals_as_seller_valid": ("сделок как продавец без аннулированных", "sotuvchi sifatida bitimlar (bekor qilinganlarsiz)"),
    "bought_uzs_valid": ("куплено на сумму в сумах без аннулированных", "xarid summasi (so‘m, bekor qilinganlarsiz)"),
    "sold_uzs_valid": ("продано на сумму в сумах без аннулированных", "sotuv summasi (so‘m, bekor qilinganlarsiz)"),
    "first_deal_date": ("первая сделка", "birinchi bitim"), "last_deal_date": ("последняя сделка", "oxirgi bitim"),
    "deals_as_seller_broker": ("сделок как брокер продавца (все состояния)", "sotuvchi brokeri sifatida bitimlar (barcha holatlar)"),
    "deals_as_buyer_broker": ("сделок как брокер покупателя (все состояния)", "xaridor brokeri sifatida bitimlar (barcha holatlar)"),
    "deals_as_seller_broker_valid": ("сделок как брокер продавца без аннулированных",
                                     "sotuvchi brokeri sifatida bitimlar (bekor qilinganlarsiz)"),
    "deals_as_buyer_broker_valid": ("сделок как брокер покупателя без аннулированных",
                                    "xaridor brokeri sifatida bitimlar (bekor qilinganlarsiz)"),
    "deals_either_side": ("сделок на любой стороне", "istalgan tomonda bitimlar"),
    "deals_all": ("сделок всего", "jami bitimlar"), "deals_valid": ("сделок без аннулированных", "bitimlar (bekor qilinganlarsiz)"),
    "brand": ("марка", "marka"), "grade": ("сорт", "nav"), "size": ("размер", "o‘lcham"),
    "warehouse_address": ("адрес склада", "ombor manzili"),
    "seller_brokers": ("брокеры продавца", "sotuvchi brokerlari"), "buyer_brokers": ("брокеры покупателя", "xaridor brokerlari"),
}
# (id column, name column, kind of id) shown together as «name» (ИНН id) / «name» (код id)
_ENTITIES = [("seller_inn", "seller_name", "inn"), ("buyer_inn", "buyer_name", "inn"), ("inn", "name", "inn"),
             ("broker_code", "broker_name", "code"), ("seller_broker", "seller_broker_name", "code"),
             ("buyer_broker", "buyer_broker_name", "code")]
_ENTITY_LABEL = {"seller_inn": "seller", "buyer_inn": "buyer", "inn": "company", "broker_code": "broker",
                 "seller_broker": "seller_broker", "buyer_broker": "buyer_broker"}
_PLAIN = {"unit", "currency", "role", "broker_side", "ranking"}     # short codes / words: not quoted
_ID_COLS = {"deal_id", "contract_id"}
_COMPARE_COLS = {"year", "month_from", "month_to"}


_MONTH_NAMES = {
    "ru": ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь",
           "декабрь"],
    "uz": ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentyabr", "oktyabr", "noyabr",
           "dekabr"],
}


def is_per_kg(s: Subject, m: Measure) -> bool:
    """A price per kg in so'm (fields.yaml per_kg): shown per kg and per tonne."""
    if m.agg in s.measures:
        return s.measures[m.agg].per_kg
    return bool(m.of) and m.of in s.fields and s.fields[m.of].per_kg and m.agg != "distinct"


def per_tonne(v):
    return v * 1000 if isinstance(v, Decimal) else float(v) * 1000


def derived_numbers(plan: Plan, d: Dictionary, columns: list[str], rows: list) -> str:
    """Numbers the templates derive from row cells (per-kg prices per tonne), as text for the answer check."""
    try:
        r = _R(plan, d, columns, "ru")
    except (_Unusual, KeyError, IndexError, TypeError, ValueError):
        return ""
    out = []
    for col, (m, _) in r.measures.items():
        if is_per_kg(r.s, m):
            i = r.idx[col]
            out += [format_number(per_tonne(row[i])) for row in rows if _num(row[i])]
    return "; ".join(out)


class _Unusual(Exception):
    """A row shape the templates do not cover."""


def _lang(lang: str) -> str:
    return "uz" if lang.startswith("uz") else "ru"


def _num(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


class _R:
    def __init__(self, plan: Plan, d: Dictionary, columns: list[str], lang: str):
        self.plan, self.s, self.columns = plan, d.subject(plan.subject), list(columns)
        self.lang, self.l = lang, _lang(lang)
        self.w = _W[self.l]
        self.idx = {c: i for i, c in enumerate(columns)}
        if len(self.idx) != len(columns):
            raise _Unusual("duplicate columns")
        self.measures = self._measure_cols()

    # ---- labels ----
    def t(self, text: str) -> str:
        return to_cyrillic(text) if self.lang == "uz-Cyrl" else text

    def field_label(self, name: str) -> str:
        f = self.s.fields.get(name)
        if f is not None:
            return label(f, self.l)
        for fl in self.s.fields.values():
            if fl.col == name:
                return label(fl, self.l)
            if fl.display is not None and fl.display.col == name:
                return label(fl, self.l)
        if name in _COLS:
            return _COLS[name][0 if self.l == "ru" else 1]
        raise _Unusual(f"no label for column {name}")

    def measure_label(self, m: Measure) -> str:
        s = self.s
        if m.agg == "count":
            return s.count_label.get(self.l) or s.count_col
        if m.agg in s.measures:
            return label(s.measures[m.agg], self.l)
        if m.agg == "share":
            return self.w["share"] + (f" ({label(s.fields[m.of], self.l)})" if m.of else "")
        v = label(s.fields[m.of], self.l)
        return v if m.agg == "sum" else self.w[m.agg].format(v=v)

    @staticmethod
    def is_pct(m: Measure) -> bool:
        return m.agg in ("share", "cancelled_share") or (m.of or "").endswith("_pct")

    def _measure_cols(self) -> dict[str, tuple[Measure, str]]:
        """measure output column -> (measure, view: '' | all | valid), as app.plan.build names them."""
        out: dict[str, tuple[Measure, str]] = {}
        seen: dict[str, int] = {}
        for m in self.plan.measures:
            alias = measure_alias(self.s, m)
            seen[alias] = seen.get(alias, 0) + 1
            if seen[alias] > 1:
                alias = f"{alias}_{seen[alias]}"
            if f"{alias}_all" in self.idx and f"{alias}_valid" in self.idx:
                out[f"{alias}_all"], out[f"{alias}_valid"] = (m, "all"), (m, "valid")
            elif alias in self.idx:
                out[alias] = (m, "")
            else:
                raise _Unusual(f"measure column {alias} missing")
        return out

    # ---- values ----
    def value(self, col: str, v) -> str:
        if v is None:
            return "—"
        if isinstance(v, bool):
            return self.t(self.w["yes"] if v else self.w["no"])
        if col in _ID_COLS or (col in self.s.fields and self.s.fields[col].ground in ("deal", "contract")):
            return str(v)
        if _num(v):
            return format_number(v)
        if isinstance(v, (dt.date, dt.datetime, dt.time)):
            return format_cell(v)
        text = str(v)
        if col in _PLAIN or not any(ch.isalpha() for ch in text):
            return text
        return f"«{text}»"

    def measure_value(self, row, col: str) -> str:
        m, _ = self.measures[col]
        v = row[self.idx[col]]
        if v is None:
            return self.t(self.w["no_value"])
        text = format_number(v) if _num(v) else self.value(col, v)
        if _num(v) and is_per_kg(self.s, m):
            # a price per kg: the same price per tonne too, so the unit the user asked for is in the answer
            text = self.t(self.w["per_kg"].format(kg=text, t=format_number(per_tonne(v))))
        if self.is_pct(m) and _num(v):
            text += "%"
        unit = self.idx.get(self.s.fields[self.s.unit_field].col) if self.s.unit_field else None
        if unit is not None and self._per_unit(m) and row[unit] is not None and "unit" not in self.plan.group_by:
            text += f" {row[unit]}"
        return text

    def _per_unit(self, m: Measure) -> bool:
        if m.agg in self.s.measures:
            return self.s.measures[m.agg].per_unit
        return bool(m.of) and self.s.fields[m.of].per_unit and m.agg != "distinct"

    def entity(self, row, id_col: str, name_col: str, kind: str) -> str:
        ident, name = row[self.idx[id_col]], row[self.idx[name_col]] if name_col in self.idx else None
        tag = self.w["inn"] if kind == "inn" else self.w["code"]
        if ident is None:
            return "—"
        return f"«{name}» ({self.t(tag)} {ident})" if name else f"{self.t(tag)} {ident}"

    # ---- parts of a line ----
    def measures_text(self, row) -> str:
        parts, done = [], set()
        for col, (m, view) in self.measures.items():
            if col in done:
                continue
            lbl = self.t(self.measure_label(m))
            if view == "all":
                valid = col[:-4] + "_valid"
                done.add(valid)
                parts.append(f"{lbl}: {self.measure_value(row, col)} ({self.t(self.w['valid'])}: "
                             f"{self.measure_value(row, valid)})")
            else:
                parts.append(f"{lbl}: {self.measure_value(row, col)}")
        return "; ".join(parts)

    def key_text(self, row, used: set[str]) -> str:
        s, parts = self.s, []
        for g in self.plan.group_by:
            f = s.fields[g]
            if f.side_of:
                used.add(f.col)
                parts.append(self.t(self.w["side_" + str(row[self.idx[f.col]])]))
            elif f.either:
                used |= {f.col, f.display.col}
                parts.append(self.entity(row, f.col, f.display.col, "inn" if f.ground == "company" else "code"))
                if f.mode == "per_side":
                    used.add("role")
                    parts.append(self.t(self.w[str(row[self.idx["role"]])]))
            elif f.type == "grain":
                parts.append(self.time_text(row, f, used))
            else:
                used.add(f.col)
                if f.display is not None:
                    used.add(f.display.col)
                    parts.append(self.entity(row, f.col, f.display.col, "inn" if f.ground == "company" else "code"))
                else:
                    v = row[self.idx[f.col]]
                    parts.append(self.value(f.col, v) if f.ground not in ("deal", "contract")
                                 else f"{self.t(label(f, self.l))} {v}")
        return ", ".join(parts)

    def month(self, m, y=None) -> str:
        name = self.t(_MONTH_NAMES[self.l][int(m) - 1])
        return f"{name} {y}" if y is not None else name

    def time_text(self, row, f, used: set[str]) -> str:
        v = row[self.idx[f.col]]
        used.add(f.col)
        if f.name in ("day",):
            return self.value(f.col, v)
        year_col = self.s.fields["year"].col if "year" in self.s.fields else None
        y = row[self.idx[year_col]] if year_col in self.idx else None
        if f.name == "year":
            return str(v)
        if f.name == "month":
            used.add(year_col)
            return self.month(v, y)
        if f.name == "quarter":
            used.add(year_col)
            return self.t(self.w["quarter"].format(q=v, y=y if y is not None else ""))
        if f.name == "hour":
            return self.t(self.w["hour"].format(h=v))
        return self.value(f.col, v)

    # ---- shapes ----
    def record_line(self, row) -> str:
        ents = {e[0]: e for e in _ENTITIES if e[0] in self.idx and e[1] in self.idx}
        names = {e[1] for e in ents.values()}
        parts = []
        for c in self.columns:
            v = row[self.idx[c]]
            if c in names or c in self.measures or v is None:
                continue
            if c in ents:
                parts.append(f"{self.t(self.field_label(_ENTITY_LABEL[c]))}: {self.entity(row, *ents[c])}")
            else:
                parts.append(f"{self.t(self.field_label(c))}: {self.value(c, v)}")
        if self.measures:
            shown = {p.split(": ", 1)[0] for p in parts}
            text = self.measures_text(row)
            if not all(x.split(": ", 1)[0] in shown for x in text.split("; ")):
                parts.append(text)      # a ranking measure that repeats a shown column is left out
        return "; ".join(parts)

    def row_line(self, row) -> str:
        if any(self.s.fields[g].type == "record" for g in self.plan.group_by):
            return self.record_line(row)
        used: set[str] = set(self.measures)
        key = self.key_text(row, used)
        extra = [c for c in self.columns if c not in used and c != "ranking"
                 and not (self.s.unit_field and c == self.s.fields[self.s.unit_field].col)]
        if extra:
            raise _Unusual(f"unexpected columns {extra}")
        body = self.measures_text(row)
        return f"{key} — {body}" if key else body

    def lines(self, rows) -> list[str]:
        out = []
        numbered = self.plan.limit is not None or any(self.s.fields[g].type == "record" for g in self.plan.group_by)
        for i, row in enumerate(rows[:MAX_LINES], 1):
            line = self.row_line(row)
            out.append(f"{i}. {line}" if numbered and len(rows) > 1 else line)
        if len(rows) > MAX_LINES:
            out.append(self.t(self.w["more"]))
        return out

    def compare(self, rows) -> list[str]:
        if not all(c in self.idx for c in _COMPARE_COLS):
            raise _Unusual("compare columns")
        out = []
        unit = self.s.fields[self.s.unit_field].col if self.s.unit_field else None
        changes = [c for c in self.columns if c.endswith("change_pct") or c.endswith("change_pp")]
        for row in rows:
            y, mf, mt = (row[self.idx[c]] for c in ("year", "month_from", "month_to"))
            day = row[self.idx["day"]] if "day" in self.idx else None
            period = f"{int(day):02d}.{int(mf):02d}.{y}" if day is not None else str(y) if mf is None else (self.month(mf, y) if mf == mt
                                                else f"{self.month(mf)}–{self.month(mt)} {y}")
            if unit in self.idx and row[self.idx[unit]] is not None:
                period += f", {row[self.idx[unit]]}"
            out.append(f"{period}: {self.measures_text(row)}")
            for c in changes:
                v = row[self.idx[c]]
                if v is None:
                    continue
                sign = "+" if v > 0 else ""
                suffix = f" {self.t(self.w['pp'])}" if c.endswith("_pp") else "%"
                base = c.rsplit("_change_", 1)[0]
                m, view = self.measures.get(base) or (None, "")
                bits = [self.measure_label(m)] if m is not None and len(self.plan.measures) > 1 else []
                bits += [self.w["valid"]] if view == "valid" else []
                what = f" ({self.t(', '.join(bits))})" if bits else ""
                out.append(f"{self.t(self.w['change'])}{what}: {sign}{format_number(v)}{suffix}")
        return out

    def body(self, rows) -> str:
        known = set(self.measures) | {"ranking"}
        if self.plan.compare:
            return "\n".join(self.compare(rows))
        if "ranking" in self.idx:
            out = []
            for key in dict.fromkeys(r[self.idx["ranking"]] for r in rows):
                col = str(key).removeprefix("by_")
                m = self.measures.get(col) or self.measures.get(col + "_all")
                if m is None:
                    raise _Unusual(f"ranking {key}")
                out.append(self.t(self.w["by"].format(m=self.measure_label(m[0]))))
                out += self.lines([r for r in rows if r[self.idx["ranking"]] == key])
            return "\n".join(out)
        if not self.plan.group_by and len(rows) == 1 and set(self.columns) <= known:
            return self.measures_text(rows[0])
        return "\n".join(self.lines(rows))


def render(plan: Plan, d: Dictionary, columns: list[str], rows: list, lang: str) -> str | None:
    """Answer text for a plan result, or None when the shape is not covered (use templates.table_answer)."""
    if not rows:
        return None
    try:
        r = _R(plan, d, columns, lang)
        return r.body(rows) or None
    except (_Unusual, KeyError, IndexError, TypeError, ValueError):
        return None


_EXTRA_LABELS = {"ru": {"year": "год", "month_from": "месяц с", "month_to": "месяц по", "day": "день",
                        "change_pct": "изменение, %", "change_pp": "изменение, п.п.", "ranking": "рейтинг по",
                        "broker_side": "сторона", "unit": "единица"},
                 "uz": {"year": "yil", "month_from": "oydan", "month_to": "oygacha", "day": "kun",
                        "change_pct": "o‘zgarish, %", "change_pp": "o‘zgarish, f.p.", "ranking": "reyting",
                        "broker_side": "tomon", "unit": "birlik"}}


def column_labels(plan: Plan, d: Dictionary, columns: list[str], lang: str) -> list[str]:
    """A readable label per result column in the question's language (the raw name when none is known), for the
    data table shown under the answer."""
    try:
        r = _R(plan, d, columns, lang)
    except (_Unusual, KeyError, IndexError, TypeError, ValueError):
        return list(columns)
    extra = _EXTRA_LABELS[r.l]
    out = []
    for c in columns:
        text = None
        if c in r.measures:
            m, view = r.measures[c]
            every = "все" if r.l == "ru" else "barchasi"
            text = r.measure_label(m) + {"all": f" ({every})", "valid": f" ({r.w['valid']})", "": ""}[view]
        elif c.endswith(("_change_pct", "_change_pp")) or c in extra:
            base = c.rsplit("_change_", 1)[0] if "_change_" in c else ""
            key = "change_pp" if c.endswith("_pp") else "change_pct" if c.endswith("_pct") else c
            text = extra[key]
            if base and (base in r.measures or base + "_all" in r.measures):
                text = f"{r.measure_label((r.measures.get(base) or r.measures[base + '_all'])[0])}: {text}"
        else:
            try:
                text = r.field_label(c)
            except _Unusual:
                text = None
        if text and c.endswith("_inn") or c == "inn":
            text = f"{text}, {r.w['inn']}" if text and text != r.w["inn"] else text
        elif text and c.endswith("_name") and c != "product_name":
            text = f"{text}, {'название' if r.l == 'ru' else 'nomi'}"
        out.append(r.t(text) if text else c)
    return out


__all__ = ["render", "column_labels", "derived_numbers", "MAX_LINES", "Subject"]
