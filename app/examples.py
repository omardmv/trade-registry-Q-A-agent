"""Curated question → spec/SQL examples. All are given to the planner (render_all); search() is kept for tools."""
import json
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

    def render_all(self) -> str:
        """Every example as compact prompt text. All of them go into the planner's instructions, because
        letter-similarity search picks wrong patterns across Russian and Uzbek."""
        lines = []
        for e in self.examples:
            if e.spec is not None:
                how = "compile_query " + json.dumps(e.spec, ensure_ascii=False, separators=(",", ":"))
            elif e.sql:
                how = "submit_sql " + " ".join(e.sql.split())
            else:
                how = e.action or "declare_unanswerable"
            note = f"  ({e.note})" if e.note else ""
            lines.append(f"- {e.question} -> {how}{note}")
        return "\n".join(lines)

    def search(self, question: str, k: int = 4) -> list[Example]:
        q = _grams(question)
        ranked = sorted(range(len(self.examples)), key=lambda i: -_cosine(q, self._grams[i]))
        return [self.examples[i] for i in ranked[:k]]
