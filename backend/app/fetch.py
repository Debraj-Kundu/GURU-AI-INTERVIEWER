"""Fetch a web page for the KB with an SSRF guard (PLAN §4.3): public IPs only, every redirect
re-checked, robots.txt respected, size and time capped."""

import asyncio
import ipaddress
import urllib.robotparser
from urllib.parse import urljoin, urlsplit

import httpx

from .parsers import ParseError

USER_AGENT = "GuruBot/0.1 (interview knowledge-base ingest)"
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = httpx.Timeout(10.0)


async def _check(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ParseError("Only http(s) links are supported.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, port)
    except OSError:
        raise ParseError(f"Couldn't resolve {parts.hostname}.") from None
    for *_, sockaddr in infos:
        # is_global excludes private, loopback, link-local (cloud metadata 169.254.x), and reserved.
        if not ipaddress.ip_address(sockaddr[0]).is_global:
            raise ParseError("That address isn't on the public internet.")
    # ponytail: the IP is checked at resolve time; DNS rebinding between check and connect is
    # still possible. Pin the resolved IP in the transport if links come from untrusted users.


async def _get(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    for _ in range(MAX_REDIRECTS + 1):
        await _check(url)
        async with client.stream("GET", url) as r:
            if r.is_redirect and "location" in r.headers:
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code != 200:
                raise ParseError(f"The page returned HTTP {r.status_code}.")
            body = bytearray()
            async for part in r.aiter_bytes():
                body += part
                if len(body) > MAX_BYTES:
                    raise ParseError("The page is larger than 5 MB.")
            return bytes(body), url
    raise ParseError("Too many redirects.")


async def fetch_page(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        parts = urlsplit(url)
        robots = urllib.robotparser.RobotFileParser()
        try:
            text, _ = await _get(client, f"{parts.scheme}://{parts.netloc}/robots.txt")
            robots.parse(text.decode("utf-8", errors="replace").splitlines())
        except ParseError:
            robots.parse([])  # no robots.txt: allowed
        if not robots.can_fetch(USER_AGENT, url):
            raise ParseError("The site's robots.txt doesn't allow fetching this page.")
        body, _ = await _get(client, url)
        return body
