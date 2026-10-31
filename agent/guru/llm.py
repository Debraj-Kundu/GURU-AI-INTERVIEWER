"""The single place non-voice code talks to LLMs (PLAN A5): OpenRouter first, Gemini as fallback.
Embeddings are Gemini-only (switching them would mean re-indexing Qdrant)."""

import asyncio
import logging
from functools import cache
from typing import TypeVar

from google.genai import Client, types
from openai import AsyncOpenAI
from pydantic import BaseModel

from . import config

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


async def generate_json(model: str, prompt: str, schema: type[M], timeout: float = 60) -> M:
    """`model` is the Gemini fallback for this role; OpenRouter (config.OPENROUTER_MODEL) goes first.
    Each attempt gets the full `timeout`, so the worst case is about twice that."""
    if config.OPENROUTER_API_KEY:
        try:
            return await _openrouter_json(prompt, schema, timeout)
        except Exception as e:
            # ponytail: no circuit breaker yet, so a dead primary costs one timeout per call.
            # Phase 2 adds the Redis-backed breaker (PLAN §2) to skip it while it's down.
            log.warning("openrouter failed, falling back to gemini: %s", e)
    return await _gemini_json(model, prompt, schema, timeout)


async def _openrouter_json(prompt: str, schema: type[M], timeout: float) -> M:
    async with _gate:  # timeout starts after the slot is acquired, so queueing isn't a failure
        async with asyncio.timeout(timeout):
            resp = await openrouter().chat.completions.create(
                model=config.OPENROUTER_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    # strict needs every field required; our schemas have defaults, so validate below.
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
                },
                extra_body={"provider": {"data_collection": "deny", "require_parameters": True}},
            )
    text = resp.choices[0].message.content if resp.choices else None
    if not text:
        raise RuntimeError(f"{config.OPENROUTER_MODEL} returned no text")
    return schema.model_validate_json(text)


async def _gemini_json(model: str, prompt: str, schema: type[M], timeout: float) -> M:
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=schema.model_json_schema(),
    )
    async with _gate:  # timeout starts after the slot is acquired, so queueing isn't a failure
        async with asyncio.timeout(timeout):
            resp = await client().aio.models.generate_content(model=model, contents=prompt, config=cfg)
    if not resp.text:  # e.g. blocked by a safety filter
        raise RuntimeError(f"{model} returned no text (finish: {resp.candidates and resp.candidates[0].finish_reason})")
    # Gemini drops some schema constraints server-side, so validate here.
    return schema.model_validate_json(resp.text)


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
