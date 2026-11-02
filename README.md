# Guru

AI voice interviewer. See [PLAN.md](PLAN.md) for the architecture.

| Folder | What | Run |
|---|---|---|
| `deploy/` | Postgres + MinIO (S3) for local dev | `docker compose -f deploy/docker-compose.yml up -d` |
| `backend/` | FastAPI: jobs, KB upload + ingestion, plans, interview links, scoring | see below |
| `agent/` | LiveKit voice interviewer worker | `uv run python -m guru.interviewer dev` |
| `web/` | Next.js recruiter dashboard + candidate interview page | `npm run dev` |

## First run

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then start the infrastructure:
   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   ```
2. Secrets: `agent/.env` holds LiveKit, OpenRouter, Gemini, Qdrant, and `GURU_SERVICE_TOKEN`
   (see `agent/.env.example`). The backend reads them from there too.
3. Backend (http://localhost:8000/docs):
   ```bash
   cd backend
   cp .env.example .env
   uv sync
   uv run alembic upgrade head
   uv run fastapi dev app/main.py
   ```
4. Agent (separate terminal):
   ```bash
   cd agent
   uv run python -m guru.interviewer dev
   ```
5. Web (http://localhost:3000):
   ```bash
   cd web
   cp .env.example .env.local
   npm install
   npm run dev
   ```

## Flow

Jobs → **New job** (details, question bank or CSV import, criteria) → **Knowledge base** (upload
PDF/DOCX/TXT/MD/CSV or add links; wait for `ready`) → **Plan** (generate, review) → **Interviews**
(create link, send to candidate) → candidate opens `/interview/<id>`, consents, talks to Guru →
the session shows up as `completed`, scoring runs automatically → **Review**.

## Tests

```bash
cd agent && uv run python -m pytest -q
cd backend && uv run python -m pytest -q
```
