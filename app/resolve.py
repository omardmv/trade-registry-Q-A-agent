"""Value resolution: map user-typed entity text to exact IDs stored in the database."""
import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from app.db import Database
from app.text import normalize_name, normalize_text

Kind = Literal["company", "broker", "product", "product_variant", "region", "contract"]
_CUTOFF = 75.0
_SHORT_LEN = 6  # one-letter edits between short words are different words
_SHORT_CUTOFF = 90.0


def _fuzzy(q, keys, limit):
    """WRatio-ranked hits (key, score, index), keeping only precise ones.

    WRatio's partial-ratio component scores a query ~90 when it is merely a
    substring of a longer key ("мед" in "катод медный"), so a hit is accepted
    only if fuzz.ratio >= cutoff or every query token is a whole key token.
    """
    q_tokens = q.split()
    out = []
    for key, score, i in process.extract(q, keys, scorer=fuzz.WRatio, limit=None, score_cutoff=_CUTOFF):
        cutoff = _SHORT_CUTOFF if min(len(q), len(key)) <= _SHORT_LEN else _CUTOFF
        if fuzz.ratio(q, key) >= cutoff or set(q_tokens) <= set(key.split()):
            out.append((key, score, i))
            if limit is not None and len(out) >= limit:
                break
    return out
_ALL = 100_000

_GRADE_KEYS = {"sort": "sort", "sorta": "sort", "sortu": "sort", "klass": "klass", "klassa": "klass"}
_GRADE_NUMS = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "perviy": "1", "pervogo": "1", "pervaya": "1",
               "vtoroy": "2", "vtorogo": "2", "tretiy": "3", "tretego": "3", "chetvertiy": "4", "birinchi": "1",
               "ikkinchi": "2", "uchinchi": "3"}


def _is_num(t: str) -> bool:
    return t.isdigit()


_GLUED_RE = re.compile(r"[a-z]+|[0-9]+")


def _is_mark(t: str) -> bool:
    """Grade / mark token (digits, folded grade, short letter code such as "i", "n", "m", "k"): marks of a spelling
    are compared as a set, words in order."""
    return any(ch.isdigit() for ch in t) or ":" in t or len(t) <= 2


def canonical_variant_tokens(text: str) -> list[str]:
    """normalize_text tokens with grade expressions folded into one token: "Сорт 1 сорт", "1-сорта",
    "первый сорт", "I сорт" -> "sort:1"; the same for класс -> "klass:N"."""
    # glued letters and digits are separate tokens: "42,5Н" -> "42", "5", "n"; "М00К" -> "m", "00", "k"
    split = [p for t in normalize_text(text).split() for p in _GLUED_RE.findall(t)]
    raw = [_GRADE_KEYS.get(t, t) for t in split]
    toks = []
    for i, t in enumerate(raw):  # roman / word numerals count only next to сорт/класс
        near = (i > 0 and raw[i - 1] in ("sort", "klass")) or (i + 1 < len(raw) and raw[i + 1] in ("sort", "klass"))
        toks.append(_GRADE_NUMS.get(t, t) if near else t)
    out: list[str] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t in ("sort", "klass") and i + 1 < n and _is_num(toks[i + 1]):
            out.append(f"{t}:{toks[i + 1]}")
            i += 2
            if i < n and toks[i] == t:      # "Сорт 1 сорт"
                i += 1
            continue
        if _is_num(t) and i + 1 < n and toks[i + 1] in ("sort", "klass"):
            out.append(f"{toks[i + 1]}:{t}")
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def _in_order(q: list[str], name: list[str]) -> bool:
    """Words of q appear in name in the same order; grade / mark tokens of q only need to be present (a set)."""
    marks = {t for t in q if _is_mark(t)}
    if not marks <= set(name):
        return False
    it = iter(name)
    return all(any(t == x for x in it) for t in q if not _is_mark(t))


@dataclass
class Candidate:
    kind: str
    id: str
    name: str
    score: float
    extra: dict = field(default_factory=dict)


