FROM ghcr.io/astral-sh/uv:0.11.3 AS uv

FROM python:3.13-slim

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/apps/api-lens/.venv/bin:$PATH"

WORKDIR /app/apps/api-lens

COPY apps/api-lens/pyproject.toml apps/api-lens/uv.lock ./
RUN uv sync --locked --no-dev --no-cache

COPY apps/api-lens/app.py ./
COPY apps/api-lens/src/__init__.py ./src/
COPY apps/api-lens/src/api/ ./src/api/
COPY apps/api-lens/src/application/ ./src/application/
COPY apps/api-lens/src/domain/ ./src/domain/
COPY apps/api-lens/src/infrastructure/ ./src/infrastructure/
COPY apps/web/index.html /app/apps/web/index.html
COPY examples/traces/readme-run.timed.sanitized.jsonl /app/examples/traces/readme-run.timed.sanitized.jsonl

RUN install -d -m 0700 -o 10001 -g 10001 /app/.captures

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
