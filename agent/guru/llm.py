"""The single place non-voice code talks to LLMs (PLAN A5): OpenRouter first, Gemini as fallback.
Embeddings are Gemini-only (switching them would mean re-indexing Qdrant)."""

import asyncio
import hashlib
import logging
from functools import cache
from typing import Literal, TypeVar

from google.genai import Client, types
from openai import AsyncOpenAI
from pydantic import BaseModel

from . import config
from .jsonlog import jlog

Provider = Literal["auto", "openrouter", "gemini"]
M = TypeVar("M", bound=BaseModel)
log = logging.getLogger("guru.llm")

# One bounded fan-out for every LLM call from this process (PLAN §4.8).
_gate = asyncio.Semaphore(8)


@cache
def client() -> Client:
    # SDK-level retry: capped exponential backoff with jitter, on 429/5xx only (PLAN §2).
    retry = types.HttpRetryOptions(attempts=3, initial_delay=0.5, max_delay=8, jitter=1)
    return Client(http_options=types.HttpOptions(retry_options=retry, timeout=60_000))


@cache
def openrouter() -> AsyncOpenAI:
    # No SDK retries: on failure we fall back to Gemini instead of retrying the same provider.
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=config.OPENROUTER_API_KEY,
        max_retries=0,
        default_headers={"X-Title": "Guru"},
    )


async def generate_json(
    model: str,
    prompt: str,
    schema: type[M],
    timeout: float = 60,
    context: str = "",
    session_id: str = "",
    flex: bool = False,
    provider: Provider = "auto",
) -> M:
    """`model` is the Gemini fallback for this role; OpenRouter (config.OPENROUTER_MODEL) goes first.
    Each attempt gets the full `timeout`, so the worst case is about twice that.
    `context` is a shared prefix sent as its own system message so repeated calls hit the prompt cache.
    `session_id` keeps one interview's calls on the same provider (cache hits) and is sent hashed as `user`.
    `flex` asks for OpenRouter's cheaper, slower flex tier; only for work nobody is waiting on.
    `provider` pins one provider with no fallback (the scorer, so one session has one judge)."""
    if provider == "openrouter":
        return await _openrouter_json(prompt, schema, timeout, context, session_id, flex)
    if provider == "gemini":
        return await _gemini_json(model, prompt, schema, timeout, context)
    if config.OPENROUTER_API_KEY:
        try:
            return await _openrouter_json(prompt, schema, timeout, context, session_id, flex)
        except Exception as e:
            # ponytail: no circuit breaker yet, so a dead primary costs one timeout per call.
            # Phase 2 adds the Redis-backed breaker (PLAN §2) to skip it while it's down.
            log.warning("openrouter failed, falling back to gemini: %s", e)
    return await _gemini_json(model, prompt, schema, timeout, context)


async def _openrouter_json(
    prompt: str, schema: type[M], timeout: float, context: str, session_id: str, flex: bool
) -> M:
    messages = [{"role": "system", "content": context}] if context else []
    messages.append({"role": "user", "content": prompt})
    body: dict = {"provider": {"data_collection": "deny", "require_parameters": True}}
    if session_id:
        body["session_id"] = session_id  # sticky provider routing, so the prompt cache is reused
        # Per-user abuse isolation: without it a provider block applies to the whole account.
        body["user"] = hashlib.sha256(session_id.encode()).hexdigest()[:32]
    if flex:
        body["service_tier"] = "flex"  # about half price; falls back only to equal or cheaper endpoints
    async with _gate:  # timeout starts after the slot is acquired, so queueing isn't a failure
        async with asyncio.timeout(timeout):
            resp = await openrouter().chat.completions.create(
                model=config.OPENROUTER_MODEL,
                messages=messages,  # type: ignore[arg-type]
                response_format={
                    "type": "json_schema",
                    # strict needs every field required; our schemas have defaults, so validate below.
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
                },
                extra_body=body,
            )
    if u := resp.usage:
        _log_usage(
            config.OPENROUTER_MODEL,
            schema,
            u.prompt_tokens,
            u.prompt_tokens_details and u.prompt_tokens_details.cached_tokens,
            u.completion_tokens,
            u.completion_tokens_details and u.completion_tokens_details.reasoning_tokens,
            getattr(u, "cost", None),  # OpenRouter extension: USD for this call
            getattr(resp, "service_tier", None),
        )
    text = resp.choices[0].message.content if resp.choices else None
    if not text:
        raise RuntimeError(f"{config.OPENROUTER_MODEL} returned no text")
    return schema.model_validate_json(text)


async def _gemini_json(model: str, prompt: str, schema: type[M], timeout: float, context: str) -> M:
    cfg = types.GenerateContentConfig(
        system_instruction=context or None,
        response_mime_type="application/json",
        response_json_schema=schema.model_json_schema(),
    )
    async with _gate:  # timeout starts after the slot is acquired, so queueing isn't a failure
        async with asyncio.timeout(timeout):
            resp = await client().aio.models.generate_content(model=model, contents=prompt, config=cfg)
    if u := resp.usage_metadata:
        _log_usage(
            model,
            schema,
            u.prompt_token_count,
            u.cached_content_token_count,
            u.candidates_token_count,
            u.thoughts_token_count,
        )
    if not resp.text:  # e.g. blocked by a safety filter
        raise RuntimeError(f"{model} returned no text (finish: {resp.candidates and resp.candidates[0].finish_reason})")
    # Gemini drops some schema constraints server-side, so validate here.
    return schema.model_validate_json(resp.text)


def _log_usage(
    model: str,
    schema: type,
    prompt: int | None,
    cached: int | None,
    output: int | None,
    reasoning: int | None,
    cost: float | None = None,
    tier: str | None = None,
) -> None:
    """Token counts per call, so cost and cache hit rate are visible (cached / prompt)."""
    jlog(
        "LLM usage",
        function_name="generate_json",
        output={
            "model": model,
            "schema": schema.__name__,
            "prompt_tokens": prompt,
            "cached_tokens": cached or 0,
            "output_tokens": output,
            "reasoning_tokens": reasoning or 0,
            "cost_usd": cost,  # None on Gemini: its API doesn't return a price
            "service_tier": tier,
        },
    )


async def embed(texts: list[str]) -> list[list[float]]:
    # Each text must be its own Content: a plain list of strings becomes ONE multi-part
    # content for gemini-embedding-2 and returns a single vector.
    contents: list[types.ContentUnion] = [types.Content(parts=[types.Part(text=t)]) for t in texts]
    cfg = types.EmbedContentConfig(output_dimensionality=config.EMBED_DIM)
    async with _gate:
        async with asyncio.timeout(30):
            resp = await client().aio.models.embed_content(
                model=config.EMBED_MODEL, contents=contents, config=cfg
            )
    vectors = [e.values for e in resp.embeddings or [] if e.values]
    if len(vectors) != len(texts):
        raise RuntimeError(f"expected {len(texts)} embeddings, got {len(vectors)}")
    return vectors