class Resolver:
    def __init__(self, db: Database):
        self._db = db
        self._companies = db.query(
            "SELECT inn, name, name_norm, region, role, deals_as_buyer, deals_as_seller FROM participants",
            max_rows=_ALL).rows
        self._company_by_inn = {r[0]: r for r in self._companies}
        self._brokers = db.query(
            "SELECT broker_code, name, name_norm, region, deals_as_seller_broker, deals_as_buyer_broker FROM brokers",
            max_rows=_ALL).rows
        self._broker_by_code = {r[0]: r for r in self._brokers}
        self._product_rows = db.query(
            "SELECT product_name, product_canonical, product_family, synonyms FROM products", max_rows=_ALL).rows
        self._regions = db.query("SELECT region, region_ru, synonyms FROM regions", max_rows=_ALL).rows
        self._variants = db.query(
            """SELECT product_name_full, product_canonical, COUNT(*) FROM deals GROUP BY 1, 2
               ORDER BY 3 DESC, 1""", max_rows=_ALL).rows
        self._variant_seqs = [canonical_variant_tokens(r[0]) for r in self._variants]
        self._variant_tokens = [set(t) for t in self._variant_seqs]
        self._product_keys, self._product_targets = self._index_products()
        self._region_keys, self._region_targets = self._index_regions()

    def _index_products(self):
        keys, targets = [], []
        for raw, canonical, family, synonyms in self._product_rows:
            syns = [t for t in synonyms.split(";") if t.strip()]
            for text in [raw, canonical, *syns]:
                keys.append(normalize_text(text))
                targets.append(("product", canonical, family))
            keys.append(normalize_text(family))
            targets.append(("product_family", family, family))
            # A product whose synonyms list the family name itself (e.g. "хлопок") is the family's
            # base product: its other synonyms (e.g. Uzbek "paxta") also identify the family.
            if normalize_text(family) in {normalize_text(t) for t in syns}:
                for text in syns:
                    keys.append(normalize_text(text))
                    targets.append(("product_family", family, family))
        return keys, targets

    def _index_regions(self):
        keys, targets = [], []
        for region, region_ru, synonyms in self._regions:
            for text in [region, region_ru, *synonyms.split(";")]:
                keys.append(normalize_text(text))
                targets.append(region)
        return keys, targets

    def resolve(self, kind: Kind, text: str, limit: int = 10) -> list[Candidate]:
        text = (text or "").strip()
        if not text:
            return []
        return {
            "company": self._company,
            "broker": self._broker,
            "product": self._product,
            "product_variant": self._product_variant,
            "region": self._region,
            "contract": self._contract,
        }[kind](text, limit)

    def known_names(self) -> frozenset[str]:
        """Normalized (normalize_name) display names of every company, broker, product, family and region."""
        raw = [r[1] for r in self._companies] + [r[1] for r in self._brokers]
        for product_name, canonical, family, _ in self._product_rows:
            raw += [product_name, canonical, family]
        for region, region_ru, _ in self._regions:
            raw += [region, region_ru]
        return frozenset(n for n in (normalize_name(t) for t in raw if t) if n)

    def alias_groups(self) -> list[frozenset[str]]:
        """Groups of normalized names that denote the same region or product (incl. translations/synonyms)."""
        groups = []
        for region, region_ru, synonyms in self._regions:
            groups.append([region, region_ru, *synonyms.split(";")])
        for product_name, canonical, family, synonyms in self._product_rows:
            groups.append([product_name, canonical, family, *synonyms.split(";")])
        out = []
        for g in groups:
            names = frozenset(n for n in (normalize_name(t) for t in g if t and t.strip()) if n)
            if names:
                out.append(names)
        return out

    def product_rows(self) -> list[tuple]:
        """(product_name, product_canonical, product_family, synonyms) of the products dictionary."""
        return list(self._product_rows)

    def region_rows(self) -> list[tuple]:
        """(region, region_ru, synonyms) of the regions dictionary."""
        return list(self._regions)

    def all_entity_names(self) -> list[str]:
        """Every name a question may use for an entity: companies, brokers, products (+ synonyms), product
        variants and regions (+ synonyms)."""
        out = [r[1] for r in self._companies] + [r[1] for r in self._brokers] + [r[0] for r in self._variants]
        for product_name, canonical, family, synonyms in self._product_rows:
            out += [product_name, canonical, family, *(synonyms or "").split(";")]
        for region, region_ru, synonyms in self._regions:
            out += [region, region_ru, *(synonyms or "").split(";")]
        return [n for n in out if n]

    def entity_names(self, kind: str, value: str) -> list[str]:
        """Names of one entity as a plan uses it: kind is a field ground (company, broker, product,
        product_family, product_variant, region); value an INN / broker code / catalog value / variant phrase."""
        value = str(value)
        if kind == "company":
            row = self._company_by_inn.get(value)
            return [row[1]] if row else []
        if kind == "broker":
            row = self._broker_by_code.get(value.upper())
            return [row[1]] if row else []
        if kind in ("product", "product_family"):
            col = 1 if kind == "product" else 2
            out = []
            for row in self._product_rows:
                if row[col] == value:
                    out += [row[0], row[1], row[2], *(row[3] or "").split(";")]
            out += [r[0] for r in self._variants if r[1] in {row[1] for row in self._product_rows if row[col] == value}]
            return out or [value]
        if kind == "product_variant":
            return [value, *self.variant_names(value)]
        if kind == "region":
            return [n for r in self._regions if value in (r[0], r[1]) for n in (r[0], r[1], *(r[2] or "").split(";"))]
        return [value]

    def exists(self, kind: str, value: str) -> bool:
        """Exact existence of an id: kind in inn / broker / contract / deal."""
        if kind == "inn":
            return value in self._company_by_inn
        if kind == "broker":
            return value.upper() in self._broker_by_code
        table, column = {"contract": ("contracts", "contract_id"), "deal": ("deals", "deal_id")}[kind]
        if not re.fullmatch(r"\d{1,12}", value):
            return False
        return bool(self._db.query(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", [int(value)]).rows)

    def _company(self, text, limit):
        if re.fullmatch(r"\d{9}", text):
            row = self._company_by_inn.get(text)
            return [self._company_candidate(row, 100.0)] if row else []
        q = normalize_name(text)
        if not q:
            return []
        hits = _fuzzy(q, [r[2] for r in self._companies], limit)
        cands = [self._company_candidate(self._companies[i], score) for _, score, i in hits]
        return sorted(cands, key=lambda c: (-c.score, -(c.extra["deals_as_buyer"] + c.extra["deals_as_seller"])))

    @staticmethod
    def _company_candidate(row, score):
        inn, name, _, region, role, buyer, seller = row
        return Candidate("company", inn, name, float(score),
                         {"region": region, "role": role, "deals_as_buyer": buyer, "deals_as_seller": seller})

    def _broker(self, text, limit):
        code = text.upper().replace(" ", "")
        if re.fullmatch(r"BK\d{3}", code):
            row = self._broker_by_code.get(code)
            return [self._broker_candidate(row, 100.0)] if row else []
        q = normalize_name(text)
        if not q:
            return []
        hits = _fuzzy(q, [r[2] for r in self._brokers], limit)
        return [self._broker_candidate(self._brokers[i], score) for _, score, i in hits]

    @staticmethod
    def _broker_candidate(row, score):
        code, name, _, region, as_seller, as_buyer = row
        return Candidate("broker", code, name, float(score),
                         {"region": region, "deals_as_seller_broker": as_seller, "deals_as_buyer_broker": as_buyer})

    def _product(self, text, limit):
        q = normalize_text(text)
        if not q:
            return []
        hits = _fuzzy(q, self._product_keys, None)
        best: dict[tuple[str, str], Candidate] = {}
        for _, score, i in hits:
            kind, value, family = self._product_targets[i]
            key = (kind, value)
            if key not in best or score > best[key].score:
                best[key] = Candidate(kind, value, value, float(score), {"product_family": family})
        return sorted(best.values(), key=lambda c: -c.score)[:limit]

    def _variant_hits(self, text: str) -> tuple[list[int], set[str]]:
        """Indexes of every raw registry spelling (deals.product_name_full) that matches the text, best first.

        Grades are folded into single tokens ("1 сорт" = "Сорт 1 сорт" = "первый сорт" -> sort:1), so "1 класс" never
        matches a "1 сорт" query; marks differ by one character (М00К / М0К) and must match exactly. Spellings that
        contain the words in order win; if none does, the word set is used, then fuzzy matching with exact digits.
        Never truncated: this is the matcher that filters.product_variant_query is expanded with."""
        q_seq = canonical_variant_tokens(text)
        if not q_seq:
            return [], set()
        q = set(q_seq)
        hits = [i for i, seq in enumerate(self._variant_seqs) if _in_order(q_seq, seq)]
        if not hits:
            hits = [i for i, toks in enumerate(self._variant_tokens) if q <= toks]
        if not hits:
            digits = {t for t in q if any(ch.isdigit() for ch in t)}
            keys = [" ".join(seq) for seq in self._variant_seqs]
            hits = [i for _, _, i in _fuzzy(" ".join(q_seq), keys, None) if digits <= self._variant_tokens[i]]
        hits.sort(key=lambda i: (len(self._variant_tokens[i] - q), -self._variants[i][2]))
        return hits, q

    def same_grade_spellings(self, text: str) -> list[str]:
        """Spellings of the same product(s) with the same grade / mark that the text misses because the product is
        named differently ("Портландцемент ЦЕМ I 42,5 Н" vs "Цемент портландский ЦЕМ I 42,5Н"): the query's
        product-name words are dropped and the rest (other words in order, marks as a set) is matched within the
        products of the original hits. Empty when the text has no grade / mark or nothing is missed."""
        hits, _ = self._variant_hits((text or "").strip())
        q_seq = canonical_variant_tokens(text or "")
        if not hits or not any(_is_mark(t) for t in q_seq):
            return []
        products = {self._variants[i][1] for i in hits}
        vocab = set()
        for raw, canonical, _, synonyms in self._product_rows:
            if canonical in products:
                for key in [raw, canonical, *synonyms.split(";")]:
                    vocab |= {t for t in canonical_variant_tokens(key) if not _is_mark(t)}
        rest = [t for t in q_seq if _is_mark(t) or t not in vocab]
        if not any(_is_mark(t) for t in rest):
            return []
        have = set(hits)
        return [self._variants[i][0] for i, seq in enumerate(self._variant_seqs)
                if i not in have and self._variants[i][1] in products and _in_order(rest, seq)]

    def variant_names(self, text: str) -> list[str]:
        """Every raw spelling that resolve("product_variant", text) matches (the expansion of
        filters.product_variant_query)."""
        hits, _ = self._variant_hits((text or "").strip())
        return [self._variants[i][0] for i in hits]

    def _product_variant(self, text, limit=None):
        """Every matching raw spelling as a candidate (see _variant_hits); `limit` is ignored."""
        hits, q = self._variant_hits(text)
        out = []
        for i in hits:
            name, canonical, n = self._variants[i]
            score = 100.0 if self._variant_tokens[i] == q else 90.0
            out.append(Candidate("product_variant", name, name, score,
                                 {"product_canonical": canonical, "deals": n, "matches": len(hits)}))
        return out

    def _region(self, text, limit):
        q = normalize_text(text)
        hits = _fuzzy(q, self._region_keys, None)
        best: dict[str, Candidate] = {}
        for _, score, i in hits:
            region = self._region_targets[i]
            if region not in best or score > best[region].score:
                best[region] = Candidate("region", region, region, float(score))
        return sorted(best.values(), key=lambda c: -c.score)[:limit]

    def _contract(self, text, limit):
        digits = re.sub(r"\D", "", text)
        if not digits:
            return []
        res = self._db.query(
            """SELECT c.contract_id, c.product_name, c.product_canonical, c.seller_inn, p.name
               FROM contracts c LEFT JOIN participants p ON p.inn = c.seller_inn WHERE c.contract_id = ?""",
            [int(digits)])
        return [Candidate("contract", str(r[0]), r[1], 100.0,
                          {"product_canonical": r[2], "seller_inn": r[3], "seller_name": r[4]}) for r in res.rows]
