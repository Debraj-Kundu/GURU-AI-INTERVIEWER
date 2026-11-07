# syntax=docker/dockerfile:1
# The LiveKit interviewer, for deploy/docker-compose.yml. Build context: agent/.
# Secrets are never copied in (.dockerignore drops .env); compose passes them with env_file.
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

# Dependencies first (agent/uv.lock, its own pinned SDK versions), so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
# Bake in model files (Silero VAD and plugin files; the BM25 model) instead of downloading per start.
RUN python -m guru.interviewer download-files && python -c "from guru import kb; kb.bm25()"
CMD ["python", "-m", "guru.interviewer", "start"]
