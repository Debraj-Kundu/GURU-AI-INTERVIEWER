"""The single place non-voice code talks to Gemini (PLAN A5). Add a provider here when one lands."""

import asyncio
from functools import cache
from typing import TypeVar

from google.genai import Client, types
from pydantic import BaseModel

from . import config

M = TypeVar("M", bound=BaseModel)

# One bounded fan-out for every Gemini call from this process (PLAN §4.8).
_gate = asyncio.Semaphore(8)


@cache
def client() -> Client:
    # SDK-level retry: capped exponential backoff with jitter, on 429/5xx only (PLAN §2).
    retry = types.HttpRetryOptions(attempts=3, initial_delay=0.5, max_delay=8, jitter=1)
    return Client(http_options=types.HttpOptions(retry_options=retry, timeout=60_000))


async def generate_json(model: str, prompt: str, schema: type[M], timeout: float = 60) -> M:
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
