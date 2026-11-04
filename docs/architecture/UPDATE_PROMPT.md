# Architecture diagrams: update prompt

Paste the prompt below into Claude Code from the repo root whenever the code changes.

| Diagram | Source spec (edit this) | Rendered | Archify type |
|---|---|---|---|
| Class map: every class, its fields and methods | `classes.architecture.json` | `classes.html` | architecture |
| Offline CLI call sequence (kb seed, planner, scorer) | `api-offline.sequence.json` | `api-offline.html` | sequence |
| Live interview call sequence (LiveKit worker) | `api-live.sequence.json` | `api-live.html` | sequence |
| Agent architecture: handoffs and flow | `agents.workflow.json` | `agents.html` | workflow (v2) |
| Whole system: web, API, agent, data stores, cloud services | `system.architecture.json` | `system.html` | architecture |

Flow diagrams (Mermaid, edit directly) live in `docs/flows/`: `kb-ingestion.mmd`, `source-status.mmd`,
`plan-generation.mmd`, `interview.mmd`, `session-status.mmd`. Update them when routes, statuses or
background tasks in `backend/app/` or the agent's `/internal` calls change. The Postgres schema is
`docs/database.mmd` (from `backend/app/db.py`) and the agent state machine is `docs/agent/states.mmd`.

The root `README.md` embeds copies of all these Mermaid files. GitHub can't run the Archify viewer,
so the system and agent diagrams also have Mermaid versions for the README:
`docs/architecture/system.mmd` and `agents.mmd`. When `system.architecture.json` or
`agents.workflow.json` changes, update the matching `.mmd` too. After changing any `.mmd`, paste it
into the matching README block.

The JSON specs are the source of truth. Never hand-edit the `.html` files; re-deliver them from the specs.
`*.visual-check.*` files are generated browser evidence and are git-ignored.

---

## Prompt

```
/archify Update the Guru architecture diagrams in docs/architecture/ so they match the current code.
Read docs/architecture/UPDATE_PROMPT.md first. Edit the existing JSON specs in place; keep every
existing id that still exists in code so guided views stay stable. Do not assume; if something is
ambiguous (for example a new endpoint whose caller is unclear), ask me before drawing it.

1. Class map (classes.architecture.json)
   - Scan every class in agent/guru/**/*.py (and any new top-level packages, e.g. a Phase 2 api/).
   - One component per class: label = class name, sublabel = base class, tag = "<file>.py:<line>".
   - One card per class, titled "<Class>(<Base>)  ·  <file>.py", listing EVERY field/class variable
     (with type and default) and EVERY method (async, @function_tool and @property marked, args, return).
   - Connections = composition/ownership (field of that type), awaits, and agent handoffs only.
   - Add/remove components and cards for added/removed classes; update line numbers.
   - Add a boundaries region per source file when a new file contributes classes.

2. API sequences (api-offline.sequence.json, api-live.sequence.json)
   - Draw only calls the code actually makes: CLI entry points (python -m guru.*), the LiveKit
     rtc_session entrypoint, and every outbound call (OpenRouter, Gemini, Qdrant, Deepgram,
     ElevenLabs, LiveKit), plus any HTTP endpoints that now exist in code (e.g. FastAPI routes).
   - When real HTTP endpoints exist, add a new api-http.sequence.json (one message per route,
     "METHOD /path" labels) instead of overloading the two existing diagrams. Remove the
     "no HTTP endpoints yet" card once that is no longer true.
   - Keep each sequence at 14 messages or fewer and 28px+ between message y values; split into
     another file rather than shrinking text.

3. Agent architecture (agents.workflow.json)
   - Every Agent / AgentTask subclass, every handoff (a function_tool returning another Agent,
     an awaited AgentTask, session.shutdown), plus the Observer, the lookup tool, the timer, the
     Planner and the Scorer. Update the edge labels to the real tool/function names.
   - Keep the main path planner -> consent -> interviewer -> closing -> transcript -> scorer
     unless the code changed it.

For each spec: run `node bin/archify.mjs validate <type> <spec> --quality showcase --json`
until all 9 checks pass with 0 errors, then
`node bin/archify.mjs deliver <type> <spec> <same-name>.html --quality showcase --json` and
`node bin/archify.mjs visual-check <html> --json`. Report anything that changed, and any check
that still fails, truthfully.
```

## Known, accepted results

- `visual-check` reports `overflowY` on classes, agents and api-live. The pages scroll vertically
  because the cards (the full member lists) sit below the diagram. Readability passes at every
  viewport. This is expected; don't drop member cards to make it pass.
- No git remote is configured, so Archify `sources` links are unavailable; class nodes carry `file:line` in their tag instead.
  Once an origin exists, the tags can become `components[].sources` with `meta.repository`.
