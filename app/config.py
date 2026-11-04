import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """KEY=VALUE -> (key, value). Accepts an `export` prefix (any whitespace), matched single/double quotes
    (a # inside them is kept) and a trailing ` # comment` after an unquoted value; None if the line is not
    usable (comment, no key, unterminated quote, junk after the closing quote)."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if key.startswith("export") and key[6:7].isspace():
        key = key[6:].strip()
    value = value.strip()
    if not key or any(c.isspace() for c in key):
        return None
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        rest = value[end + 1:].strip() if end > 0 else None
        if rest is None or (rest and not rest.startswith("#")):
            return None
        return key, value[1:end]
    for i, ch in enumerate(value):  # a comment starts at whitespace followed by #
        if ch == "#" and (i == 0 or value[i - 1].isspace()):
            value = value[:i].rstrip()
            break
    return key, value


def load_dotenv(path: Path) -> None:
    """KEY=VALUE lines from a .env file into os.environ; variables already set in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            os.environ.setdefault(*parsed)


load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.environ.get("UZEX_DATA_DIR", ROOT / "data"))
DICT_DIR = ROOT / "data_dictionary"
DB_PATH = Path(os.environ.get("UZEX_DB_PATH", ROOT / "build" / "uzex.duckdb"))
EXAMPLES_PATH = ROOT / "examples" / "examples.yaml"
SEMANTIC_PATH = ROOT / "app" / "semantic.yaml"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_THINKING_BUDGET = int(os.environ.get("GEMINI_THINKING_BUDGET", "1024"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low").strip()  # empty: do not send
LLM_PROVIDERS = [p.strip().lower() for p in os.environ.get("LLM_PROVIDERS", "gemini,openai").split(",") if p.strip()]

DEADLINE_S = float(os.environ.get("UZEX_DEADLINE_S", "75"))
LLM_TIMEOUT_S = float(os.environ.get("UZEX_LLM_TIMEOUT_S", "15"))
MAX_TOOL_CALLS = 8
MAX_FIX_RETRIES = 2
ROW_LIMIT = 1000
