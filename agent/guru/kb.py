"""Company knowledge base: chunk, embed, store, and grounded lookup (PLAN §3.8, §4.3).

Phase 1 seeds Markdown/TXT fixtures. Phase 2's ingest worker adds PDF/DOCX/HTML/CSV parsing
in front of the same chunk() and upsert path.

Usage: uv run python -m guru.kb seed fixtures/job.json fixtures/kb
"""

import argparse
import asyncio
import re
import uuid
from functools import cache
from pathlib import Path

from fastembed import SparseTextEmbedding
from qdrant_client import AsyncQdrantClient, models

from . import config, llm, prompts
from .models import JobRole, ScopeLabel

CHUNK_CHARS = 2000  # ~512 tokens (Azure AI Search starting point)
OVERLAP_PARAS = 1  # carry the last paragraph into the next chunk (~15-25% overlap)
EMBED_BATCH = 64  # texts per embedding request


def _split_long(p: str, limit: int) -> list[str]:
    """Split a paragraph longer than `limit` on line and sentence ends (PDF pages often have no
    blank lines), packing the pieces back up to `limit`. A piece with no break gets hard-cut."""
    if len(p) <= limit:
        return [p]
    out, buf = [], ""
    for piece in re.split(r"(?<=[.!?])\s+|\n", p):
        for part in (piece[i : i + limit] for i in range(0, len(piece), limit)):
            if buf and len(buf) + 1 + len(part) > limit:
                out.append(buf)
                buf = ""
            buf = f"{buf} {part}" if buf else part
    return out + [buf] if buf else out


def chunk(text: str, title: str) -> list[str]:
    """Split on headings, then pack paragraphs up to CHUNK_CHARS. Each chunk is prefixed with
    its document title and section so it is understandable on its own."""
    chunks = []
    for section in re.split(r"\n(?=#{1,6} )", text.strip()):
        lines = section.strip().splitlines()
        heading = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else ""
        body = "\n".join(lines[1:] if heading else lines)
        prefix = f"{title} > {heading}\n" if heading else f"{title}\n"
        room = CHUNK_CHARS - len(prefix)
        paras = [s for p in re.split(r"\n\s*\n", body) if p.strip() for s in _split_long(p.strip(), room)]
        buf: list[str] = []
        for p in paras:
            if buf and len(prefix) + sum(map(len, buf)) + len(p) > CHUNK_CHARS:
                chunks.append(prefix + "\n\n".join(buf))
                overlap = buf[-OVERLAP_PARAS:]
                buf = overlap if len(prefix) + sum(map(len, overlap)) + len(p) <= CHUNK_CHARS else []
            buf.append(p)
        if buf:
            chunks.append(prefix + "\n\n".join(buf))
    return chunks


@cache
def bm25() -> SparseTextEmbedding:
    return SparseTextEmbedding("Qdrant/bm25")


@cache
def qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY, timeout=5)


def kb_id(job: JobRole) -> str:
    """Knowledge bases belong to a company and are shared by its roles."""
    return job.company_id or job.id


async def ensure_collection() -> None:
    q = qdrant()
    if await q.collection_exists(config.QDRANT_COLLECTION):
        # Added after the first collections were made; creating an existing index is a no-op.
        await q.create_payload_index(config.QDRANT_COLLECTION, "source_id", models.PayloadSchemaType.KEYWORD)
        await q.create_payload_index(config.QDRANT_COLLECTION, "kb_id", models.PayloadSchemaType.KEYWORD)
        return
    await q.create_collection(
        config.QDRANT_COLLECTION,
        vectors_config={"dense": models.VectorParams(size=config.EMBED_DIM, distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    # Index payload fields before data arrives; org_id is the tenant key (Qdrant multitenancy guide).
    await q.create_payload_index(
        config.QDRANT_COLLECTION,
        "org_id",
        models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
    )
    await q.create_payload_index(config.QDRANT_COLLECTION, "kb_id", models.PayloadSchemaType.KEYWORD)
    await q.create_payload_index(config.QDRANT_COLLECTION, "source_id", models.PayloadSchemaType.KEYWORD)


async def upsert(org_id: str, kb: str, source: str, chunks: list[str], source_id: str = "") -> None:
    """`source` is what answers cite (file name or URL); `source_id` (backend uploads) keys deletes."""
    dense: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):  # a 20-page PDF can be 100+ chunks
        dense += await llm.embed(chunks[i : i + EMBED_BATCH])
    sparse = list(bm25().embed(chunks))
    key = source_id or source
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kb}/{key}/{i}")),  # idempotent re-seed
            vector={"dense": d, "bm25": models.SparseVector(indices=s.indices.tolist(), values=s.values.tolist())},
            payload={"org_id": org_id, "kb_id": kb, "source": source, "source_id": key, "text": c},
        )
        for i, (c, d, s) in enumerate(zip(chunks, dense, sparse))
    ]
    await qdrant().upsert(config.QDRANT_COLLECTION, points)


