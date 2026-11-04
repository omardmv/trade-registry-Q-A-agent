"""Value index built from the database itself — no hand-written lists.

At startup every distinct value of every text dimension is read from DuckDB (products, regions, statuses,
categories, industries, delivery bases, packaging, units, activities, roles...). If tomorrow's data adds a new
product or region, it is in the index after a restart.

Product mentions are resolved CHESS-style:
  1. the LLM rewrites each mention as a Russian registry-style term ("bug'doy" -> "пшеница", "qand" -> "сахар");
  2. code matches that term against the real registry names: stem match, or embedding similarity >= HIGH
     (spelling variants only). Plain embedding similarity is NOT trusted for different products
     ("кукуруза" ~ "пшеница" = 0.90), so anything without a strong match is unresolved -> "no data".
"""
import json, math, os, re, urllib.request
from . import core as C

EMB_MODEL = "gemini-embedding-001"
HIGH = 0.95          # same product, different spelling / word order
CACHE = "data/value_embeddings.json"
_V = None


class Vocab:
    def __init__(self):
        q = C.q
        self.products = [b for (b,) in q("SELECT DISTINCT product_base FROM deals_enriched ORDER BY 1")]
        self.regions = [r for (r,) in q("SELECT DISTINCT region FROM deals ORDER BY 1")]
        self.enums = {
            "status": [v for (v,) in q("SELECT DISTINCT status FROM deals ORDER BY 1")],
            "contract_type": [v for (v,) in q("SELECT DISTINCT contract_type FROM deals ORDER BY 1")],
            "contract_form": [v for (v,) in q("SELECT DISTINCT contract_form FROM deals ORDER BY 1")],
            "auction_type": [v for (v,) in q("SELECT DISTINCT auction_type FROM deals ORDER BY 1")],
            "unit": [v for (v,) in q("SELECT DISTINCT unit FROM deals ORDER BY 1")],
            "product_group": [v for (v,) in q("SELECT DISTINCT product_group FROM contracts_clean ORDER BY 1")],
            "product_category": [v for (v,) in q("SELECT DISTINCT product_category FROM contracts_clean ORDER BY 1")],
            "industry": [v for (v,) in q("SELECT DISTINCT industry FROM contracts_clean ORDER BY 1")],
            "delivery_basis": [v for (v,) in q("SELECT DISTINCT delivery_basis FROM contracts_clean ORDER BY 1")],
            "packaging": [v for (v,) in q("SELECT DISTINCT packaging FROM contracts_clean ORDER BY 1")],
            "activity": [v for (v,) in q('SELECT DISTINCT "Вид деятельности" FROM participants ORDER BY 1')],
            "role": [v for (v,) in q('SELECT DISTINCT "Роль" FROM participants ORDER BY 1')],
        }
        self.emb = _load_embeddings(self.products)


def V():
    global _V
    if _V is None: _V = Vocab()
    return _V


# ------------------------------------------------------------------ embeddings (cached on disk)
def _embed(texts):
    from .gemini import key
    out = []
    for i in range(0, len(texts), 100):
        body = {"requests": [{"model": f"models/{EMB_MODEL}", "content": {"parts": [{"text": t}]}, "taskType": "SEMANTIC_SIMILARITY",
                              "outputDimensionality": 768} for t in texts[i:i + 100]]}
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{EMB_MODEL}:batchEmbedContents",
                                     json.dumps(body).encode(), {"content-type": "application/json", "x-goog-api-key": key()})
        r = json.load(urllib.request.urlopen(req, timeout=60))
        out += [_unit(e["values"]) for e in r["embeddings"]]
    return out


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _load_embeddings(texts):
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    missing = [t for t in texts if t not in cache]
    if missing:
        try:
            for t, v in zip(missing, _embed(missing)): cache[t] = v
            json.dump(cache, open(CACHE, "w"))
        except Exception as ex:
            print("embedding unavailable:", str(ex)[:100])
    return {t: cache[t] for t in texts if t in cache}


def cos(a, b): return sum(x * y for x, y in zip(a, b))


# ------------------------------------------------------------------ product resolution
def _stems(text):
    t = text.lower().replace("ё", "е")
    CODE = r"[а-яa-z]{1,3}-?\d+[а-яa-z]*|\d+[.,]\d+|\d+-?(?:класс\w*|сорт\w*)|\d+"     # аи-92, м00к, pp-0350, 42,5, 4-класс, 4
    codes = re.findall(CODE, t)
    rest = re.sub(CODE, " ", t)
    words = [w[:max(4, len(w) - 2)] for w in re.findall(r"[а-яa-z]+", rest) if len(w) >= 3]
    codes = [re.sub(r"-?(класс|сорт)\w*$", "", c) if re.match(r"\d", c) else c for c in codes]   # "4-класса" -> "4"
    return words + [c for c in codes if c]


def resolve_product(term_ru, picked=()):
    """Registry names a mention refers to. term_ru = the LLM's Russian registry-style term.
    picked = names the LLM chose from the registry list (validated). Returns (names, how)."""
    v = V()
    names = {p for p in picked if p in v.products}
    stems = _stems(term_ru or "")
    lex = []
    if stems:
        head = stems[0]
        first = lambda b: re.findall(r"[а-яёa-z0-9]+", b)[0] if re.findall(r"[а-яёa-z0-9]+", b) else ""
        flat = lambda b: " " + b + " " + b.replace("-", " ") + " " + b.replace("-", "") + " "
        if len(stems) == 1:
            # one word ("сахар", "пшеница", "цемент"): it must be the head word of the name (not "мука пшеничная")
            lex = [b for b in v.products if head in first(b)]
        else:
            def has_tok(x, b):
                if re.fullmatch(r"\d+", x): return re.search(rf"(?<!\d){x}(?!\d)", b) is not None   # "4" ≠ "42,5"
                return x in flat(b) or x.replace("-", "") in flat(b)
            lex = [b for b in v.products if all(has_tok(x, b) for x in stems)]
            if not lex:  # grade words written differently: keep the head match, drop the rest
                lex = [b for b in v.products if head in first(b) and any(has_tok(x, b) for x in stems[1:])] or \
                      [b for b in v.products if head in first(b)]
        names |= set(lex)
    if not names and term_ru and v.emb:
        # nothing by spelling: accept only a near-identical name (same product, different spelling)
        try:
            tv = _embed([term_ru])[0]
            names |= {b for b, e in v.emb.items() if cos(tv, e) >= HIGH}
        except Exception:
            pass
    return sorted(names), ("resolved" if names else "unresolved")


def resolve_region(x):
    v = V()
    if x in v.regions: return x
    from rapidfuzz import process, fuzz
    from .core import name_key
    keys = {name_key(r): r for r in v.regions}
    hit = process.extractOne(name_key(x), list(keys), scorer=fuzz.WRatio, score_cutoff=85)
    return keys[hit[0]] if hit else None
