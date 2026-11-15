"""Render docs/agent/prompt-flow.md: one real interview from the consent line to the goodbye, in order,
with every string and JSON body sent to an LLM and what came back.

Requests are rebuilt, not copied: the interview's record and log are replayed through the agent's own
prompt builders (interviewer_instructions, current_question_block, prompts.OBSERVER/SCOPE/SCORER) and
LiveKit's own OpenAI formatter (ChatContext.to_provider_format("openai"),
ToolContext.parse_function_tools("openai", strict=True)), with the same body fields the OpenRouter
plugin and llm.generate_json add. Responses come from the log and the stored session.

Inputs:
  --session     JSON {plan, transcript, notes, usage, score, job_id}: one interview_sessions row
  --log         the agent container's log (docker compose logs --no-log-prefix agent)
  --scorer-log  optional: the api container's log, to show the scorer's fallback

Usage (from agent/):  uv run python -m evals.render_prompt_flow --session-id <id> --session s.json \\
                          --log agent.log --scorer-log api.log --out ../docs/agent/prompt-flow.md
"""

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from livekit.agents import llm

from guru import config, kb, prompts
from guru.agents.closing import ClosingAgent
from guru.agents.consent import ConsentTask
from guru.agents.interviewer import InterviewerAgent, current_question_block, interviewer_instructions
from guru.models import CriterionScore, ObserverNote, Plan, ScopeLabel
from guru.states import InterviewState


@dataclass
class Ev:
    t: float  # seconds from the job request
    kind: str
    data: dict = field(default_factory=dict)


KINDS = {
    "Agent said": "agent", "Candidate said": "candidate", "Tool called": "tool", "Observer note": "note",
    "Observer call superseded by a newer turn": "superseded", "LLM metrics": "llm", "LLM usage": "usage",
    "Company lookup result": "lookup", "Time up: next reply moves to closing": "time_up",
    "Candidate disconnected: waiting for a rejoin": "left", "Candidate reconnected": "back",
    "Silence check-in": "checkin", "Session closed": "closed",
}


def parse_log(path: Path) -> list[Ev]:
    evs, t0 = [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg, inner = d.get("message", ""), {}
        if msg.startswith("{"):
            try:
                inner = json.loads(msg)
                msg = inner.get("message", msg)
            except ValueError:
                pass
        ts = datetime.fromisoformat(d["timestamp"])
        if msg == "received job request":
            t0, evs = ts, []  # keep only the last job in the log
        if t0 is None or msg not in KINDS:
            continue
        data = inner.get("output") or {} if inner else {k: v for k, v in d.items() if k not in ("message", "timestamp")}
        evs.append(Ev((ts - t0).total_seconds(), KINDS[msg], data))
    return evs


# ---------------------------------------------------------------- request bodies, as sent


def body_voice(messages: list, tools: list) -> dict:
    """The chat.completions body the LiveKit OpenAI plugin sends to OpenRouter (session/pipeline.py)."""
    b = {"model": config.OPENROUTER_MODEL, "messages": messages}
    if tools:
        b["tools"] = tools
    return b | {"stream": True, "stream_options": {"include_usage": True},
                "max_completion_tokens": config.VOICE_MAX_TOKENS, "reasoning_effort": config.OPENROUTER_REASONING,
                "provider": {"data_collection": "deny"}}  # extra_body is merged into the body by the SDK


def body_json(prompt: str, schema, *, model=config.OPENROUTER_MODEL, context="", session_id="", flex=False, max_tokens=2048) -> dict:
    """The body llm.generate_json sends to OpenRouter (llm._openrouter_json)."""
    messages = [{"role": "system", "content": context}] if context else []
    messages.append({"role": "user", "content": prompt})
    b = {"model": model, "messages": messages, "max_tokens": max_tokens,
         "response_format": {"type": "json_schema", "json_schema": {
             "name": schema.__name__, "schema": schema.model_json_schema(), "strict": False}},
         "provider": {"data_collection": "deny", "require_parameters": True}}
    if session_id:
        b["session_id"] = session_id
        b["user"] = hashlib.sha256(session_id.encode()).hexdigest()[:32]
    if flex:
        b["service_tier"] = "flex"
    return b


def body_gemini(model: str, prompt: str, schema, *, context="", max_tokens=2048) -> dict:
    """The generate_content request llm._gemini_json makes (the fallback)."""
    return {"model": model, "contents": prompt, "config": {
        "system_instruction": context or None, "max_output_tokens": max_tokens,
        "response_mime_type": "application/json", "response_json_schema": schema.model_json_schema()}}


def fence(obj) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False)
    return f"```json\n{text}\n```\n"


