"""Book 4 Pydantic schemas for API keys, brain, search, and knowledge nodes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

try:  # Pydantic v2
    from pydantic import ConfigDict
except Exception:  # pragma: no cover - Pydantic v1 fallback
    ConfigDict = None  # type: ignore


PERMISSION_VOCABULARY = {"read", "write", "nodes", "publish", "admin"}
FORMAT_VOCABULARY = {"json", "skill", "plain", "markdown"}
NODE_STATUS_VOCABULARY = {"draft", "published", "archived"}


def _orm_config() -> Dict[str, Any]:
    return {"from_attributes": True} if ConfigDict else {}


class OrmBaseModel(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(**_orm_config())  # type: ignore[misc]
    else:  # pragma: no cover
        class Config:
            orm_mode = True


class SeedApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1)
    role: str = Field("reader", min_length=1)
    domains: List[str] = Field(default_factory=list)
    project_ids: Optional[List[UUID]] = None
    permissions: List[str] = Field(default_factory=lambda: ["read"])
    format: str = Field("plain")
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


class SeedApiKeyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    domains: Optional[List[str]] = None
    project_ids: Optional[List[UUID]] = None
    permissions: Optional[List[str]] = None
    format: Optional[str] = None
    active: Optional[bool] = None
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


class SeedApiKeyPublic(OrmBaseModel):
    id: UUID
    name: str
    key_prefix: str
    role: str
    domains: List[str]
    project_ids: Optional[List[UUID]] = None
    permissions: List[str]
    format: str
    active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    notes: Optional[str] = None


class SeedApiKeyCreated(BaseModel):
    key: str = Field(..., description="Raw key. Returned once only.")
    record: SeedApiKeyPublic


class BrainNode(BaseModel):
    id: UUID
    slug: str
    title: str
    summary_500: str = ""
    deep_link: str
    kind: str = "wiki_entry"
    published_at: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None


class BrainResponse(BaseModel):
    domain: Optional[str] = None
    format: str
    etag: str
    last_updated: Optional[datetime] = None
    node_count: int
    nodes: List[BrainNode]


class SearchResult(BaseModel):
    id: UUID
    slug: str
    title: str
    summary_500: str = ""
    relevance_score: float
    domain: str
    deep_link: str


class SearchResponse(BaseModel):
    query: str
    domain: Optional[str] = None
    results: List[SearchResult]
    degraded: bool = False


class KnowledgeNodeCreate(BaseModel):
    slug: Optional[str] = None
    title: str = Field(..., min_length=1)
    body_md: str = ""
    summary_500: str = ""
    domain: str = "seed"
    kind: str = "wiki_entry"
    tags: List[str] = Field(default_factory=list)
    status: str = "draft"


class KnowledgeNodeUpdate(BaseModel):
    slug: Optional[str] = None
    title: Optional[str] = Field(None, min_length=1)
    body_md: Optional[str] = None
    summary_500: Optional[str] = None
    domain: Optional[str] = None
    kind: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None
    last_verified_at: Optional[datetime] = None


class KnowledgeNodeResponse(OrmBaseModel):
    id: UUID
    slug: str
    title: str
    body_md: str = ""
    summary_500: str = ""
    domain: str = "seed"
    kind: str = "wiki_entry"
    status: str = "draft"
    tags: List[str] = Field(default_factory=list)
    body_md_hash: Optional[str] = None
    git_path: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None
    contexts: List[Dict[str, Any]] = Field(default_factory=list)


class NodeLinkRequest(BaseModel):
    context_id: UUID


class NodeLinkResponse(BaseModel):
    node_id: UUID
    context_id: UUID
    linked: bool
