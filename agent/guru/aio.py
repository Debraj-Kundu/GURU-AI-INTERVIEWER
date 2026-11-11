import asyncio

_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """create_task that keeps a reference, so the task isn't garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
