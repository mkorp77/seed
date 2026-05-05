"""FastAPI entry point: POST /scrape.

Orchestrates:
  - URL -> domain detection -> path choice (or force_path override)
  - Discourse fast lane with automatic Playwright fallback on failure
  - Optional POST to Seed API
  - Per-request JSON line log to stdout
"""
from __future__ import annotations
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import discourse, generic, routing
from .models import (
    ContextRecord,
    ScrapeAttempt,
    ScrapeFailure,
    ScrapeRequest,
    ScrapeSuccess,
)
from .seed_client import SeedPostError, post_context
from .util import Timer, log_event, now_iso, sha256_hex


app = FastAPI(title="seed-scraper", version="0.1.0")


@app.get("/healthz")
async def healthz():
    return JSONResponse(content={"ok": True}, media_type="application/json; charset=utf-8")


@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    url = str(req.url)
    attempts: list[ScrapeAttempt] = []

    # Decide initial path
    if req.force_path:
        path = req.force_path
    else:
        kind = await routing.detect(url)
        path = "discourse_api" if kind == "discourse" else "playwright"

    # Attempt 1
    result, path_taken, fallback = await _attempt(path, url, attempts)
    if result is None and path == "discourse_api" and not req.force_path:
        # Auto-fallback to Playwright
        log_event(msg="fallback_to_playwright", url=url, reason=attempts[-1].error)
        result, path_taken, _ = await _attempt("playwright", url, attempts)
        fallback = True

    if result is None:
        return JSONResponse(
            status_code=502,
            content=ScrapeFailure(error="both_paths_failed", attempts=attempts).model_dump(),
            media_type="application/json; charset=utf-8",
        )

    # Build context record
    selected_text = result["markdown"]
    record = ContextRecord(
        project_id=req.project_id,
        source_kind="web",
        source_uri=url,
        source_title=result.get("title", "") or "",
        source_span_start=0,
        source_span_end=len(selected_text),
        selected_text=selected_text,
        content_hash=sha256_hex(selected_text),
        captured_at=now_iso(),
        source_external={
            **(result.get("source_external") or {}),
            "platform": "scraper",
            "capture_mode": "full_thread" if path_taken == "discourse_api" else "page_render",
            "path_taken": path_taken,
            "fallback": fallback,
            "discourse_post_count": result.get("post_count"),
            "scrape_duration_ms": result.get("duration_ms"),
        },
    )
    # Drop None values from source_external for cleanliness
    record.source_external = {k: v for k, v in record.source_external.items() if v is not None}

    # Optional persistence
    context_id: Optional[str] = None
    if req.post_to_seed:
        try:
            context_id = await post_context(record.model_dump())
        except SeedPostError as e:
            record.source_external["seed_post_error"] = {"status": e.status, "detail": e.detail}
            log_event(msg="seed_post_failed", url=url, status=e.status, error=e.detail)

    log_event(
        msg="scrape_complete",
        url=url,
        path_taken=path_taken,
        fallback=fallback,
        content_length=len(selected_text),
        context_id=context_id,
        duration_ms=result.get("duration_ms"),
    )

    return JSONResponse(
        content=ScrapeSuccess(
            path_taken=path_taken,
            fallback=fallback,
            context=record,
            context_id=context_id,
        ).model_dump(),
        media_type="application/json; charset=utf-8",
    )


async def _attempt(path: str, url: str, attempts: list[ScrapeAttempt]) -> tuple[Optional[dict], str, bool]:
    """Run a single scrape attempt. Mutates `attempts` with the result."""
    timer = Timer()
    timer.__enter__()
    try:
        if path == "discourse_api":
            data = await discourse.scrape(url)
        else:
            data = await generic.scrape(url)
        timer.__exit__(None, None, None)
        data["duration_ms"] = timer.elapsed_ms
        return data, path, False
    except Exception as e:
        timer.__exit__(None, None, None)
        status = getattr(getattr(e, "response", None), "status_code", None)
        attempts.append(ScrapeAttempt(path=path, status=status, error=f"{type(e).__name__}: {e}"))
        log_event(
            msg="attempt_failed",
            path=path,
            url=url,
            duration_ms=timer.elapsed_ms,
            error=str(e),
        )
        return None, path, False
