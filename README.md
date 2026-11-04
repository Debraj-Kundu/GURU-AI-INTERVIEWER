# Guru

An AI voice interviewer. A recruiter sets up a job (questions, scoring criteria, company
knowledge base), Guru plans the interview, and a candidate joins an audio-only call from an invite
link. A LiveKit voice agent runs the interview; afterwards the transcript is scored per criterion
for the recruiter to review. Scores are advisory and need a human decision.

| Folder | What | Stack |
|---|---|---|
| [`web/`](web/) | Recruiter dashboard and the candidate interview page | Next.js 15, TypeScript, LiveKit Agents UI |
| [`backend/`](backend/) | REST API: jobs, knowledge-base upload and ingestion, plans, invite links, scoring | FastAPI, SQLAlchemy (async), Alembic |
| [`agent/`](agent/) | The voice interviewer, plus the planner, scorer and knowledge-base code (`guru` package) | LiveKit Agents (Python) |
| [`deploy/`](deploy/) | Local infrastructure | Docker Compose: Postgres 18, Floci (S3) |
| [`docs/`](docs/) | Diagram sources (Mermaid `.mmd`, Archify JSON) | |

Background design and decisions: [PLAN.md](PLAN.md).

## Contents
- [Architecture](#architecture)
- [Flows](#flows)
- [Database schema](#database-schema)
- [Getting started](#getting-started)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Diagram sources](#diagram-sources)

## Architecture

```mermaid
flowchart LR
  recruiter(["Recruiter"]) -->|HTTPS| web
  candidate(["Candidate"]) -->|invite link| web

  subgraph local["Runs locally"]
    web["Next.js web<br/>/jobs, /interview/id"]
    api["FastAPI<br/>jobs, ingest, plans, scoring"]
    agent["Interviewer agent<br/>guru.interviewer worker"]
    subgraph docker["docker compose"]
      pg[("PostgreSQL")]
      s3[("S3: Floci")]
    end
  end

  subgraph cloud["Cloud services"]
    lk["LiveKit Cloud<br/>audio room"]
    qd[("Qdrant Cloud<br/>KB chunks")]
    llm["LLMs<br/>OpenRouter, Gemini"]
    speech["Speech<br/>Deepgram Flux, ElevenLabs"]
  end

  web -->|REST JSON| api
  web -->|presigned PUT| s3
  web <-->|WebRTC audio| lk
  api -->|SQL| pg
  api -->|boto3| s3
  api -->|upsert chunks| qd
  api -->|plan, score, embed| llm
  lk -->|dispatch| agent
  agent -->|"/internal: start, complete"| api
  agent -->|voice LLM, Observer| llm
  agent -->|lookup_tool| qd
  agent -->|STT, TTS| speech
```

| Part | Responsibility |
|---|---|
| **Web** | Recruiter screens (job editor, knowledge base, plan preview, interview links, session review) and the candidate page (consent, mic check, audio room). Calls only the API; it never holds LiveKit keys. |
| **API** | Source of truth in Postgres. Issues presigned upload URLs and LiveKit tokens, runs background work (ingest, plan generation, scoring) in-process, and exposes `/internal/*` to the agent behind a service token. |
| **Agent** | One process per interview. Claims the session (`POST /internal/sessions/{id}/start`), runs consent, questions and follow-ups, and the closing Q&A, then posts the transcript and Observer notes back (`/complete`). |
| **Floci** | Local S3 emulator; uploads go browser → S3 directly. Swap for AWS S3 by changing `S3_ENDPOINT` and keys. |
| **Qdrant** | Company knowledge base (one collection, `kb_chunks`, filtered by `org_id` and `job_id`), dense + BM25 vectors. Never holds candidate data. |
| **LLMs** | OpenRouter (`openai/gpt-6-luna`) for every text call; Gemini as fallback and for embeddings. |

Interactive version with pan, zoom and guided views: open
[docs/architecture/system.html](docs/architecture/system.html) in a browser (after cloning).

## Flows

### End-to-end, as a user

1. **Recruiter** creates a job: details, question bank (form or CSV import), scoring criteria.
2. Adds knowledge-base sources (PDF, DOCX, TXT, MD, CSV or a web link) and waits for `ready`.
3. Generates the interview plan and reviews which questions were picked or skipped.
4. Creates an interview link and sends it to the candidate.
5. **Candidate** opens `/interview/<id>`, ticks the consent boxes, passes the mic check, and talks to Guru.
6. The session becomes `completed` and is scored in the background.
7. **Recruiter** opens the session: per-criterion scores with evidence that links to transcript turns.

### Knowledge-base upload and ingestion

```mermaid
sequenceDiagram
  autonumber
  actor R as Recruiter
  participant W as Next.js (kb-panel.tsx)
  participant A as FastAPI (jobs.py)
  participant S3 as S3 (Floci)
  participant I as ingest() background task
  participant P as Process pool (parsers.parse)
  participant Q as Qdrant (kb.py)

  alt File (PDF, DOCX, TXT, MD, CSV)
    R->>W: drop file
    W->>A: POST /jobs/{id}/sources/upload {filename, content_type, size_bytes}
    A->>A: limits (20 MB, 50 sources), SourceRow status=uploading
    A-->>W: {source, upload_url, headers} (presign_put)
    W->>S3: PUT upload_url (file bytes, browser to storage)
    alt PUT failed
      W->>A: DELETE /sources/{id}
    else PUT ok
      W->>A: POST /sources/{id}/uploaded
      A->>S3: size_of(key)
      alt larger than 20 MB
        A->>S3: delete(key)
        A-->>W: status=failed
      else
        A->>I: spawn(ingest(id)), status=queued
      end
    end
  else Website link
    R->>W: paste URL
    W->>A: POST /jobs/{id}/sources/url {url}
    A->>I: spawn(ingest(id)), status=queued
  end

  Note over I: Semaphore(2) limits concurrent ingests
  I->>I: status=parsing
  alt kind == url
    I->>I: fetch_page(url): SSRF check per hop, robots.txt, 5 MB, 10 s
  else file
    I->>S3: get_bytes(key)
  end
  I->>I: sha256, same content already ready in this job?
  alt duplicate
    I->>I: status=duplicate
  else new content
    I->>P: parse(kind, data, name)
    P-->>I: chunks, excerpt (or ParseError -> status=failed)
    I->>I: status=embedding
    I->>Q: delete_source(job, source_id)
    I->>Q: upsert(job, name, chunks, source_id) (Gemini embeddings, batches of 64)
    I->>I: status=ready, chunks, excerpt
  end
  loop every few seconds (usePoll)
    W->>A: GET /jobs/{id}/sources
  end
```

Source statuses:

```mermaid
stateDiagram-v2
  [*] --> uploading : POST /jobs/{id}/sources/upload
  [*] --> queued : POST /jobs/{id}/sources/url
  uploading --> queued : POST /sources/{id}/uploaded, object found
  uploading --> failed : object larger than 20 MB
  queued --> parsing : ingest() starts
  parsing --> duplicate : same sha256 already ready in job
  parsing --> embedding : parse() returned chunks
  parsing --> failed : ParseError or fetch error
  embedding --> ready : kb.upsert done
  embedding --> failed : Qdrant or embedding error
  failed --> queued : POST /sources/{id}/retry
  duplicate --> queued : POST /sources/{id}/retry
  ready --> [*] : DELETE /sources/{id}, any state (S3 object + Qdrant points removed)
  note right of queued
    API restart: queued, parsing and embedding
    are re-run by requeue_unfinished()
  end note
```

### Plan generation

```mermaid
sequenceDiagram
  autonumber
  actor R as Recruiter
  participant W as Next.js (plan-panel.tsx)
  participant A as FastAPI (jobs.py)
  participant DB as Postgres
  participant T as _plan() background task
  participant L as OpenRouter (flex)

  R->>W: Generate plan
  W->>A: POST /jobs/{id}/plan
  alt no questions or no criteria
    A-->>W: 422
  else already generating
    A-->>W: 409 (single-flight per job)
  else
    A->>DB: PlanRow job_version=job.version, status=generating
    A->>T: spawn(_plan(job_id, version))
    A-->>W: 202 {status: generating}
  end
  T->>DB: excerpts of ready sources
  T->>L: make_plan(job, summary)
  alt ok
    L-->>T: InterviewPlan
    T->>DB: status=ready, plan (only if job_version still matches)
  else error
    T->>DB: status=failed, error
  end
  loop while generating (usePoll)
    W->>A: GET /jobs/{id}/plan
    A-->>W: {status, stale: job_version != job.version, plan}
  end
  Note over A,DB: PUT /jobs/{id} bumps job.version, so the saved plan becomes stale.<br/>Creating an interview needs a ready, non-stale plan (409 otherwise).
```

### Live interview

```mermaid
sequenceDiagram
  autonumber
  actor R as Recruiter
  actor C as Candidate
  participant W as Next.js (/interview/[id])
  participant A as FastAPI (sessions.py)
  participant DB as Postgres
  participant LK as LiveKit Cloud
  participant G as Agent (guru.interviewer)
  participant X as OpenRouter, Deepgram, ElevenLabs, Qdrant

  R->>A: POST /jobs/{id}/sessions {candidate_name}
  A->>DB: SessionRow status=created, plan snapshot, room interview-{id}
  A-->>R: invite link /interview/{id}
  C->>W: open link
  W->>A: GET /interviews/{id} (public fields only)
  C->>W: 3 consent boxes + mic check, Start
  W->>A: POST /interviews/{id}/token
  A->>DB: status created: new room interview-{id}-{rand} (active: same room, for a rejoin)
  A-->>W: {server_url, participant_token, started_at} (microphone only, dispatch "interviewer", departure_timeout 150 s)
  W->>LK: connect (useSession + TokenSource.custom)
  alt no agent joins in time
    W->>C: "We couldn't start your interview", Try again (status stays created)
  end
  LK->>G: dispatch job (metadata = session id)
  G->>A: POST /internal/sessions/{id}/start {room} (X-Service-Token)
  A->>DB: status=active, started_at (409 if already started or ended: agent deletes room, reports nothing)
  A-->>G: plan snapshot
  opt candidate disconnects
    W->>C: "You've been disconnected", Rejoin
    G->>G: wait RECONNECT_GRACE_SEC (120 s): rejoin = welcome back + repeat question, else shutdown
  end
  G->>C: ConsentTask (voice)
  alt consent declined, or session ends first
    G->>A: POST /internal/sessions/{id}/complete {consented: false}
    A->>DB: status=declined, nothing kept
  else consent given
    loop each planned question
      C->>G: answer (Deepgram Flux STT)
      G->>X: voice LLM reply, Observer note, lookup_tool (Qdrant)
      G->>C: reply (ElevenLabs TTS)
      opt 7 s silence
        G->>C: "Are you still there?" (max 3, then goodbye + shutdown)
      end
    end
    G->>G: ClosingAgent, timer or candidate_wants_to_end
    G->>A: POST /internal/sessions/{id}/complete {turns, notes, usage, duration_sec}
    A->>DB: status=completed, score_status=scoring
    A->>A: spawn(score(id)): scorer.score_session via OpenRouter flex
    A->>DB: score, score_status=scored (or failed)
  end
  R->>A: GET /sessions/{id} (session-review.tsx), optional POST /sessions/{id}/score
```

One attempt per interview: until an agent has started, every try gets a new LiveKit room (a token
only dispatches the agent when it creates the room). Once the agent starts, a dropped candidate can
rejoin the same room for 2 minutes; after that the interview ends and the link stops working.

Interview and scoring statuses:

```mermaid
stateDiagram-v2
  state "status" as status {
    [*] --> created : POST /jobs/{id}/sessions (needs ready, non-stale plan)
    created --> active : agent POST /internal/.../start
    active --> completed : /internal/.../complete consented=true
    active --> declined : /internal/.../complete consented=false
    completed --> [*]
    declined --> [*]
  }
  state "score_status" as score {
    [*] --> none
    none --> scoring : completed with at least one turn
    scoring --> scored : score_session ok
    scoring --> failed : scorer error
    scored --> scoring : POST /sessions/{id}/score
    failed --> scoring : POST /sessions/{id}/score
  }
  note right of status
    Token requests for completed or declined
    sessions get 409. Scoring left in "scoring"
    is resumed when the API starts.
  end note
```

### Inside the agent

```mermaid
flowchart LR
  subgraph api["Backend API"]
    start["POST /start<br/>claim session, plan"]
    complete["POST /complete<br/>store, score_session"]
  end

  subgraph helpers["Helpers"]
    observer["Observer<br/>one call per answer"]
    lookup["kb.lookup<br/>scope LLM + Qdrant"]
    timer["run_timer<br/>time_up, then cap"]
  end

  subgraph live["Voice session"]
    consent["ConsentTask"] -->|consent_given| interviewer["InterviewerAgent<br/>per-question loop"]
    interviewer -->|handoff| closing["ClosingAgent<br/>candidate Q and A"]
    closing -->|end_interview| report["Report<br/>on shutdown"]
  end

  subgraph exits["Forced exits"]
    used["Already used<br/>leave, no report"]
    declined["Declined<br/>say DECLINED"]
    gone["Candidate gone<br/>silence or no rejoin"]
    cap["Hard cap<br/>say GOODBYE"]
  end

  start -->|plan| consent
  report -->|results| complete
  interviewer -.->|each answer| observer
  interviewer -.->|company Q| lookup
  closing -.->|company Q| lookup
  interviewer -.->|starts at consent| timer
  start -.->|409| used
  consent -.->|consent_declined| declined
  interviewer -.->|unresponsive| gone
  timer -.->|cap reached| cap
  declined & gone & cap -.->|shutdown| report

  classDef exit stroke:#e11d48,stroke-width:2px
  class used,declined,gone,cap exit
```

Interactive version with notes: [docs/architecture/agents.html](docs/architecture/agents.html) (after
cloning). The same flow as states, with timings and follow-up rules:

```mermaid
stateDiagram-v2
  [*] --> Prewarmed : prewarm loads VAD, LLM chain, BM25
  Prewarmed --> Rejected : POST /internal/.../start returns 409
  Rejected --> [*] : delete room, report nothing
  Prewarmed --> Consent : start claims the session, session.start, ConsentTask

  Consent --> Declined : consent_declined
  Declined --> Ended : say DECLINED, shutdown
  Consent --> Interviewing : consent_given, consented=true, timer starts

  state Interviewing {
    [*] --> Asking : idx=0, answers=[]
    Asking --> Listening : question spoken
    Listening --> Answered : turn committed, answers += text, Observer runs async
    Answered --> Decide
    state Decide <<choice>>
    Decide --> FollowUp : LLM asks follow-up, allowed while answers below 2
    Decide --> Advance : LLM calls advance_to_next_question
    FollowUp --> Listening : follow-up spoken
    Advance --> Asking : more questions, idx += 1, answers=[], note cleared
    Advance --> [*] : last question or time_up
  }

  Interviewing --> Closing : handoff to ClosingAgent (or candidate_wants_to_end)
  Interviewing --> Ended : hard cap, 3 unanswered check-ins, no rejoin in 120 s

  state Closing {
    [*] --> InviteQuestions : on_enter asks for questions
    InviteQuestions --> Lookup : company question
    Lookup --> InviteQuestions : ANSWER or NO_ANSWER or OUT_OF_SCOPE reply
    InviteQuestions --> [*] : no more questions, end_interview
  }

  Closing --> Ended : shutdown drain=true
  Closing --> Ended : hard cap, check-ins, no rejoin

  Ended --> Reported : room deleted, save_transcript if consented, POST /internal/.../complete
  Reported --> [*]

  note right of Interviewing
    Prompt forces advance when answers reach
    MAX_FOLLOW_UPS (2), the question is over
    its time budget, or time_up is set.
    The Observer note shapes the NEXT reply.
    7 s of silence: "Are you still there?" (max 3),
    then "Seems like there's a technical issue".
    Candidate drops: wait 120 s for a rejoin,
    then welcome back and repeat the question.
  end note

  note right of Closing
    Timer: at cap minus 90 s sets time_up,
    at cap interrupts, says GOODBYE,
    then shuts down.
  end note
```

## Database schema

```mermaid
erDiagram
  jobs ||--o{ kb_sources : "knowledge base"
  jobs ||--o| interview_plans : "latest plan"
  jobs ||--o{ interview_sessions : "interview links"

  jobs {
    string id PK "job_..."
    string org_id "indexed, org_default until auth"
    string title
    string company_name
    text jd_text
    int max_duration_sec "300 to 3600, default 600"
    jsonb questions "question bank"
    jsonb criteria "weights and 1-5 anchors"
    int version "bumped on edit, makes plans stale"
    timestamptz created_at
    timestamptz updated_at
  }

  kb_sources {
    string id PK "src_..."
    string org_id
    string job_id FK "cascade delete"
    string kind "pdf, docx, txt, md, csv, url"
    string name "file name or URL"
    string s3_key "org/job/src/file, files only"
    text url "links only"
    int size_bytes
    string sha256 "indexed, duplicate check"
    string status "uploading to ready, failed, duplicate"
    text error
    int chunks "points in Qdrant"
    text excerpt "feeds the planner"
    timestamptz created_at
  }

  interview_plans {
    string job_id PK, FK "one row per job"
    int job_version "stale when not equal to jobs.version"
    string status "generating, ready, failed"
    jsonb plan "job snapshot + questions"
    text error
    timestamptz updated_at
  }

  interview_sessions {
    string id PK "32 hex, in the invite link"
    string org_id
    string job_id FK "cascade delete"
    string candidate_name
    string room_name "new per try until the agent starts"
    string status "created, active, completed, declined"
    jsonb plan "snapshot at link creation"
    jsonb transcript "turns"
    jsonb notes "Observer notes"
    jsonb usage "tokens, latency"
    float duration_sec
    string score_status "none, scoring, scored, failed"
    jsonb score "per-criterion scores + evidence"
    text score_error
    timestamptz created_at
    timestamptz started_at "set when the agent starts"
    timestamptz ended_at
  }
```

Questions, criteria, plans, transcripts and scores are JSONB because they are always read and
written whole. Outside Postgres:

| Store | Key / layout | Holds |
|---|---|---|
| S3 bucket `guru-uploads` | `{org_id}/{job_id}/{source_id}/{file}` | Uploaded knowledge-base files |
| Qdrant collection `kb_chunks` | payload `org_id`, `job_id`, `source_id`, `source`, `text` | Embedded chunks; deleted per source |
| `agent/out/{session_id}.json` | one file per consented interview | Local backup of the results the agent posts |

## Getting started

### Prerequisites

- **Docker** with the Compose plugin (`docker compose version` works)
- **[uv](https://docs.astral.sh/uv/)** (installs Python 3.12+ for you)
- **Node.js 24** and npm
- Accounts and API keys: [LiveKit Cloud](https://cloud.livekit.io), [Deepgram](https://deepgram.com),
  [ElevenLabs](https://elevenlabs.io), [OpenRouter](https://openrouter.ai),
  [Google AI Studio](https://aistudio.google.com) (Gemini), [Qdrant Cloud](https://cloud.qdrant.io)

Ports used: `3000` web, `8080` API, `5432` Postgres, `4566` Floci.

### 1. Start Postgres and S3

From the repo root:

```bash
docker compose -f deploy/docker-compose.yml up -d
```

```bash
docker compose -f deploy/docker-compose.yml ps
```

Both services should show `running` (Postgres also `healthy`). Data lives in Docker volumes, so it
survives restarts.

### 2. Add your keys

```bash
cp agent/.env.example agent/.env
```

Fill in `agent/.env`: LiveKit URL, key and secret, Deepgram, ElevenLabs, OpenRouter, Google, and
Qdrant. Then generate the token the agent uses to call the API and paste it into
`GURU_SERVICE_TOKEN`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The backend reads `agent/.env` too, so keys live in one place. Never commit `.env` files.

### 3. Run the API

```bash
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py --port 8080
```

`backend/.env` already matches the Compose services. API docs: http://localhost:8080/docs. On start
the API creates the S3 bucket and the Qdrant collection if they're missing.

### 4. Run the agent (new terminal)

```bash
cd agent
uv sync
uv run python -m guru.interviewer dev
```

Wait for `registered worker` in the output. Keep the API running: the agent fetches each
interview's plan from it and posts results back. Logs also go to `agent/logs/guru.txt`.

To talk to the agent without the web app (uses the sample plan in `agent/fixtures/`):

```bash
uv run python -m guru.interviewer console
```

### 5. Run the web app (new terminal)

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000 and follow [End-to-end, as a user](#end-to-end-as-a-user).

### Stopping

Stop each app with `Ctrl+C`. Stop the containers:

```bash
docker compose -f deploy/docker-compose.yml down
```

To also delete all local data (database and uploads):

```bash
docker compose -f deploy/docker-compose.yml down -v
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Candidate page: "We couldn't start your interview" | No agent joined within about 20 s. Start the agent (step 4) and wait for `registered worker`, then click **Try again**. |
| Agent joins and leaves at once, log says `Interview already started or ended` | That interview link was already used. Create a new link. |
| Browser calls `localhost:8000` or gets CORS errors | The web app was built before `NEXT_PUBLIC_API_URL` changed. `NEXT_PUBLIC_*` values are baked in at build time: restart `npm run dev`, or rerun `npm run build` before `npm start`. |
| `alembic upgrade head` can't connect | Postgres isn't up. Check `docker compose -f deploy/docker-compose.yml ps`. |
| `warning: VIRTUAL_ENV=... does not match the project environment` | Another project's virtualenv is active in that shell. Harmless; run `deactivate` to silence it. |
| Console errors `publisher data channel ... closed unexpectedly` after the goodbye | LiveKit closing the connection when the agent deletes the room. Expected noise. |

## Tests

```bash
cd agent
uv run python -m pytest -q
```

```bash
cd backend
uv run python -m pytest -q
```

```bash
cd web
npx tsc --noEmit
npm run lint
```

## Diagram sources

The diagrams above are copies of these Mermaid files: edit the source, then paste it into the
matching README block. The two architecture diagrams also have an interactive Archify version; keep
both in step.

| Diagram | Source |
|---|---|
| Architecture | [docs/architecture/system.mmd](docs/architecture/system.mmd); interactive: [system.architecture.json](docs/architecture/system.architecture.json) → [system.html](docs/architecture/system.html) |
| Flows | [docs/flows/](docs/flows/) |
| Agent architecture | [docs/architecture/agents.mmd](docs/architecture/agents.mmd); interactive: [agents.workflow.json](docs/architecture/agents.workflow.json) → [agents.html](docs/architecture/agents.html) |
| Agent states | [docs/agent/states.mmd](docs/agent/states.mmd) |
| Database schema | [docs/database.mmd](docs/database.mmd) |

How to regenerate the Archify diagrams: [docs/architecture/UPDATE_PROMPT.md](docs/architecture/UPDATE_PROMPT.md).
