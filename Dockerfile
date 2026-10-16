# HeyDesk's backend. Two stages: uv installs the locked dependencies, then a slim image runs
# them as a user without root. The database and photos live in /data (a volume).

FROM ghcr.io/astral-sh/uv:0.12.5-python3.12-trixie-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# The dependencies first, so a code change doesn't reinstall them
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:3.12-slim-trixie
RUN useradd --create-home --uid 1000 heydesk && mkdir /data && chown heydesk /data
WORKDIR /app
COPY --from=build --chown=heydesk /app /app
USER heydesk
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/heydesk.db \
    MEDIA_DIR=/data/media \
    QDRANT_LOCAL_PATH=/data/qdrant
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
# Migrations first, so a new image brings its database up to date on start
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn heydesk.main:app --host 0.0.0.0 --port 8000"]
