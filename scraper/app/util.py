"""Shared utilities: structured JSON logging, hashing, timestamps."""
from __future__ import annotations
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any


_logger = logging.getLogger("seed_scraper")


def log_event(**fields: Any) -> None:
    """Emit a single JSON line to stdout. Docker captures it."""
    fields.setdefault("ts", datetime.now(timezone.utc).isoformat())
    sys.stdout.write(json.dumps(fields, default=str) + "\n")
    sys.stdout.flush()


def sha256_hex(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Timer:
    """Context manager for measuring elapsed milliseconds."""
    def __enter__(self) -> "Timer":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.elapsed_ms = int((time.perf_counter() - self.t0) * 1000)
