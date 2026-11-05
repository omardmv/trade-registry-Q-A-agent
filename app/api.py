"""HTTP API: POST /ask, GET /health, static chat UI."""
import logging
import threading
from collections import OrderedDict
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.pipeline import Pipeline, build_pipeline

log = logging.getLogger(__name__)
app = FastAPI(title="Exchange Q&A")
INTERNAL_FIELDS = ("reason", "unsupported", "trace")


class Previous(BaseModel):
    question: str = Field(default="", max_length=1000)
    sql: str = Field(default="", max_length=20000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    previous: Previous | None = None


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    return build_pipeline()


@app.on_event("startup")
def warm_up() -> None:
    """Open the database and build the planner before the first question, so nobody waits for it."""
    try:
        get_pipeline()
    except Exception:  # a missing key must not stop the server; /ask reports it
        log.exception("warm-up failed")


# Same question again (no follow-up context) -> the same answer at once: the data does not change while the
# server runs. Only answered / no-data results are kept, never errors or time-outs.
_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_CACHE_SIZE = 1000
_CACHE_LOCK = threading.Lock()


def _cache_key(question: str) -> str:
    return " ".join(question.lower().split())


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.post("/ask")
def ask(body: AskIn, pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    previous = body.previous.model_dump() if body.previous else None
    key = _cache_key(body.question) if previous is None else None
    if key is not None:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit is not None:
                _CACHE.move_to_end(key)
                return {**hit, "timings": {"total_s": 0.0}, "cached": True}
    out = asdict(pipeline.ask(body.question.strip(), previous))
    for internal in INTERNAL_FIELDS:  # kept for logs / eval only
        out.pop(internal, None)
    if key is not None and out.get("source") not in ("failed", "timeout", "error"):
        with _CACHE_LOCK:
            _CACHE[key] = out
            while len(_CACHE) > _CACHE_SIZE:
                _CACHE.popitem(last=False)
    return out


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
