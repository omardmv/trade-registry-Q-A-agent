"""HTTP API: POST /ask, GET /health, static chat UI."""
import logging
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


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.post("/ask")
def ask(body: AskIn, pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    previous = body.previous.model_dump() if body.previous else None
    out = asdict(pipeline.ask(body.question.strip(), previous))
    for internal in INTERNAL_FIELDS:  # kept for logs / eval only
        out.pop(internal, None)
    return out


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
