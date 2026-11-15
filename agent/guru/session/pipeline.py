"""Builds the voice pipeline: speech-to-text, the LLMs (with fallback), text-to-speech, turn handling."""

import os

import httpx
from google.genai import types
from livekit.agents import AgentSession, JobProcess, TurnHandlingOptions, inference, llm, tts
from livekit.plugins import cartesia, deepgram, elevenlabs, google, openai
from openai import AsyncOpenAI

from .. import config
from ..http_debug import async_hooks


def voice_llms() -> list[llm.LLM]:
    """The voice LLMs in fallback order (evals/voice_llm_eval.py times each one)."""
    if not config.OPENROUTER_API_KEY:  # same check with_openrouter() made; avoids falling back to OPENAI_API_KEY
        raise ValueError("OPENROUTER_API_KEY is required")
    llms: list[llm.LLM] = [
        # OpenRouter first. Live calls, same job (11 Nov): Gemini first (78d20f9c) took 4.42 s median
        # to the first token, 10 of 18 calls over 4 s, voice-to-voice 5.4 s p50 / 8.6 s p95, 0 cached
        # tokens. OpenRouter first (4c01d7db): 2.26 s median, none over 4 s, 2.6 s p50 / 4.1 s p95,
        # 67% of input tokens cached. evals/voice_llm_eval.py had Gemini faster (1.24 s p50), but it
        # doesn't reproduce live load and rate limits.
        # Plain LLM, not with_openrouter(), which has no max_completion_tokens.
        openai.LLM(
            model=config.OPENROUTER_MODEL,
            # The plugin's default client (livekit/plugins/openai/llm.py), plus header logging. Passing a
            # client makes the plugin ignore api_key/base_url, so they're set here. Gemini voice keeps its
            # default (aiohttp) transport: switching it on the live path isn't worth the risk.
            client=AsyncOpenAI(
                api_key=config.OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
                max_retries=0,
                http_client=httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=15.0, read=5.0, write=5.0, pool=5.0),
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=50, keepalive_expiry=120),
                    event_hooks=async_hooks("openrouter-voice"),
                ),
            ),
            extra_headers={"X-Title": "Guru"},
            # Implicit prompt caching (the default). It was off ("explicit" mode, no breakpoints) while the
            # instructions changed every turn; now they're fixed and per-turn state is added in llm_node.
            extra_body={"provider": {"data_collection": "deny"}},
            # Reasoning was most of the output tokens, and output tokens drive latency (OpenAI latency guide).
            reasoning_effort=config.OPENROUTER_REASONING,  # type: ignore[arg-type]  # validated by the API
            max_completion_tokens=config.VOICE_MAX_TOKENS,
        ),
        # ponytail: a free-tier key allows 15 requests/min on this model, about 2-3 live interviews.
        # Use a paid tier if Gemini goes back to first.
        # ponytail: no Gemini prompt cache hits. Implicit caching needs a prefix of at least 4,096
        # tokens on Gemini 3.x Flash (ai.google.dev/gemini-api/docs/caching); interview requests are
        # ~1-3.5k (0 cached tokens on all calls, session e3cd9607). Explicit `cached_content` isn't
        # worth it at this size; revisit if prompts grow past 4k.
        google.LLM(
            model=config.INTERVIEWER_MODEL,
            temperature=0.4,
            thinking_config=types.ThinkingConfig(
                                    thinking_level=types.ThinkingLevel(config.INTERVIEWER_THINKING.upper())),
            max_output_tokens=config.VOICE_MAX_TOKENS,  # same runaway cap as the OpenRouter entry
        ),
    ]
    return llms


def build_llm() -> llm.LLM:
    # Gemini rejects request deadlines under 10 s; the adapter's 5 s default fails every call.
    return llm.FallbackAdapter(voice_llms(), attempt_timeout=10.0)


def build_tts() -> tts.TTS:
    """TTS_PROVIDER first, the other provider as fallback. A provider without its API key is left out."""
    providers = {
        "elevenlabs": lambda: elevenlabs.TTS(
            model="eleven_flash_v2_5",  # the plugin default (turbo_v2_5) is deprecated
            voice_id=config.ELEVENLABS_VOICE_ID or elevenlabs.DEFAULT_VOICE_ID,
        ),
        "cartesia": lambda: cartesia.TTS(
            model=config.CARTESIA_MODEL,
            **({"voice": config.CARTESIA_VOICE_ID} if config.CARTESIA_VOICE_ID else {}),
        ),
    }
    keys = {"elevenlabs": "ELEVEN_API_KEY", "cartesia": "CARTESIA_API_KEY"}
    if config.TTS_PROVIDER not in providers:
        raise ValueError(f"TTS_PROVIDER must be one of {sorted(providers)}, got {config.TTS_PROVIDER!r}")
    order = [config.TTS_PROVIDER] + [p for p in providers if p != config.TTS_PROVIDER]
    instances = [providers[p]() for p in order if os.getenv(keys[p])]
    if not instances:
        raise ValueError(f"set {' or '.join(keys.values())}")
    # Doesn't switch mid-utterance once audio has played (LiveKit docs: fallback strategies).
    return instances[0] if len(instances) == 1 else tts.FallbackAdapter(instances)


def build_session(proc: JobProcess, keyterms: list[str]) -> AgentSession:
    return AgentSession(
        stt=deepgram.STTv2(
            model="flux-general-en",
            audio_chunk_duration_ms=80,  # Deepgram's recommendation for Flux
            eot_threshold=config.EOT_THRESHOLD,
            eager_eot_threshold=config.EAGER_EOT_THRESHOLD,  # enables preemptive generation
            eot_timeout_ms=config.EOT_TIMEOUT_MS,
            keyterm=keyterms,
            mip_opt_out=True,  # keep candidate audio out of Deepgram's model-improvement program
        ),
        llm=proc.userdata["llm"],
        tts=build_tts(),
        vad=proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            # The audio detector takes precedence over Flux's end-of-turn signal (LiveKit docs: turn
            # detector, Usage). v1 runs on LiveKit Inference (self-hosted: a monthly development
            # allowance, then v1-mini on CPU); its endpointing defaults are 0.3-2.5 s. With "stt",
            # Flux has already waited, so only a small min_delay is added.
            turn_detection=inference.TurnDetector() if config.TURN_DETECTION == "model" else "stt",
            endpointing={} if config.TURN_DETECTION == "model" else {"min_delay": config.ENDPOINTING_MIN_DELAY},
            # Explicit: unset, the SDK uses adaptive only on LiveKit Cloud or in dev mode and silently
            # falls back to VAD in a self-hosted production worker, where "mm-hmm" cuts the interviewer
            # off. Self-hosted use counts toward LiveKit's 40k requests/month development allowance.
            # min_words=1: while the agent speaks, a transcript with no words (noise came through as
            # ".") neither interrupts it nor becomes a turn; one did, and the agent restarted its sentence.
            interruption={"mode": "adaptive", "min_words": 2},
            preemptive_generation={
                "enabled": True,
                "preemptive_tts": False,  # never synthesise or speak before the turn is confirmed
                "max_speech_duration": config.PREEMPT_MAX_SPEECH_SEC,
                "max_retries": config.PREEMPT_MAX_RETRIES,
            },
        ),
        user_away_timeout=config.SILENCE_SEC,  # drives on_silence via the "away" user state
    )
