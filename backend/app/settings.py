"""Backend settings. backend/.env holds DB, S3, and service-token values; importing guru.config
also loads agent/.env (LiveKit, OpenRouter, Gemini, Qdrant keys), so those aren't duplicated."""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from guru import config as guru_config  # noqa: E402,F401  (loads agent/.env)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://guru:guru@localhost:5432/guru")

S3_ENDPOINT = os.getenv("S3_ENDPOINT") or None  # None = real AWS S3
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = os.getenv("S3_BUCKET", "guru-uploads")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
AGENT_NAME = "interviewer"  # must match @server.rtc_session(agent_name=...) in agent/guru/interviewer.py

SERVICE_TOKEN = os.getenv("GURU_SERVICE_TOKEN", "")  # shared with the agent for /internal/*
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://localhost:3000")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")  # Celery broker for KB ingest (app/worker.py)
# A source in one ingest stage this long is assumed to have lost its task and is re-queued (app/relay.py).
# Longer than the slowest stage, including its retries (10 + 20 + 40 s of backoff).
STUCK_AFTER = timedelta(minutes=int(os.getenv("INGEST_STUCK_MIN", "10")))
INVITE_TTL_MIN = int(os.getenv("INVITE_TTL_MIN", "10"))  # link expiry if the interview hasn't started

# ponytail: one org until auth lands (PLAN D2); org_id is stored on every row already.
DEFAULT_ORG = "org_default"

# Upload limits (PLAN Q9)
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_SOURCES_PER_COMPANY = 50
FILE_KINDS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "md", ".csv": "csv"}
