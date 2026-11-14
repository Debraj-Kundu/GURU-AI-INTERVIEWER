"""Offline checks for the deterministic logic. Run: uv run pytest -q"""

import json
from pathlib import Path

from guru import config
from guru.agents.interviewer import current_question_block, interviewer_instructions
from guru.states import InterviewState
from guru.kb import CHUNK_CHARS, chunk
from guru.models import CriterionScore, InterviewPlan, JobRole, ObserverNote, Plan, PlannedQuestion
from guru.planner import fit_to_time
from guru.scorer import aggregate

JOB = JobRole.model_validate_json(Path("fixtures/job.json").read_text(encoding="utf-8"))


def q(text, sec):
    return PlannedQuestion(text=text, expected_signals=["s"], follow_up_probes=["p"], time_budget_sec=sec, linked_criteria=[])


def test_fit_to_time_drops_what_does_not_fit():
    plan = InterviewPlan(questions=[q("a", 200), q("b", 300), q("c", 100)], allowed_tech_stack=[])
    plan = fit_to_time(plan, 400)
    assert [x.text for x in plan.questions] == ["a", "c"]
    assert [x.text for x in plan.skipped_questions] == ["b"]


def test_aggregate_is_weighted_and_skips_insufficient_evidence():
    s = [
        CriterionScore(criterion_id="system_design", score=4, insufficient_evidence=False, evidence_turns=[1], rationale=""),
        CriterionScore(criterion_id="debugging", score=2, insufficient_evidence=False, evidence_turns=[3], rationale=""),
        CriterionScore(criterion_id="data", score=None, insufficient_evidence=True, evidence_turns=[], rationale=""),
    ]
    assert aggregate(s, JOB.criteria) == round((4 * 3 + 2 * 2) / 5, 2)
    assert aggregate(s[2:], JOB.criteria) is None


def test_chunk_respects_size_and_keeps_context():
    paras = [f"para{i} " * 200 for i in range(5)]  # ~1200-1400 chars each, all distinct
    text = "# Title\n\n" + "\n\n".join(paras) + "\n\n## Benefits\n\nShort section."
    chunks = chunk(text, "doc")
    assert len(chunks) > 2
    assert all(len(c) <= CHUNK_CHARS + 100 for c in chunks)
    assert all(c.startswith("doc") for c in chunks)
    assert any(c.startswith("doc > Benefits") for c in chunks)
    assert len(set(chunks)) == len(chunks)  # overlap must not produce duplicate chunks


def test_long_paragraph_is_split():
    """PDF pages often arrive as one huge paragraph; it must still fit CHUNK_CHARS."""
    page = " ".join(f"Sentence number {i} about the company." for i in range(300))  # ~11k chars
    chunks = chunk(page + "\n" + "x" * 5000, "pdf")  # plus one unbreakable run
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    assert "Sentence number 299" in "".join(chunks)


def test_fixture_kb_chunks():
    for p in Path("fixtures/kb").glob("*.md"):
        assert chunk(p.read_text(encoding="utf-8"), p.stem)


def test_follow_up_limit_and_time_up_force_advance():
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=["Python"]))
    s = InterviewState(plan=plan, session_id="t")
    s.question_started_at = __import__("time").monotonic()
    assert "call advance_to_next_question now" not in current_question_block(s)
    s.answers = ["a"] * config.MAX_FOLLOW_UPS
    assert "call advance_to_next_question now" in current_question_block(s)
    s.answers, s.time_up = [], True
    assert "call advance_to_next_question now" in current_question_block(s)
    s.time_up = False
    s.answers, s.clarifications = ["a"] * config.MAX_FOLLOW_UPS, 1  # one turn only asked to repeat the question
    assert "call advance_to_next_question now" not in current_question_block(s)


