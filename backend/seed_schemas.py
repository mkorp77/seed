from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    slug: str
    name: str
    description: str | None = None
    parent_project_id: uuid.UUID | None = None
    status: str = "active"


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    parent_project_id: uuid.UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ContextCreate(BaseModel):
    project_id: uuid.UUID
    source_kind: str
    source_uri: str
    source_title: str | None = None
    source_span_start: int
    source_span_end: int
    selected_text: str
    content_hash: str
    captured_at: datetime
    source_external: dict = Field(default_factory=dict)

    tags: list[str] = Field(default_factory=list)
    user_note: str = ""
    destination: list[str] = Field(default_factory=list)
    publish_to_vault: bool = False


class ContextMetadataRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    context_id: uuid.UUID
    tags: list[str] = Field(default_factory=list)
    user_note: str = ""
    destination: list[str] = Field(default_factory=list)
    updated_at: datetime


class MetadataUpdate(BaseModel):
    tags: list[str] | None = None
    user_note: str | None = None
    destination: list[str] | None = None


class FeedbackCreate(BaseModel):
    model_name: str
    model_version: str | None = None
    response_text: str
    response_ref: str | None = None
    source_model_thread_ref: str | None = None
    source_model_message_ref: str | None = None


class FeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    context_id: uuid.UUID
    model_name: str
    model_version: str | None = None
    response_text: str
    response_ref: str | None = None
    source_model_thread_ref: str | None = None
    source_model_message_ref: str | None = None
    created_at: datetime


class ContextRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_kind: str
    source_uri: str
    source_title: str | None = None
    source_span_start: int
    source_span_end: int
    selected_text: str
    content_hash: str
    source_external: dict = Field(default_factory=dict)
    captured_at: datetime
    created_at: datetime
    metadata: ContextMetadataRead = Field(validation_alias="metadata_row")
    feedback: list[FeedbackRead] = Field(default_factory=list, validation_alias="model_feedback")


class ContextListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    project_id: uuid.UUID
    source_kind: str
    source_uri: str
    source_title: str | None = None
    source_span_start: int
    source_span_end: int
    selected_text: str
    content_hash: str
    captured_at: datetime
    created_at: datetime
    metadata: ContextMetadataRead = Field(validation_alias="metadata_row")


class ContextListResponse(BaseModel):
    items: list[ContextListItem]
    total: int


class ContextPublishRequest(BaseModel):
    domain: str | None = Field(
        default=None,
        description="Optional user override. If omitted, domain is detected from tags.",
    )
    subdirectory: str = Field(default="raw")


class ContextPublishResponse(BaseModel):
    context_id: str
    domain: str
    subdirectory: str
    file_path: str
    file_size: int
    published_at: str


class DomainDetectResponse(BaseModel):
    domain: str
    confidence: str
    matching_tags: list[str]
