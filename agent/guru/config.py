"""All tunables in one place. Model IDs are env-driven so they can change without code edits."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Models (PLAN §1). Override via env if a provider renames/retires one.
# Text LLM calls try OpenRouter first and fall back to the Gemini model for that role, except the
# live interviewer, which tries Gemini (INTERVIEWER_MODEL) first because it's faster (interviewer.voice_llms).
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-6-luna")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
INTERVIEWER_MODEL = os.getenv("INTERVIEWER_MODEL", "gemini-3.5-flash-lite")
OBSERVER_MODEL = os.getenv("OBSERVER_MODEL", "gemini-3.5-flash-lite")
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gemini-3.5-flash-lite")
SCORER_MODEL = os.getenv("SCORER_MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-2")
EMBED_DIM = 768
INTERVIEWER_THINKING = os.getenv("INTERVIEWER_THINKING", "minimal")  # Gemini thinking + OpenRouter reasoning_effort
VOICE_MAX_TOKENS = 400  # reasoning + a spoken reply; replies were under 110 tokens, so this only stops runaways

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")  # empty -> plugin default voice
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None  # required for Qdrant Cloud
QDRANT_COLLECTION = "kb_chunks"
PLAN_PATH = Path(os.getenv("PLAN_PATH", ROOT / "fixtures" / "plan.json"))  # console runs (no session)
API_URL = os.getenv("GURU_API_URL", "http://localhost:8080")  # backend: plan by session, results back
SERVICE_TOKEN = os.getenv("GURU_SERVICE_TOKEN", "")  # shared with backend for /internal/*
# The backend's Celery broker: results go out as a queued webhook (interviewer.post_results).
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
OUT_DIR = ROOT / "out"
LOG_FILE = Path(os.getenv("LOG_FILE", ROOT / "logs" / "guru.txt"))  # file sink; console is the other

# Interview timing (PLAN §3.3, Q5). max_duration_sec comes from the job; these are the rest.
CLOSING_SEC = 90
MAX_FOLLOW_UPS = 2
KEEP_CHAT_ITEMS = 16  # rolling window kept when moving to the next question (§3.4)
SILENCE_SEC = 7.0  # no speech from either side for this long -> "Are you still there?"
SILENCE_PROMPTS = 3  # check-ins before ending the interview as a technical issue
RECONNECT_GRACE_SEC = 120.0  # a dropped candidate may rejoin within this; then the interview ends for good

# Turn taking (PLAN §3.5.1). Flux decides end-of-turn; eager threshold enables preemptive LLM.
EOT_THRESHOLD = 0.8  # higher = more patient with thinking pauses
EAGER_EOT_THRESHOLD = 0.5  # must be <= EOT_THRESHOLD
EOT_TIMEOUT_MS = 6000  # max silence before Flux forces end-of-turn
# LiveKit adds min_delay after the STT end-of-turn; Flux already waited, so keep it small.
ENDPOINTING_MIN_DELAY = 0.2
# LiveKit skips preemption for speech longer than 10 s by default; interview answers run longer.
PREEMPT_MAX_SPEECH_SEC = 120.0
PREEMPT_MAX_RETRIES = 5

# Company Q&A grounding (PLAN §3.8). Tuned on evals/qa_eval.py.
RAG_TOP_K = 4
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.5"))  # min dense cosine of best hit
SCOPE_TIMEOUT_SEC = float(os.getenv("SCOPE_TIMEOUT_SEC", "5"))  # live latency budget; fails closed