def test_tools_wait_for_a_candidate_reply():
    """Session cd2fde9e: the model asked a question and called the next tool in the same response,
    so consent, both questions and the handoff went by with no candidate turn."""
    import asyncio

    from livekit.agents.llm import ChatContext, ToolError

    from guru import agents as iv

    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=[]))
    s = InterviewState(plan=plan, session_id="t")

    def bare(cls, ctx=None):
        agent = object.__new__(cls)  # no live session needed for the guards
        agent.s, agent._chat_ctx = s, ctx or ChatContext.empty()

        async def keep_history(_ctx):  # advancing trims history through the live session; not under test
            pass

        agent.update_chat_ctx = keep_history
        return agent

    def refused(call) -> bool:
        try:
            asyncio.run(call)
        except ToolError:
            return True
        return False

    interviewer, consent, closing = bare(iv.InterviewerAgent), bare(iv.ConsentTask), bare(iv.ClosingAgent)
    assert refused(interviewer.advance_to_next_question()) and s.idx == 0
    assert refused(interviewer.candidate_wants_to_end())
    assert refused(consent.consent_given()) and refused(consent.consent_declined())
    assert refused(closing.end_interview())
    assert s.guard_refusals == 5  # each refusal counts towards the outcome metrics

    s.answers = ["an answer"]
    assert not refused(interviewer.advance_to_next_question()) and s.idx == 1
    s.answers, s.time_up = [], True  # time up moves on even with no answer to this question
    assert not refused(interviewer.advance_to_next_question())


def test_a_pause_does_not_use_up_a_follow_up():
    """Session 65aa4287: a pause split one answer into two candidate turns, the interviewer replied
    once, and the question advanced after one follow-up instead of two."""
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120)], allowed_tech_stack=[]))
    s = InterviewState(plan=plan, session_id="t")
    s.question_started_at = __import__("time").monotonic()
    s.record_answer("Uh, yes. I was just thinking. I'd use retrieval")
    s.record_answer("and pass the context to the LLM.")  # same answer, after a pause: no interviewer reply between
    assert s.answers == ["Uh, yes. I was just thinking. I'd use retrieval and pass the context to the LLM."]
    # The interviewer asked follow-up 1; replying to its answer, it may still ask follow-up 2.
    # Counting turns (2) forced the advance here.
    assert "call advance_to_next_question now" not in current_question_block(s)
    s.interviewer_spoke = True
    s.record_answer("I'd also lower the temperature.")  # the answer to follow-up 1 is a new answer
    assert len(s.answers) == 2


def test_observer_counts_clarifications_on_the_same_question(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from guru.session import observer as interviewer

    labels = iter(["clarification", "answer"])

    async def fake_generate_json(*_, **__):
        return ObserverNote(coverage=0, depth="shallow", signals_hit=[], signals_missing=[], red_flags=[],
                            suggested_followup=None, latest_turn=next(labels))

    monkeypatch.setattr(interviewer, "generate_json", fake_generate_json)
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=[]))
    s = InterviewState(plan=plan, session_id="t")
    agent = SimpleNamespace(s=s)
    session = SimpleNamespace(current_agent=agent)
    asyncio.run(interviewer.observe(session, agent, "what was the other part?", "what was the other part?"))
    asyncio.run(interviewer.observe(session, agent, "an answer", "an answer"))
    assert s.clarifications == 1
    assert "latest_turn" not in s.note  # the label is for counting, not shown to the interviewer


def test_silence_checks_in_then_ends():
    import asyncio

    from guru.phrases import SILENCE_GOODBYE, STILL_THERE
    from guru.session.lifecycle import on_silence

    class Done:
        def __await__(self):
            return iter(())

    class FakeSession:
        def __init__(self):
            self.said, self.resets, self.closed = [], 0, False

        def say(self, text, **_):
            self.said.append(text)
            return Done()

        def reset_away_timer(self):
            self.resets += 1

        def shutdown(self):
            self.closed = True

    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120)], allowed_tech_stack=[]))
    s, session = InterviewState(plan=plan, session_id="t"), FakeSession()
    asyncio.run(on_silence(session, s))  # type: ignore[arg-type]
    assert session.said == [] and s.silence_prompts == 0  # the agent still owes its first reply: not silence
    assert session.resets == 1  # "away" cleared so LiveKit re-arms after the agent's turn
    session.resets = 0
    s.awaiting_reply = False  # the agent's turn ended; noise without a candidate turn doesn't change this
    for _ in range(config.SILENCE_PROMPTS):
        asyncio.run(on_silence(session, s))  # type: ignore[arg-type]
    assert session.said == [STILL_THERE] * config.SILENCE_PROMPTS and not session.closed
    assert session.resets == config.SILENCE_PROMPTS  # each check-in re-arms the timer
    asyncio.run(on_silence(session, s))  # type: ignore[arg-type]
    assert session.said[-1] == SILENCE_GOODBYE and session.closed


