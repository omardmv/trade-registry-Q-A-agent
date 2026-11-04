"""Curated question → spec/SQL examples, retrieved by character-trigram similarity."""
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.text import normalize_text


@dataclass
class Example:
    question: str
    spec: dict | None = None
    sql: str | None = None
    action: str | None = None
    note: str = ""


def _grams(text: str) -> Counter:
    t = f"  {normalize_text(text)}  "
    return Counter(t[i:i + 3] for i in range(len(t) - 2))


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class ExampleStore:
    def __init__(self, examples: list[Example]):
        self.examples = examples
        self._grams = [_grams(e.question) for e in examples]

    @classmethod
    def from_yaml(cls, path: Path) -> "ExampleStore":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls([Example(r["question"], r.get("spec"), r.get("sql"), r.get("action"), r.get("note", ""))
                    for r in raw])

    def search(self, question: str, k: int = 4) -> list[Example]:
        q = _grams(question)
        ranked = sorted(range(len(self.examples)), key=lambda i: -_cosine(q, self._grams[i]))
        return [self.examples[i] for i in ranked[:k]]
