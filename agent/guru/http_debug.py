"""Response-header logging for outbound HTTP, for debugging (httpx event hooks).

One line per response: service, method, path (no query string: it can carry keys), status, time, and
an allowlist of headers: request ids for support tickets, rate limits, timing. Never cookies or
anything named like a token, key, secret or auth. Errors (4xx/5xx) are always logged, at WARNING;
successes only when LOG_HTTP=debug, since an interview makes 60-80 calls.
"""

import logging
import re
import time

import httpx

from . import config
from .jsonlog import jlog

_ALLOW = re.compile(
    r"request-?id|ratelimit|retry-after|server-timing|processing-ms|upstream-service-time"
    r"|^cf-ray$|traceparent|trace-context|^content-type$|^content-length$",
    re.I,
)
_DENY = re.compile(r"token|key|secret|auth|cookie", re.I)


def picked_headers(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items() if _ALLOW.search(k) and not _DENY.search(k)}


def log_http(service: str, method: str, path: str, status: int, ms: int | None, headers: dict) -> None:
    failed = status >= 400
    if not failed and config.LOG_HTTP != "debug":
        return
    jlog(
        "HTTP response",
        function_name="http",
        output={"service": service, "method": method, "path": path, "status": status, "ms": ms, "headers": headers},
        level=logging.WARNING if failed else logging.INFO,
    )


def _started(request: httpx.Request) -> None:
    request.extensions["guru_t0"] = time.monotonic()


def _finished(response: httpx.Response, service: str) -> None:
    req = response.request
    t0 = req.extensions.get("guru_t0")
    ms = round((time.monotonic() - t0) * 1000) if t0 else None  # to the headers; a stream may run on
    log_http(service, req.method, req.url.path, response.status_code, ms, picked_headers(response.headers))


def async_hooks(service: str) -> dict:
    """For httpx.AsyncClient(event_hooks=...)."""

    async def started(request: httpx.Request) -> None:
        _started(request)

    async def finished(response: httpx.Response) -> None:
        _finished(response, service)

    return {"request": [started], "response": [finished]}


def sync_hooks(service: str) -> dict:
    """For httpx.Client(event_hooks=...)."""
    return {"request": [_started], "response": [lambda response: _finished(response, service)]}