def test_system_prompt_is_fixed_for_the_interview():
    """Prompt caching needs an identical prefix across turns (§3.4): question, follow-ups, notes
    and time-up live in current_question_block, never in the system prompt."""
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=["Python"]))
    s = InterviewState(plan=plan, session_id="t")
    a = interviewer_instructions(s)
    s.idx, s.note, s.answers, s.time_up = 1, json.dumps({"depth": "deep"}), ["a"], True
    assert interviewer_instructions(s) == a
    assert "Current question (2 of 2)" in current_question_block(s)


def test_split_answer_is_observed_once(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from guru.session import observer as interviewer

    graded, cancelled = [], []

    async def fake_observe(session, agent, answer, latest):
        try:
            await asyncio.sleep(0.05)
            graded.append(answer)
        except asyncio.CancelledError:
            cancelled.append(answer)
            raise

    monkeypatch.setattr(interviewer, "observe", fake_observe)
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=[]))
    s = InterviewState(plan=plan, session_id="t")
    agent = SimpleNamespace(s=s)

    async def run():
        s.answers = ["part one"]
        interviewer.observe_latest(None, agent)  # type: ignore[arg-type]
        await asyncio.sleep(0)  # the first call is in flight
        s.answers.append("part two")  # a pause split the answer into a second turn
        interviewer.observe_latest(None, agent)  # type: ignore[arg-type]
        await asyncio.sleep(0)
        s.idx, s.answers = 1, ["next answer"]  # a new question must not cancel the last one's call
        interviewer.observe_latest(None, agent)  # type: ignore[arg-type]
        await asyncio.gather(*s.observing.values(), return_exceptions=True)

    asyncio.run(run())
    assert cancelled == ["part one"]
    assert graded == ["part one\npart two", "next answer"]


def test_scorer_uses_one_model_per_session(monkeypatch):
    """One OpenRouter failure re-scores every criterion on Gemini, never a mix of judges."""
    import asyncio

    from guru import llm, scorer

    calls: list[tuple[str, str]] = []

    async def fake_generate_json(model, prompt, schema, provider="auto", **_):
        cid = prompt.split("(id: ")[1].split(")")[0] if "(id: " in prompt else prompt[:20]
        calls.append((provider, cid))
        if provider == "openrouter" and len(calls) == 2:  # the second criterion fails
            raise RuntimeError("402 out of credits")
        return CriterionScore(criterion_id="", score=3, insufficient_evidence=False, evidence_turns=[1], rationale=provider)

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(llm, "generate_json", fake_generate_json)
    plan = Plan.model_validate_json(config.PLAN_PATH.read_text(encoding="utf-8"))
    out = asyncio.run(scorer.score_session({"session_id": "t", "turns": [], "notes": []}, plan))

    n = len(plan.job.criteria)
    assert n >= 2 and out["model"] == config.SCORER_MODEL
    assert {c["rationale"] for c in out["criteria"]} == {"gemini"}  # every final score from one judge
    assert [p for p, _ in calls[-n:]] == ["gemini"] * n


def test_fixed_lines_cover_every_case():
    from guru.agents.closing import ENDINGS
    from guru.phrases import CLOSING_OPENERS

    assert set(CLOSING_OPENERS) == set(ENDINGS)  # every handoff to closing has an opening line


def test_last_spoken_skips_a_line_that_was_cut_off():
    from livekit.agents import Agent

    from guru.agents.guards import last_spoken

    agent = Agent(instructions="x")
    ctx = agent.chat_ctx.copy()
    ctx.add_message(role="assistant", content="How would you design it?")
    ctx.add_message(role="user", content="hmm")
    agent._chat_ctx = ctx
    assert last_spoken(agent) == "How would you design it?"

    ctx.add_message(role="assistant", content="That's a great question, but I don't have that inf", interrupted=True)
    assert last_spoken(agent) is None  # half a sentence is never repeated


