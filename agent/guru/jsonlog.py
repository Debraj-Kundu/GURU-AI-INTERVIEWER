"""JSON log lines for agent lifecycle hooks, tool calls, and constructors.

Every line has the same keys: message, class_name, function_name, then args (start lines) or
output (in-function lines), then err_message and err_stack (null unless something failed).
"""

import functools
import inspect
import json
import logging
import logging.handlers
import traceback
from pathlib import Path

log = logging.getLogger("guru")

MAX_VALUE_CHARS = 300  # long values (plans, transcripts) are cut so one line stays readable

# Attributes every LogRecord has; anything else was passed via extra= (e.g. LiveKit's LLM metrics).
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class _TextFormatter(logging.Formatter):
    """Plain text like the console, plus any extra= fields as JSON so metrics aren't lost."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extra = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and not k.startswith("_")}
        return f"{line} {json.dumps(extra, default=_short, ensure_ascii=False)}" if extra else line


def add_file_sink(path: Path, max_mb: int = 10, backups: int = 5) -> None:
    """Second sink next to the console: every log record (ours and LiveKit's) also goes to `path`.

    Call once, in the main process only. LiveKit forwards job-process records to the main
    process's root logger, so adding it in job processes too would duplicate every line.
    Rotates at `max_mb`, keeping `backups` old files (guru.txt.1, guru.txt.2, ...).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(_TextFormatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "..."


def jlog(
    message: str,
    *,
    class_name: str | None = None,
    function_name: str | None = None,
    output: object = None,
    err: BaseException | None = None,
    level: int = logging.INFO,
) -> None:
    """One in-function log line, e.g. jlog("Calling company lookup", class_name=..., output=...)."""
    record = {
        "message": message,
        "class_name": class_name,
        "function_name": function_name,
        "output": output,
        "err_message": str(err) if err else None,
        "err_stack": "".join(traceback.format_exception(err)) if err else None,
    }
    log.log(level, json.dumps(record, default=_short, ensure_ascii=False))


def logged(fn):
    """Decorator: logs "Start <function>" with its arguments, and the error + stack if it raises.

    Uses functools.wraps, so LiveKit's @function_tool still sees the real signature and docstring.
    Put it UNDER @function_tool.
    """
    sig = inspect.signature(fn)
    parts = fn.__qualname__.split(".")
    class_name = parts[-2] if len(parts) > 1 and parts[-2] != "<locals>" else None
    name = fn.__name__

    def start(args: tuple, kwargs: dict) -> None:
        bound = sig.bind_partial(*args, **kwargs).arguments
        record = {
            "message": f"Start {name}",
            "class_name": class_name,
            "function_name": name,
            "args": {k: v for k, v in bound.items() if k != "self"},
            "err_message": None,
            "err_stack": None,
        }
        log.info(json.dumps(record, default=_short, ensure_ascii=False))

    def failed(e: Exception) -> None:
        jlog(f"Error in {name}", class_name=class_name, function_name=name, err=e, level=logging.ERROR)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            start(args, kwargs)
            try:
                return await fn(*args, **kwargs)
            except Exception as e:  # CancelledError is not an Exception: cancellation isn't logged as an error
                failed(e)
                raise

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start(args, kwargs)
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            failed(e)
            raise

    return wrapper
