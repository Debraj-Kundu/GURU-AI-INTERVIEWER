"""Voice interviewer launcher (PLAN §3): Consent -> Interviewer -> Closing, with a silent Observer.

Run:  uv run python -m guru.interviewer dev      (connects to LiveKit Cloud as agent "interviewer")
      uv run python -m guru.interviewer console  (talk to it with your mic, no frontend)

The worker is in worker.py, the agents in agents/, the interview state in states/, and the
session-wide behaviour (pipeline, Observer, timers, event handlers) in session/.
"""

from livekit.agents import cli

from . import config
from .jsonlog import add_file_sink
from .worker import server

if __name__ == "__main__":
    # Sinks: LiveKit's CLI sets up the console; we add the file. Main process only (see add_file_sink).
    add_file_sink(config.LOG_FILE)
    cli.run_app(server)
