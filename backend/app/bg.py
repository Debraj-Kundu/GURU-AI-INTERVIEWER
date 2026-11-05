"""Background tasks for planning and scoring (KB ingest runs in the Celery workers, app/worker.py).

ponytail: in-process asyncio tasks instead of RabbitMQ workers (PLAN §4.3). A restart loses
in-flight work, so startup re-queues anything left mid-way. Move to the queue for multi-process
API workers or when a lost job matters.
"""

import asyncio

_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """create_task that keeps a reference, so the task isn't garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task
