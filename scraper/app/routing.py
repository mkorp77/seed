"""Domain kind detection with persistent cache.

A 30-day TTL means we re-probe forums after a month in case they migrate
off Discourse. Lazy refresh: stale entries are still used for the current
request, refresh happens in the background on the next probe call.
"""
from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

from .util import log_event


CACHE_PATH = Path(os.environ.get("DETECTION_CACHE_PATH", "/app/data/detection_cache.json"))
TTL_DAYS = 30
PROBE_TIMEOUT_S = 5.0

DomainKind = Literal["discourse", "github", "generic"]


_lock = asyncio.Lock()
_cache: dict[str, dict] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    if CACHE_PATH.exists():
        try:
            _cache.update(json.loads(CACHE_PATH.read_text()))
        except (json.JSONDecodeError, OSError) as e:
            log_event(level="warn", msg="detection_cache_load_failed", error=str(e))
            _cache.clear()
    _loaded = True


def _persist() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, indent=2))
    except OSError as e:
        log_event(level="warn", msg="detection_cache_persist_failed", error=str(e))


def _domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def _is_fresh(entry: dict) -> bool:
    checked = datetime.fromisoformat(entry["checked_at"])
    return datetime.now(timezone.utc) - checked < timedelta(days=TTL_DAYS)


async def _probe(domain: str) -> DomainKind:
    """Probe /about.json to detect Discourse. Cheap, ~50ms when it exists."""
    if domain == "github.com":
        return "github"

    url = f"https://{domain}/about.json"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "about" in data:
                return "discourse"
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        pass
    return "generic"


async def detect(url: str) -> DomainKind:
    """Return the routing kind for a URL's domain.

    Cache hits return immediately. Cache misses synchronously probe.
    Stale entries return cached value and refresh in background.
    """
    _load()
    domain = _domain_of(url)

    async with _lock:
        entry = _cache.get(domain)
        if entry and _is_fresh(entry):
            return entry["kind"]
        if entry and not _is_fresh(entry):
            # Stale: kick off background refresh, return current value
            asyncio.create_task(_refresh(domain))
            return entry["kind"]

    # Cache miss: probe synchronously
    kind = await _probe(domain)
    async with _lock:
        _cache[domain] = {"kind": kind, "checked_at": datetime.now(timezone.utc).isoformat()}
        _persist()
    log_event(msg="detection_probe", domain=domain, kind=kind)
    return kind


async def _refresh(domain: str) -> None:
    """Background refresh of a stale cache entry."""
    kind = await _probe(domain)
    async with _lock:
        _cache[domain] = {"kind": kind, "checked_at": datetime.now(timezone.utc).isoformat()}
        _persist()
    log_event(msg="detection_refresh", domain=domain, kind=kind)