async def delete_source(org_id: str, source_id: str) -> None:
    """Remove every chunk of one source (re-ingest or delete from the UI). Backend source ids are
    unique, so this also clears chunks stored before knowledge bases moved to companies."""
    await qdrant().delete(
        config.QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="org_id", match=models.MatchValue(value=org_id)),
                    models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
                ]
            )
        ),
    )


async def search(job: JobRole, question: str) -> tuple[float, list[dict]]:
    """Hybrid (dense + BM25, RRF-fused) search scoped to the job's company. Also returns the best dense
    cosine score, because RRF scores are rank-based and useless as a relevance threshold."""
    [dense] = await llm.embed([question])
    [s] = list(bm25().query_embed(question))
    sparse = models.SparseVector(indices=s.indices.tolist(), values=s.values.tolist())
    scope = models.Filter(
        must=[
            models.FieldCondition(key="org_id", match=models.MatchValue(value=job.org_id)),
            models.FieldCondition(key="kb_id", match=models.MatchValue(value=kb_id(job))),
        ]
    )
    k = config.RAG_TOP_K
    fused, best = await qdrant().query_batch_points(
        config.QDRANT_COLLECTION,
        [
            models.QueryRequest(
                prefetch=[
                    models.Prefetch(query=dense, using="dense", filter=scope, limit=k * 3),
                    models.Prefetch(query=sparse, using="bm25", filter=scope, limit=k * 3),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=k,
                with_payload=True,
            ),
            models.QueryRequest(query=dense, using="dense", filter=scope, limit=1),
        ],
    )
    top = best.points[0].score if best.points else 0.0
    return top, [p.payload or {} for p in fused.points]


async def lookup(job: JobRole, allowed_tech: list[str], question: str, session_id: str = "") -> dict:
    """Three-layer grounding (PLAN §3.8). Scope check and retrieval run in parallel."""
    async with asyncio.TaskGroup() as tg:
        scope_t = tg.create_task(
            llm.generate_json(
                config.OBSERVER_MODEL,
                prompts.SCOPE.format(allowed=", ".join(allowed_tech), question=question),
                ScopeLabel,
                timeout=config.SCOPE_TIMEOUT_SEC,
                session_id=session_id,
            )
        )
        search_t = tg.create_task(search(job, question))
    label = scope_t.result().label
    top, hits = search_t.result()
    if label in ("future_projects", "tech_outside_jd"):  # OUT_OF_SCOPE wins over ANSWER
        return {"status": "OUT_OF_SCOPE", "chunks": [], "source_urls": []}
    if top < config.RAG_MIN_SCORE or not hits:
        return {"status": "NO_ANSWER", "chunks": [], "source_urls": []}
    return {
        "status": "ANSWER",
        "chunks": [h["text"] for h in hits],
        "source_urls": sorted({h["source"] for h in hits}),
    }


async def seed(job: JobRole, kb_dir: Path) -> None:
    await ensure_collection()
    for path in sorted(kb_dir.glob("*")):
        if path.suffix.lower() not in (".md", ".txt"):
            continue
        chunks = chunk(path.read_text(encoding="utf-8"), title=path.stem)
        await upsert(job.org_id, kb_id(job), path.name, chunks)
        print(f"{path.name}: {len(chunks)} chunks")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["seed"])
    ap.add_argument("job", type=Path)
    ap.add_argument("kb_dir", type=Path)
    args = ap.parse_args()
    job = JobRole.model_validate_json(args.job.read_text(encoding="utf-8"))
    asyncio.run(seed(job, args.kb_dir))


if __name__ == "__main__":
    main()
