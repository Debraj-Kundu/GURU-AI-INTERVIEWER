"""Offline checks for the deterministic logic. Run: uv run pytest -q"""

import json
from pathlib import Path

from guru import config
from guru.interviewer import State, interviewer_instructions
from guru.kb import CHUNK_CHARS, chunk
from guru.models import CriterionScore, InterviewPlan, Job, Plan, PlannedQuestion
from guru.planner import fit_to_time
from guru.scorer import aggregate

JOB = Job.model_validate_json(Path("fixtures/job.json").read_text(encoding="utf-8"))


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
    s = State(plan=plan, session_id="t")
    s.question_started_at = __import__("time").monotonic()
    assert "call advance_to_next_question now" not in interviewer_instructions(s)
    s.answers = ["a"] * config.MAX_FOLLOW_UPS
    assert "call advance_to_next_question now" in interviewer_instructions(s)
    s.answers, s.time_up = [], True
    assert "call advance_to_next_question now" in interviewer_instructions(s)


def test_silence_checks_in_then_ends():
    import asyncio

    from guru.interviewer import SILENCE_GOODBYE, STILL_THERE, on_silence

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
    s, session = State(plan=plan, session_id="t"), FakeSession()
    for _ in range(config.SILENCE_PROMPTS):
        asyncio.run(on_silence(session, s))  # type: ignore[arg-type]
    assert session.said == [STILL_THERE] * config.SILENCE_PROMPTS and not session.closed
    assert session.resets == config.SILENCE_PROMPTS  # each check-in re-arms the timer
    asyncio.run(on_silence(session, s))  # type: ignore[arg-type]
    assert session.said[-1] == SILENCE_GOODBYE and session.closed


def test_static_prompt_prefix_is_stable():
    """Gemini implicit caching needs an identical prefix across turns (§3.4)."""
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=["Python"]))
    s = State(plan=plan, session_id="t")
    a = interviewer_instructions(s)
    s.idx, s.note = 1, json.dumps({"depth": "deep"})
    b = interviewer_instructions(s)
    prefix = a.split("Current question")[0]
    assert b.startswith(prefix)


def test_split_answer_is_observed_once(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from guru import interviewer

    graded, cancelled = [], []

    async def fake_observe(session, agent, answer):
        try:
            await asyncio.sleep(0.05)
            graded.append(answer)
        except asyncio.CancelledError:
            cancelled.append(answer)
            raise

    monkeypatch.setattr(interviewer, "observe", fake_observe)
    plan = Plan(job=JOB, plan=InterviewPlan(questions=[q("Q1", 120), q("Q2", 120)], allowed_tech_stack=[]))
    s = State(plan=plan, session_id="t")
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
