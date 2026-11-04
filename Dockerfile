FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY app app
COPY data data
COPY data_dictionary data_dictionary
COPY examples examples
RUN .venv/bin/python -m app.ingest.build
EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
