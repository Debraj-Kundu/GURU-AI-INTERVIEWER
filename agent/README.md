# Guru agent (Phase 1)

Voice interviewer worker plus the Planner, Observer, and Scorer agents. Runs from fixture files; Phase 2 replaces them with the API, DB, and queues. Design: [../PLAN.md](../PLAN.md) §3.

## Setup
1. `uv sync`
2. Copy `.env.example` to `.env` and fill in the keys.
3. Qdrant: create a Qdrant Cloud free cluster, then set `QDRANT_URL` and `QDRANT_API_KEY` in `.env`. For a local Qdrant (e.g. Docker in Phase 2), use `http://localhost:6333` and leave the key empty.
4. `uv run python -m guru.interviewer download-files` (VAD and other model files)

## Run, in order
```bash
uv run python -m guru.kb seed fixtures/job.json fixtures/kb          # chunk + embed + upsert the company KB
uv run python -m guru.planner fixtures/job.json fixtures/kb          # writes fixtures/plan.json
uv run python -m guru.interviewer console                            # talk to it with your mic, no frontend
uv run python -m guru.scorer out/<session_id>.json                   # writes out/<session_id>.score.json
```
To test through LiveKit Cloud instead of `console`, run `uv run python -m guru.interviewer dev`. Then dispatch agent `interviewer` to a room, for example from the LiveKit Cloud Agents Playground.

## Checks
```bash
uv run pytest -q                        # offline logic tests, no keys needed
uv run python -m evals.followup_eval    # same question, 3 approaches -> different follow-ups
uv run python -m evals.qa_eval          # company Q&A grounding: 30 cases, exit criteria in PLAN §3.9
```
Each transcript JSON in `out/` also records voice-to-voice latency (p50/p95), how many LLM calls ran, how many preemptive generations were cancelled, and per-model usage.

## Layout
| File | What |
|---|---|
| `guru/interviewer.py` | LiveKit worker: Consent task → Interviewer → Closing, Observer hook, hard-cap timer, metrics, transcript |
| `guru/planner.py` | Job + bank + KB → plan that fits `max_duration_sec` |
| `guru/scorer.py` | Per-criterion parallel scoring + weighted aggregate |
| `guru/kb.py` | Chunking, hybrid Qdrant search, three-layer grounded lookup |
| `guru/llm.py` | The only Gemini client for non-voice calls (retries, timeout, bounded concurrency) |
| `guru/prompts.py` | All prompts, for you to edit |
| `guru/config.py` | Every tunable and model ID |
