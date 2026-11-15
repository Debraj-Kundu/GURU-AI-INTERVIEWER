"""All tunables in one place. Model IDs are env-driven so they can change without code edits."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Models (PLAN §1). Override via env if a provider renames/retires one.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-6-luna")  # voice interviewer + observer
# The live interviewer tries Gemini first because it's faster (session/pipeline.voice_llms).
INTERVIEWER_MODEL = os.getenv("INTERVIEWER_MODEL", "gemini-3.5-flash-lite")


@dataclass(frozen=True)
class Route:
    """The models that serve one role, in order (llm.generate_json): OpenRouter, then Gemini if it fails."""

    openrouter: str
    gemini: str


# Static routing table: each role gets the cheapest model that does its job well. The observer runs
# live on every answer, so it stays small; planning and judging are offline and need more reasoning.
OBSERVER_ROUTE = Route(OPENROUTER_MODEL, os.getenv("OBSERVER_MODEL", "gemini-3.5-flash-lite"))
PLANNER_ROUTE = Route(
    os.getenv("PLANNER_OPENROUTER_MODEL", "openai/gpt-6.1-sol"),  # once per job: quality over price
    os.getenv("PLANNER_MODEL", "gemini-3.5-flash-lite"),
)
SCORER_ROUTE = Route(
    os.getenv("SCORER_OPENROUTER_MODEL", "openai/gpt-6.1-terra"),  # one call per criterion per candidate
    os.getenv("SCORER_MODEL", "gemini-3.5-flash-lite"),
)
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-2")
EMBED_DIM = 768
INTERVIEWER_THINKING = os.getenv("INTERVIEWER_THINKING", "minimal")  # Gemini thinking_level (voice fallback)
# OpenRouter voice reasoning_effort. gpt-6-luna supports none/low/medium/high/xhigh/max, not "minimal":
# sent "minimal", it still reasoned 16-47 tokens a turn (+~2 s to the first token, session 4c01d7db).
# "none" broke the flow: the model asked a question and called the next tool in the same response,
# never waiting for the candidate (session cd2fde9e). The tools now refuse that, but "low" keeps it stopping.
OPENROUTER_REASONING = os.getenv("OPENROUTER_REASONING", "low")
VOICE_MAX_TOKENS = 400  # reasoning + a spoken reply; replies were under 110 tokens, so this only stops runaways

# Primary TTS: "elevenlabs" or "cartesia". The other one is the fallback (pipeline.build_tts).
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs").lower()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")  # empty -> plugin default voice
CARTESIA_MODEL = os.getenv("CARTESIA_MODEL", "sonic-3")
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")  # empty -> plugin default voice
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None  # required for Qdrant Cloud
QDRANT_COLLECTION = "kb_chunks"
PLAN_PATH = Path(os.getenv("PLAN_PATH", ROOT / "fixtures" / "plan.json"))  # console runs (no session)
API_URL = os.getenv("GURU_API_URL", "http://localhost:8080")  # backend: plan by session, results back
SERVICE_TOKEN = os.getenv("GURU_SERVICE_TOKEN", "")  # shared with backend for /internal/*
# The backend's Celery broker: results go out as a queued webhook (interviewer.post_results).
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
OUT_DIR = ROOT / "out"
# "debug" also logs successful outbound HTTP responses with their headers (guru/http_debug.py);
# errors are always logged.
LOG_HTTP = os.getenv("LOG_HTTP", "").lower()
LOG_FILE = Path(os.getenv("LOG_FILE", ROOT / "logs" / "guru.txt"))  # file sink; console is the other

# Interview timing (PLAN §3.3, Q5). max_duration_sec comes from the job; these are the rest.
CLOSING_SEC = 90
MAX_FOLLOW_UPS = 2
KEEP_CHAT_ITEMS = 16  # rolling window kept when moving to the next question (§3.4)
SILENCE_SEC = 7.0  # silence after the agent finishes a turn for this long -> "Are you still there?"
SILENCE_PROMPTS = 3  # check-ins before ending the interview as a technical issue
RECONNECT_GRACE_SEC = 120.0  # a dropped candidate may rejoin within this; then the interview ends for good

# Turn taking (PLAN §3.5.1). Flux decides end-of-turn; eager threshold enables preemptive LLM.
EOT_THRESHOLD = 0.8  # higher = more patient with thinking pauses
EAGER_EOT_THRESHOLD = 0.5  # must be <= EOT_THRESHOLD
EOT_TIMEOUT_MS = 6000  # max silence before Flux forces end-of-turn
# LiveKit adds min_delay after the STT end-of-turn; Flux already waited, so keep it small.
ENDPOINTING_MIN_DELAY = 0.2  # used only with TURN_DETECTION=stt
# Who decides the candidate finished speaking. "model" (default): LiveKit's audio turn detector
# (inference.TurnDetector), which hears intonation as well as words. Flux alone ended turns on
# unfinished clauses ("...based on the conference type itself,"), and once a cut-off answer moved
# the interview on and the rest was graded under the next question (session 4adbc327).
# "stt": Flux's own end-of-turn, the previous behaviour; keep it to compare or roll back.
TURN_DETECTION = os.getenv("TURN_DETECTION", "model")
# LiveKit skips preemption for speech longer than 10 s by default; interview answers run longer.
PREEMPT_MAX_SPEECH_SEC = 120.0
PREEMPT_MAX_RETRIES = 5

# Company Q&A grounding (PLAN §3.8). Tuned on evals/qa_eval.py.
RAG_TOP_K = 4
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.5"))  # min dense cosine of best hit
LOOKUP_FILLER_SEC = 3.0  # silence during a company lookup before "One moment, let me check that."
# The off-script check runs alongside the voice LLM (first token ~2.3 s median); past this it lets the reply through.
OFF_SCRIPT_TIMEOUT_SEC = float(os.getenv("OFF_SCRIPT_TIMEOUT_SEC", "2"))
SCOPE_TIMEOUT_SEC = float(os.getenv("SCOPE_TIMEOUT_SEC", "5"))  # live latency budget; fails closed