def test_lookup_without_an_answer_speaks_the_fixed_line_and_skips_the_reply(monkeypatch):
    import asyncio

    from livekit.agents import Agent, ToolResult

    from guru import kb
    from guru.agents import company_lookup
    from guru.phrases import NO_INFO, NO_INFO_CLOSING

    s = InterviewState(plan=Plan.model_validate_json(config.PLAN_PATH.read_text(encoding="utf-8")), session_id="t")
    said: list[str] = []
    agent = Agent(instructions="x")
    ctx = agent.chat_ctx.copy()
    ctx.add_message(role="assistant", content="What would you monitor after release?")
    agent._chat_ctx = ctx

    class Session:
        current_agent = agent

        def say(self, text, **_):
            said.append(text)

    class Context:
        session = Session()

        def with_filler(self, *_a, **_k):
            import contextlib
            return contextlib.nullcontext()

    async def no_answer(*_a, **_k):
        return {"status": "NO_ANSWER", "chunks": [], "source_urls": []}

    monkeypatch.setattr(kb, "lookup", no_answer)
    out = asyncio.run(company_lookup.lookup_tool.__wrapped__(s, "on-call?", Context()))
    assert isinstance(out, ToolResult) and out.reply_required is False
    assert said == [NO_INFO + " Coming back to my question: What would you monitor after release?"]

    s.ending = "completed"  # in closing: invite the next question instead
    asyncio.run(company_lookup.lookup_tool.__wrapped__(s, "on-call?", Context()))
    assert said[-1] == NO_INFO_CLOSING

    async def answer(*_a, **_k):
        return {"status": "ANSWER", "chunks": ["Weekly rotation."], "source_urls": ["u"]}

    monkeypatch.setattr(kb, "lookup", answer)
    assert isinstance(asyncio.run(company_lookup.lookup_tool.__wrapped__(s, "on-call?", Context())), str)  # model answers
    assert len(said) == 2  # nothing spoken by the tool


def test_derived_criteria_get_unique_ids_and_scored_anchors():
    import pytest

    from guru.models import DerivedCriterion
    from guru.planner import to_criteria

    five = ["weak", "basic", "solid", "strong", "excellent"]
    out = to_criteria([
        DerivedCriterion(name="System Design", description="d", weight=3, source="jd", anchors=five),
        DerivedCriterion(name="System design!", description="d", weight=2, source="jd", anchors=five),
        DerivedCriterion(name="Broken", description="d", weight=1, source="inferred", anchors=five[:3]),
    ])
    assert [c.id for c in out] == ["system_design", "system_design_2"]  # the 3-anchor one is dropped
    assert out[0].anchors == {"1": "weak", "2": "basic", "3": "solid", "4": "strong", "5": "excellent"}
    assert out[0].source == "jd"
    with pytest.raises(ValueError):
        to_criteria([])


def test_planner_fills_in_only_what_is_missing(monkeypatch):
    import asyncio

    from guru import llm, planner, prompts
    from guru.models import DerivedCriteria, DerivedCriterion

    prompts_seen: list[str] = []

    async def fake_generate_json(model, prompt, schema, **_):
        prompts_seen.append(prompt)
        if schema is DerivedCriteria:
            five = ["1", "2", "3", "4", "5"]
            return DerivedCriteria(criteria=[DerivedCriterion(name="API design", description="d", weight=3, source="jd", anchors=five)])
        return InterviewPlan(questions=[PlannedQuestion(text="q", expected_signals=["s"], follow_up_probes=["p"],
                                                        time_budget_sec=60, linked_criteria=["api_design", "made_up"])],
                             allowed_tech_stack=[])

    monkeypatch.setattr(llm, "generate_json", fake_generate_json)

    # Nothing given: criteria are derived first, and the planner is told to write the questions.
    bare = JOB.model_copy(update={"questions": [], "criteria": []})
    full = asyncio.run(planner.make_plan(bare, ""))
    assert [c.id for c in full.job.criteria] == ["api_design"] and full.job.criteria[0].source == "jd"
    assert prompts.PLANNER_NO_BANK_RULE in prompts_seen[-1]
    assert full.plan.questions[0].linked_criteria == ["api_design"]  # the made-up link is dropped
    assert bare.criteria == []  # the role itself isn't changed

    # Both given: no derivation call, and the bank rule is used.
    prompts_seen.clear()
    full = asyncio.run(planner.make_plan(JOB, ""))
    assert len(prompts_seen) == 1 and prompts.PLANNER_NO_BANK_RULE not in prompts_seen[0]
    assert full.job.criteria == JOB.criteria
