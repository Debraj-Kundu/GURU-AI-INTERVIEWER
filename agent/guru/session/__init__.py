"""Session-wide behaviour around the agents: the voice pipeline (pipeline.py), the background
Observer (observer.py), the clock and silence rules (lifecycle.py), and the LiveKit event handlers
that tie them together (events.py).

Kept import-free on purpose: agents/ imports session.lifecycle, and events.py imports agents/.
"""