def tools_of(agent) -> list:
    return llm.ToolContext(agent.tools).parse_function_tools("openai", strict=True)


def messages_of(ctx: llm.ChatContext) -> list:
    return ctx.to_provider_format("openai")[0]


def said(text: str) -> dict:
    return {"role": "assistant", "content": text}


def tool_call(call_id: str, name: str, arguments: str) -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]}


# ---------------------------------------------------------------- the flow


class Flow:
    def __init__(self) -> None:
        self.steps: list[tuple[float, int, str]] = []

    def add(self, t: float, md: str) -> None:
        self.steps.append((t, len(self.steps), md))

    def line(self, t: float, who: str, text: str, how: str = "") -> None:
        self.add(t, f"**{who}** ({t:.1f} s){' · ' + how if how else ''}\n\n> {text}\n")

    def call(self, t: float, title: str, request: dict, response, note: str = "", stats: str = "") -> None:
        resp = fence(response) if not isinstance(response, str) else response + "\n"
        self.add(t, f"<details open><summary><b>LLM call · {title}</b> ({t:.1f} s)</summary>\n\n"
                    + (f"{note}\n\n" if note else "")
                    + f"**Sent**\n\n{fence(request)}\n**Returned**\n\n{resp}"
                    + (f"\n{stats}\n" if stats else "") + "\n</details>\n")

    def render(self) -> str:
        return "\n".join(md for _, _, md in sorted(self.steps))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=Path, required=True)
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--scorer-log", type=Path)
    ap.add_argument("--session-id", default="")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    rec = json.loads(args.session.read_text(encoding="utf-8"))
    plan = Plan.model_validate(rec["plan"])
    sid = args.session_id
    evs = parse_log(args.log)
    usage = [e for e in evs if e.kind == "usage"]
    flow = Flow()

    s = InterviewState(plan=plan, session_id=sid)
    interviewer, consent = InterviewerAgent(s), ConsentTask(s)
    consent_ctx = llm.ChatContext()
    consent_ctx.add_message(role="system", content=consent.instructions)
    ictx = llm.ChatContext()
    ictx.add_message(role="system", content=interviewer_instructions(s))
    cctx: llm.ChatContext | None = None
    closing = None
    phase, qstart, time_up, clar, note_json, n_calls = "consent", 0.0, False, 0, "", 0
    answered: set[int] = set()  # agent lines that were an LLM's reply (the rest were session.say)

    def block_at(t: float) -> str:
        s.time_up, s.clarifications, s.note = time_up, clar, note_json
        s.question_started_at = time.monotonic() - (t - qstart)  # current_question_block reads the monotonic clock
        return current_question_block(s)

    def next_reply(j: int):
        for e in evs[j + 1:]:
            if e.kind in ("tool", "agent"):
                return e
            if e.kind == "candidate":
                return None
        return None

    def stats(t0: float, t1: float) -> str:
        ms = [e for e in evs if e.kind == "llm" and t0 - 0.05 <= e.t <= t1 + 0.05]
        return ("*Voice-LLM calls in this window (preemptive drafts included):* " + "; ".join(
            f"{m.t:.1f} s: first token {m.data.get('ttft', 0):.2f} s, prompt {m.data.get('prompt_tokens')} tokens "
            f"({m.data.get('prompt_cached_tokens')} cached), {m.data.get('completion_tokens')} out" for m in ms)) if ms else ""

    def voice_response(nxt: Ev | None, call_id: str):
        if nxt is None:
            return ("No agent line was logged before the candidate's next turn: the reply was cut off or "
                    "dropped when they kept talking.")
        if nxt.kind == "tool":
            return tool_call(call_id, nxt.data["tool"], nxt.data["arguments"])
        answered.add(id(nxt))
        return said(nxt.data.get("text", ""))

    def observer(t: float, j: int) -> None:
        prompt = prompts.OBSERVER.format(question=s.question.text, signals="; ".join(s.question.expected_signals),
                                         answer="\n".join(s.answers), latest=s.answers[-1])
        nxt = next((x for x in evs[j + 1:] if x.kind in ("superseded", "note")), None)
        if nxt is None or nxt.kind == "superseded":
            resp = "Nothing: this Observer call was cancelled when the candidate's next turn started a newer one."
            extra = ""
        else:
            resp = {k: v for k, v in nxt.data.items() if k != "question"}
            u = next((x for x in usage if x.data.get("schema") == "ObserverNote" and x.t >= t), None)
            extra = (f"*Usage:* {u.data.get('prompt_tokens')} prompt tokens, {u.data.get('output_tokens')} out "
                     f"({u.data.get('reasoning_tokens')} reasoning), returned at {nxt.t:.1f} s.") if u else ""
        flow.call(t, f"Observer, question {s.idx + 1} (background, never spoken)", body_json(prompt, ObserverNote, session_id=sid),
                  resp, note="Runs alongside the reply below; its note shapes the interviewer's *next* reply.", stats=extra)

    for j, e in enumerate(evs):
        k = e.kind
        if k == "agent":
            cut = j + 1 < len(evs) and evs[j + 1].kind == "candidate" and evs[j + 1].t - e.t < 0.5
            target = consent_ctx if phase == "consent" else cctx if phase == "closing" else ictx
            target.add_message(role="assistant", content=e.data.get("text", ""), interrupted=cut)
            s.interviewer_spoke = True
            if id(e) not in answered:
                flow.line(e.t, "Agent", e.data.get("text", ""), "fixed line, `session.say` (no LLM)")
            elif cut:
                flow.add(e.t, f"*({e.t:.1f} s) The candidate kept talking, so the agent stopped after "
                              f"\"{e.data.get('text', '')}\".*\n")
        elif k == "time_up":
            time_up = True
            flow.add(e.t, f"*({e.t:.1f} s) Interview timer: 90 s left, so the next reply must move to closing.*\n")
        elif k == "left":
            flow.add(e.t, f"*({e.t:.1f} s) The candidate disconnected; the agent waits up to "
                          f"{config.RECONNECT_GRACE_SEC:.0f} s for a rejoin.*\n")
        elif k == "back":
            flow.add(e.t, f"*({e.t:.1f} s) The candidate rejoined.*\n")
        elif k == "closed":
            flow.add(e.t, f"*({e.t:.1f} s) Session closed; the room is deleted and the results are queued for the backend.*\n")
        elif k == "note":
            note = ObserverNote.model_validate({x: v for x, v in e.data.items() if x != "question"})
            if phase == "interview" and e.data.get("question") == s.idx + 1:
                note_json = note.model_dump_json(exclude={"coverage", "latest_turn"})
                clar += note.latest_turn == "clarification"
        elif k == "tool" and e.data.get("tool") == "advance_to_next_question" and not e.data.get("result"):
            ending = "time_up" if time_up else "completed"
            closing = ClosingAgent(s, ending)
            cctx = llm.ChatContext()
            cctx.add_message(role="system", content=closing.instructions)
            phase = "closing"
            flow.add(e.t, f"*({e.t:.1f} s) Last question done: `advance_to_next_question` hands off to "
                          f"**ClosingAgent** (ending \"{ending}\"), which starts with only its own system prompt.*\n")
        elif k == "tool" and e.data.get("tool") == "end_interview":
            flow.add(e.t, f"*({e.t:.1f} s) `end_interview` runs: it says the fixed goodbye and ends the session.*\n")
        elif k == "candidate":
            text = e.data.get("text", "")
            flow.line(e.t, "Candidate", text)
            nxt = next_reply(j)
            n_calls += 1
            cid = f"call_{n_calls}"
            if phase == "consent":
                consent_ctx.add_message(role="user", content=text)
                flow.call(e.t, "ConsentTask, voice LLM (OpenRouter)", body_voice(messages_of(consent_ctx), tools_of(consent)),
                          voice_response(nxt, cid), stats=stats(e.t, nxt.t if nxt else e.t + 5))
                if nxt and nxt.kind == "tool" and nxt.data["tool"] == "consent_given":
                    flow.add(nxt.t, f"*({nxt.t:.1f} s) `consent_given` runs in Python and completes the task "
                                    "(tool output `\"\"`, no reply). Its conversation moves into the interviewer's history.*\n")
                    phase, qstart = "interview", nxt.t
                    for it in consent_ctx.items:  # LiveKit merges the task's messages, not its tool calls
                        if it.type == "message" and it.role != "system":
                            ictx.items.append(it)
                continue
            if phase == "interview":
                block = block_at(e.t)  # llm_node builds it before this answer is recorded
                ictx.add_message(role="user", content=text)
                req = ictx.copy()
                req.add_message(role="system", content=block)
                flow.call(e.t, f"InterviewerAgent, voice LLM (OpenRouter), question {s.idx + 1}",
                          body_voice(messages_of(req), tools_of(interviewer)), voice_response(nxt, cid),
                          note="The last system message is the current-question block, added to a copy of the context "
                               "for this call only (InterviewerAgent.llm_node); it's never stored in the history.",
                          stats=stats(e.t - 15, nxt.t if nxt else e.t + 5))
                s.record_answer(text)
                observer(e.t, j)
                if nxt and nxt.kind == "tool" and nxt.data["tool"] == "advance_to_next_question" and nxt.data.get("result"):
                    # the tool truncates the history; then its call and output are inserted
                    ictx = ictx.copy().truncate(max_items=config.KEEP_CHAT_ITEMS)
                    s.idx += 1
                    s.answers, s.interviewer_spoke = [], True
                    note_json, clar, qstart = "", 0, nxt.t
                    output = f"Ask this next question now, in your own words: {s.question.text}"
                    ictx.items.append(llm.FunctionCall(call_id=cid, name="advance_to_next_question", arguments="{}"))
                    ictx.items.append(llm.FunctionCallOutput(call_id=cid, name="advance_to_next_question", output=output, is_error=False))
                    flow.add(nxt.t, f"*({nxt.t:.1f} s) `advance_to_next_question` runs in Python: question {s.idx + 1} "
                                    f"starts, the history is trimmed to the last {config.KEEP_CHAT_ITEMS} items, and "
                                    f"the tool output goes back to the LLM:* `{output}`\n")
                    req = ictx.copy()
                    req.add_message(role="system", content=block_at(nxt.t))
                    n2 = next_reply(evs.index(nxt))
                    n_calls += 1
                    flow.call(nxt.t, f"InterviewerAgent, voice LLM (OpenRouter), tool reply, question {s.idx + 1}",
                              body_voice(messages_of(req), tools_of(interviewer)), voice_response(n2, f"call_{n_calls}"),
                              stats=stats(nxt.t, n2.t if n2 else nxt.t + 5))
                elif nxt and nxt.kind == "tool" and nxt.data["tool"] == "advance_to_next_question":
                    ictx.items.append(llm.FunctionCall(call_id=cid, name="advance_to_next_question", arguments="{}"))
                continue
            if phase == "closing":
                cctx.add_message(role="user", content=text)
                flow.call(e.t, "ClosingAgent, voice LLM (OpenRouter)", body_voice(messages_of(cctx), tools_of(closing)),
                          voice_response(nxt, cid), stats=stats(e.t, nxt.t if nxt else e.t + 5))
                if nxt and nxt.kind == "tool" and nxt.data["tool"] == "lookup_company_info":
                    q = json.loads(nxt.data["arguments"])["question"]
                    lk = next((x for x in evs[j:] if x.kind == "lookup"), None)
                    u = next((x for x in usage if x.data.get("schema") == "ScopeLabel" and x.t >= e.t), None)
                    flow.call(nxt.t, "scope check inside `lookup_company_info` (runs alongside the knowledge-base search)",
                              body_json(prompts.SCOPE.format(allowed=", ".join(plan.plan.allowed_tech_stack), question=q),
                                        ScopeLabel, session_id=sid),
                              f"The label isn't logged. The lookup returned `{lk.data.get('status') if lk else '?'}`, so it "
                              "was neither `future_projects` nor `tech_outside_jd`.",
                              stats=(f"*Usage:* {u.data.get('prompt_tokens')} prompt tokens, {u.data.get('output_tokens')} out "
                                     f"({u.data.get('reasoning_tokens')} reasoning).") if u else "")
                    _, hits = await kb.search(plan.job, q)  # the log truncates the chunks; re-fetch them
                    result = {"status": lk.data.get("status") if lk else "ANSWER", "chunks": [h["text"] for h in hits],
                              "source_urls": sorted({h["source"] for h in hits})}
                    cctx.items.append(llm.FunctionCall(call_id=cid, name="lookup_company_info", arguments=nxt.data["arguments"]))
                    cctx.items.append(llm.FunctionCallOutput(call_id=cid, name="lookup_company_info",
                                                              output=json.dumps(result), is_error=False))
                    n2 = next_reply(evs.index(nxt))
                    n_calls += 1
                    flow.call(nxt.t, "ClosingAgent, voice LLM (OpenRouter), tool reply after `lookup_company_info`",
                              body_voice(messages_of(cctx), tools_of(closing)), voice_response(n2, f"call_{n_calls}"),
                              note="The tool output's chunks were re-fetched from the knowledge base for this document "
                                   "(the log truncates them); the knowledge base hasn't changed since.",
                              stats=stats(nxt.t, n2.t if n2 else nxt.t + 5))

    # -------- after the call: scoring (backend)
    turns = rec["transcript"]
    context = prompts.SCORER_CONTEXT.format(notes=json.dumps(rec.get("notes", [])),
                                            transcript="\n".join(f"{t['idx']} {t['speaker']}: {t['text']}" for t in turns))
    fell_back = args.scorer_log and "re-scoring all criteria on gemini" in args.scorer_log.read_text(encoding="utf-8", errors="replace")
    by_id = {c["criterion_id"]: c for c in (rec.get("score") or {}).get("criteria", [])}
    scoring = Flow()
    end = max((e.t for e in evs), default=0) + 1
    for n, c in enumerate(plan.job.criteria, 1):
        sprompt = prompts.SCORER.format(name=c.name, id=c.id, description=c.description,
                                        anchors="\n".join(f"{k}: {v}" for k, v in sorted(c.anchors.items())))
        if n == 1:
            scoring.call(end, f"Scorer, criterion \"{c.name}\" (OpenRouter, flex)",
                         body_json(sprompt, CriterionScore, model=config.SCORER_ROUTE.openrouter, context=context, session_id=sid, flex=True),
                         "No text came back (`openai/gpt-6-luna returned no text`), so every criterion was re-scored "
                         "on Gemini." if fell_back else by_id.get(c.id, {}),
                         note="The first criterion goes alone (it writes the shared-context cache), then the rest in parallel.")
        if fell_back:
            scoring.call(end + n, f"Scorer, criterion \"{c.name}\" (Gemini {config.SCORER_ROUTE.gemini}, one judge per interview)",
                         body_gemini(config.SCORER_ROUTE.gemini, sprompt, CriterionScore, context=context), by_id.get(c.id, {}))

    head = f"""# Guru interview flow: every string and JSON sent to and returned from the LLMs

Interview `{sid}`: {plan.job.title} at {plan.job.company_name}, {len(plan.plan.questions)} questions, from the
consent line to the goodbye, in the order things happened (times are seconds from when the agent got the job).
Rendered on {datetime.now():%d %b %Y} with `agent/evals/render_prompt_flow.py`.

- **Agent** lines marked *fixed line* are spoken with `session.say`: no LLM is involved.
- **LLM call** blocks show the exact body sent and what came back. Requests are rebuilt from the interview's
  log and record with the agent's own prompt code and LiveKit's OpenAI formatter, so prompts, tool schemas
  and history are word for word. Returned values are as logged: the spoken text or tool call (shown in the
  OpenAI message format) and the parsed JSON. The raw streamed response isn't logged, and tool call ids are
  shown as `call_N`.
- Preemptive drafts (the voice LLM starting before a turn is confirmed) aren't shown separately; the
  "Voice-LLM calls in this window" line under each call counts them.

## The interview

"""
    tail = """
## After the call: scoring (backend)

""" + scoring.render()
    args.out.write_text(head + flow.render() + tail, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}: {sum(1 for _, _, m in flow.steps if m.startswith('<details'))} interview LLM calls")


if __name__ == "__main__":
    asyncio.run(main())
