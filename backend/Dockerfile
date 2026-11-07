# syntax=docker/dockerfile:1
# API, outbox relay and parse/embed workers, for deploy/docker-compose.yml.
# Build context: the repo root, because the backend installs the agent package from ../agent.
# Secrets are never copied in (backend/Dockerfile.dockerignore drops .env files); compose passes
# them with env_file.
FROM python:3.12.10-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed
WORKDIR /app

# Dependencies first (backend/uv.lock), so a code change doesn't reinstall them.
COPY agent/pyproject.toml agent/uv.lock agent/README.md agent/
COPY backend/pyproject.toml backend/uv.lock backend/
RUN --mount=type=cache,target=/root/.cache/uv cd backend && \
    uv sync --frozen --no-dev --no-install-project --no-install-package guru-agent
COPY agent agent
COPY backend backend
RUN --mount=type=cache,target=/root/.cache/uv cd backend && uv sync --frozen --no-dev
WORKDIR /app/backend
RUN python -c "from guru import kb; kb.bm25()"  # the BM25 model, used by the embed worker
CMD ["fastapi", "run", "app/main.py", "--port", "8080"]
