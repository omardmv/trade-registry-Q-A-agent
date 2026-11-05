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
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "").strip()  # empty: do not send. gpt-5.4-mini rejects it together with function tools on Chat Completions
# empty: do not send. Set 0 for a local Ollama model: its OpenAI-compatible endpoint defaults to temperature 1.0
# (not the model's own setting), which garbles small models' tool-call JSON
OPENAI_TEMPERATURE = os.environ.get("OPENAI_TEMPERATURE", "").strip()
# 1: tool calls through the prompt (<tool_call> text) instead of the API tools field, for local servers whose tool
# parser drops calls (app.agent.llm_openai.TextToolChat)
OPENAI_TEXT_TOOLS = os.environ.get("OPENAI_TEXT_TOOLS", "").strip().lower() in ("1", "true", "yes")
# Providers tried in this order (fallback chain), by name. Built-in names: gemini, openai, qwen_local (a local
# Ollama model through its OpenAI-compatible endpoint). Any name works when <NAME>_TYPE is set; per provider:
# <NAME>_TYPE (gemini | openai = any OpenAI-compatible server), <NAME>_MODEL, <NAME>_API_KEY, <NAME>_BASE_URL,
# <NAME>_TEMPERATURE, <NAME>_TEXT_TOOLS. Example: LLM_PROVIDERS=qwen_local,gemini,openai
LLM_PROVIDERS = [p.strip().lower() for p in os.environ.get("LLM_PROVIDERS", "gemini,openai").split(",") if p.strip()]
_BUILTIN_PROVIDERS = {
    "gemini": {"type": "gemini"},
    "openai": {"type": "openai"},
    "qwen_local": {"type": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5-7b-uzex",
                   "temperature": "0", "text_tools": "1"},
}
_PROVIDER_FIELDS = ("type", "model", "api_key", "base_url", "temperature", "text_tools")


def provider_settings(name: str) -> dict | None:
    """Settings of a named provider: built-in defaults overridden by <NAME>_* environment variables; None if the
    name is unknown (not built in and no <NAME>_TYPE). text_tools is a bool; the others are strings."""
    name = name.strip().lower()
    out = dict(_BUILTIN_PROVIDERS.get(name, {}))
    prefix = name.upper().replace("-", "_") + "_"
    for field in _PROVIDER_FIELDS:
        value = os.environ.get(prefix + field.upper())
        if value is not None:
            out[field] = value.strip()
    if out.get("type") not in ("gemini", "openai"):
        return None
    defaults = {"gemini": {"model": GEMINI_MODEL, "api_key": GEMINI_API_KEY},
                "openai": {"model": OPENAI_MODEL, "api_key": OPENAI_API_KEY} if name == "openai" else {"model": ""}}
    for field, value in defaults[out["type"]].items():
        out.setdefault(field, value)
    out.setdefault("api_key", "")
    out.setdefault("base_url", "")
    out.setdefault("temperature", OPENAI_TEMPERATURE if name == "openai" else "")
    out["text_tools"] = str(out.get("text_tools", "1" if OPENAI_TEXT_TOOLS and name == "openai" else "")).lower() \
        in ("1", "true", "yes")
    return out

DEADLINE_S = float(os.environ.get("UZEX_DEADLINE_S", "75"))
LLM_TIMEOUT_S = float(os.environ.get("UZEX_LLM_TIMEOUT_S", "15"))
MAX_TOOL_CALLS = 8
MAX_FIX_RETRIES = 2
# the grammar planner has more checks (mentions, guard): more fix rounds, still bounded by the deadline
GRAMMAR_MAX_FIX_RETRIES = int(os.environ.get("UZEX_GRAMMAR_MAX_FIX_RETRIES", "4"))
ROW_LIMIT = 1000
# Planner: legacy (structured form, compile_query) or grammar (small general plan, run_plan; app/plan/)
PLANNER = os.environ.get("PLANNER", "legacy").strip().lower() or "legacy"
