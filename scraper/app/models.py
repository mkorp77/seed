"""Request and response models for the /scrape endpoint."""
from __future__ import annotations
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, HttpUrl


PathTaken = Literal["discourse_api", "playwright"]


class ScrapeRequest(BaseModel):
    url: HttpUrl
    project_id: Optional[str] = None
    post_to_seed: bool = True
    force_path: Optional[PathTaken] = None


class ContextRecord(BaseModel):
    """Matches the Seed API contract for POST /api/contexts."""
    project_id: Optional[str] = None
    source_kind: str = "web"
    source_uri: str
    source_title: str
    source_span_start: int = 0
    source_span_end: int
    selected_text: str
    content_hash: str
    captured_at: str
    source_external: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    user_note: str = ""
    destination: list[Any] = Field(default_factory=list)


class ScrapeAttempt(BaseModel):
    path: PathTaken
    status: Optional[int] = None
    error: Optional[str] = None


class ScrapeSuccess(BaseModel):
    ok: Literal[True] = True
    path_taken: PathTaken
    fallback: bool
    context: ContextRecord
    context_id: Optional[str] = None


class ScrapeFailure(BaseModel):
    ok: Literal[False] = False
    error: str
    attempts: list[ScrapeAttempt]
