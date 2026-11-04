"""Value resolution: map user-typed entity text to exact IDs stored in the database."""
import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from app.db import Database
from app.text import normalize_name, normalize_text

Kind = Literal["company", "broker", "product", "region", "contract"]
_CUTOFF = 75.0


def _fuzzy(q, keys, limit):
    """WRatio-ranked hits (key, score, index), keeping only precise ones.

    WRatio's partial-ratio component scores a query ~90 when it is merely a
    substring of a longer key ("мед" in "катод медный"), so a hit is accepted
    only if fuzz.ratio >= cutoff or every query token is a whole key token.
    """
    q_tokens = q.split()
    out = []
    for key, score, i in process.extract(q, keys, scorer=fuzz.WRatio, limit=None, score_cutoff=_CUTOFF):
        if fuzz.ratio(q, key) >= _CUTOFF or set(q_tokens) <= set(key.split()):
            out.append((key, score, i))
            if limit is not None and len(out) >= limit:
                break
    return out
_ALL = 100_000


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
            "region": self._region,
            "contract": self._contract,
        }[kind](text, limit)

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
