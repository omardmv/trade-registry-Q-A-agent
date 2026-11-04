"""Semantic layer: column meanings, business rules, ambiguity registry, glossary."""
from pathlib import Path

import yaml

from app import config


def load_semantic(path: Path = config.SEMANTIC_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_schema_prompt(sem: dict) -> str:
    out = ["# Data coverage", sem["data_coverage"].strip(), "", "# Tables"]
    for table, info in sem["tables"].items():
        out.append(f"## {table}")
        out.append(info["description"])
        out.extend(f"- {col}: {desc}" for col, desc in info["columns"].items())
        out.append("")
    out.append("# Business rules")
    out.extend(f"- {r}" for r in sem["rules"])
    out.append("")
    out.append("# Ambiguity registry (the only allowed split axes)")
    for axis, info in sem["ambiguities"].items():
        out.append(f"- {axis}: when {info['when']} → {info['default']}")
    out.append("")
    out.append("# Glossary (Uzbek / Russian → schema)")
    out.extend(f"- {g}" for g in sem["glossary"])
    return "\n".join(out)
