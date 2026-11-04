import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """KEY=VALUE lines from a .env file into os.environ; variables already set in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


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
LLM_PROVIDERS = [p.strip().lower() for p in os.environ.get("LLM_PROVIDERS", "gemini,openai").split(",") if p.strip()]

DEADLINE_S = float(os.environ.get("UZEX_DEADLINE_S", "75"))
LLM_TIMEOUT_S = float(os.environ.get("UZEX_LLM_TIMEOUT_S", "15"))
MAX_TOOL_CALLS = 8
MAX_FIX_RETRIES = 2
ROW_LIMIT = 1000
