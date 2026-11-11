"""The company Q&A tool body, shared by the interviewer and the closing agent."""

import json
import logging

from livekit.agents import RunContext, ToolResult

from .. import config, kb
from ..jsonlog import jlog, logged
from ..phrases import BACK_TO_QUESTION, LOOKUP_FILLER, NO_INFO_CLOSING
from ..states import InterviewState
from .guards import last_spoken

NO_ANSWER = {"status": "NO_ANSWER", "chunks": [], "source_urls": []}


@logged
async def lookup_tool(s: InterviewState, question: str, context: RunContext) -> str | ToolResult:
    jlog("Calling company lookup (scope check + Qdrant search)", function_name="lookup_tool")
    try:
        # Speaks only if the lookup is slow (7-11 s seen in testing, 1.5-2 s lately). Not ctx.update():
        # that makes the tool non-blocking at the cost of an extra LLM reply, and the candidate is
        # waiting for this answer anyway, so there's nothing else to talk about meanwhile.
        async with context.with_filler(LOOKUP_FILLER, delay=config.LOOKUP_FILLER_SEC):
            result = await kb.lookup(s.plan.job, s.plan.plan.allowed_tech_stack, question, session_id=s.session_id)
    except Exception as e:
        # Fail closed: if the scope check or search breaks, refuse rather than guess.
        jlog("Company lookup failed, answering NO_ANSWER", function_name="lookup_tool", err=e, level=logging.ERROR)
        result = NO_ANSWER
    jlog(
        "Company lookup result",
        function_name="lookup_tool",
        output={"status": result["status"], "source_urls": result["source_urls"], "chunks": len(result["chunks"])},
    )
    if result["status"] == "ANSWER":
        return json.dumps(result)  # the model answers from the chunks
    # Nothing to answer from: the reply is a fixed line, so say it here and skip the model's reply
    # (a second LLM call, 1.5-2.5 s). reply_required=False keeps the result in history; returning
    # None would still ask the model for a reply.
    agent = context.session.current_agent
    if s.ending:  # ClosingAgent: invite the next question
        context.session.say(NO_INFO_CLOSING)
    else:  # mid-interview: go back to what the candidate was answering
        context.session.say(BACK_TO_QUESTION.format(last=last_spoken(agent) or s.question.text))
    return ToolResult(json.dumps(result), reply_required=False)
