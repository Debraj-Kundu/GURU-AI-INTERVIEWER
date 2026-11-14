"""The LiveKit worker: one entrypoint run per interview. It builds the parts (state, pipeline,
agents, event handlers) and starts the session; the behaviour lives in agents/ and session/."""

import time

from livekit.agents import (
    AgentServer,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    JobProcess,
    room_io,
)
from livekit.plugins import noise_cancellation, silero

from . import kb
from .agents import InterviewerAgent
from .backend_client import load_plan
from .jsonlog import jlog, logged
from .llm import openrouter
from .results import finish_interview
from .session.events import SessionEvents
from .session.pipeline import build_llm, build_session
from .states import InterviewState


@logged
def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["llm"] = build_llm()  
    kb.bm25()  
    openrouter()  # building its SSL context blocked the loop ~200 ms


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name="interviewer")
@logged
async def entrypoint(ctx: JobContext) -> None:
    joined_at = time.monotonic()  # start of the first-turn clock: job received to first agent audio
    # The backend's token dispatches us with the session id as metadata; we fetch that session's plan.
    plan = await load_plan(ctx.job.metadata, ctx.room.name)
    if plan is None:
        # One attempt per interview: it already started (or ended) in another room. Report nothing,
        # so the real attempt's result stands; deleting the room tells the page it's over.
        jlog("Interview already started or ended: leaving", function_name="entrypoint", output={"room": ctx.room.name})
        await ctx.delete_room()
        ctx.shutdown("interview already started")
        return
    s = InterviewState(plan=plan, session_id=ctx.job.metadata or ctx.room.name, joined_at=joined_at)
    keyterms = list(dict.fromkeys(plan.plan.allowed_tech_stack + plan.plan.stt_keyterms))  # dedup, keep order
    session = build_session(ctx.proc, keyterms=keyterms[:50])
    agent = InterviewerAgent(s)
    SessionEvents(ctx, session, s, agent).attach()
    ctx.add_shutdown_callback(lambda: finish_interview(session, s))

    await session.start(
        agent,
        room=ctx.room,
        # We end the session ourselves when the candidate doesn't come back (session/events.py); the
        # room is deleted on close to free minutes and to tell the page the interview is over.
        room_options=room_io.RoomOptions(
            close_on_disconnect=False,
            delete_room_on_close=True,
            # Krisp background noise suppression (LiveKit Cloud, no extra cost). Noise once counted as
            # candidate speech with no words (session 7c2b5e92). Not voice isolation (BVC): that is billed
            # separately and can drop a quiet candidate. Don't also enable Krisp in the web client.
            audio_input=room_io.AudioInputOptions(noise_cancellation=noise_cancellation.NC()),
        ),
        record=False,  # no LiveKit Cloud recording before consent; Phase 2 starts Egress after consent
    )
    # Quiet typing while the agent thinks. It covers the dead air on company questions (the model
    # deciding to look up, the lookup, then the answer: 7-11 s in testing), and briefly fills
    # ordinary 1-2 s waits too. Not played in console mode (the SDK skips it there).
    background = BackgroundAudioPlayer(
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.28),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.25),
        ]
    )
    await background.start(room=ctx.room, agent_session=session)
    ctx.add_shutdown_callback(background.aclose)
    jlog(
        "Session started",
        function_name="entrypoint",
        output={"session_id": s.session_id, "job_id": plan.job.id, "questions": len(plan.plan.questions)},
    )
