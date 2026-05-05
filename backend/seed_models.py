from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, PrimaryKeyConstraint, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, validates


# -----------------------------------------------------------------------------
# Seed ORM model layer v0.1
# Mirrors the adopted PostgreSQL DDL v0.1.
#
# Settled contract:
# - seed_contexts = immutable after insert
# - seed_model_feedback = append-only
# - metadata / nodes / links = mutable
# - knowledge node kind is app-validated, not DB-constrained
# -----------------------------------------------------------------------------


SOURCE_KINDS = {"web", "chat", "doc"}
PROJECT_STATUSES = {"active", "archived"}
KNOWLEDGE_NODE_STATUSES = {"draft", "published", "archived"}
RELATION_TYPES = {"supports", "quoted_in", "related_to", "promoted_to", "originated"}

# Recommended starter taxonomy. This is intentionally app-level only.
# Set to None to allow any kind without validation.
ALLOWED_KNOWLEDGE_NODE_KINDS: set[str] | None = {
    "wiki_entry",
    "decision",
    "spec",
    "reference",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SeedModelError(ValueError):
    """Base application-level validation error for Seed models."""


class ImmutableRowError(SeedModelError):
    """Raised when code attempts to mutate an immutable row."""


class AppendOnlyRowError(SeedModelError):
    """Raised when code attempts to mutate an append-only row."""


class SeedValidationError(SeedModelError):
    """Raised when a value violates app-level Seed validation rules."""


class Base(DeclarativeBase):
    pass


def _normalize_text_array(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = (raw or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _validate_in(value: str, allowed: set[str], field_name: str) -> str:
    if value not in allowed:
        allowed_display = ", ".join(sorted(allowed))
        raise SeedValidationError(f"{field_name} must be one of: {allowed_display}")
    return value


@dataclass(frozen=True)
class ContextCreate:
    project_id: uuid.UUID
    source_kind: str
    source_uri: str
    source_span_start: int
    source_span_end: int
    selected_text: str
    content_hash: str
    captured_at: datetime
    source_title: str | None = None
    source_external: dict[str, Any] | None = None
    tags: list[str] | None = None
    user_note: str = ""
    destination: list[str] | None = None


@dataclass(frozen=True)
class FeedbackCreate:
    context_id: uuid.UUID
    model_name: str
    response_text: str
    model_version: str | None = None
    response_ref: str | None = None
    source_model_thread_ref: str | None = None
    source_model_message_ref: str | None = None


class SeedProject(Base):
    __tablename__ = "seed_projects"

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_seed_projects_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    parent_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_projects.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    parent_project: Mapped[SeedProject | None] = relationship(
        "SeedProject", remote_side=[id], back_populates="child_projects"
    )
    child_projects: Mapped[list[SeedProject]] = relationship(
        "SeedProject", back_populates="parent_project"
    )

    contexts: Mapped[list[SeedContext]] = relationship(back_populates="project")
    knowledge_nodes: Mapped[list[SeedKnowledgeNode]] = relationship(back_populates="project")

    @validates("status")
    def validate_status(self, _: str, value: str) -> str:
        return _validate_in(value, PROJECT_STATUSES, "SeedProject.status")


class SeedContext(Base):
    __tablename__ = "seed_contexts"

    __table_args__ = (
        CheckConstraint("source_kind IN ('web', 'chat', 'doc')", name="ck_seed_contexts_source_kind"),
        CheckConstraint("source_span_start >= 0", name="ck_seed_contexts_span_start"),
        CheckConstraint("source_span_end > source_span_start", name="ck_seed_contexts_span_end"),
        CheckConstraint("length(trim(selected_text)) > 0", name="ck_seed_contexts_selected_text"),
        UniqueConstraint(
            "project_id",
            "source_kind",
            "source_uri",
            "source_span_start",
            "source_span_end",
            "content_hash",
            name="uq_seed_context_dedup",
        ),
        Index("idx_seed_contexts_project_id", "project_id"),
        Index("idx_seed_contexts_source_kind", "source_kind"),
        Index("idx_seed_contexts_captured_at", sa.text("captured_at DESC")),
        Index(
            "idx_seed_contexts_source_external_gin",
            "source_external",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_projects.id", ondelete="RESTRICT"), nullable=False
    )

    source_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    source_span_start: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    source_span_end: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    selected_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)

    source_external: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    captured_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    project: Mapped[SeedProject] = relationship(back_populates="contexts")
    metadata_row: Mapped[SeedContextMetadata | None] = relationship(
        back_populates="context",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    model_feedback: Mapped[list[SeedModelFeedback]] = relationship(
        back_populates="context", passive_deletes=True
    )
    node_links: Mapped[list[SeedContextNodeLink]] = relationship(
        back_populates="context", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("source_kind")
    def validate_source_kind(self, _: str, value: str) -> str:
        return _validate_in(value, SOURCE_KINDS, "SeedContext.source_kind")

    @validates("source_span_start")
    def validate_span_start(self, _: str, value: int) -> int:
        if value < 0:
            raise SeedValidationError("SeedContext.source_span_start must be >= 0")
        return value

    @validates("source_span_end")
    def validate_span_end(self, _: str, value: int) -> int:
        if value <= 0:
            raise SeedValidationError("SeedContext.source_span_end must be > 0")
        return value

    @validates("selected_text")
    def validate_selected_text(self, _: str, value: str) -> str:
        if not value or not value.strip():
            raise SeedValidationError("SeedContext.selected_text must not be blank")
        return value

    @validates("content_hash")
    def validate_content_hash(self, _: str, value: str) -> str:
        if not value or not value.strip():
            raise SeedValidationError("SeedContext.content_hash must not be blank")
        return value


class SeedContextMetadata(Base):
    __tablename__ = "seed_context_metadata"

    __table_args__ = (
        Index("idx_seed_context_metadata_tags_gin", "tags", postgresql_using="gin"),
        Index(
            "idx_seed_context_metadata_destination_gin",
            "destination",
            postgresql_using="gin",
        ),
    )

    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_contexts.id", ondelete="CASCADE"), primary_key=True
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=text("'{}'::text[]")
    )
    user_note: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=text("''"))
    destination: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=text("'{}'::text[]")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    context: Mapped[SeedContext] = relationship(back_populates="metadata_row")

    @validates("tags")
    def validate_tags(self, _: str, value: Iterable[str] | None) -> list[str]:
        return _normalize_text_array(value)

    @validates("destination")
    def validate_destination(self, _: str, value: Iterable[str] | None) -> list[str]:
        return _normalize_text_array(value)


class SeedModelFeedback(Base):
    __tablename__ = "seed_model_feedback"

    __table_args__ = (
        CheckConstraint("length(trim(response_text)) > 0", name="ck_seed_model_feedback_response_text"),
        Index("idx_seed_model_feedback_context_id", "context_id", sa.text("created_at DESC")),
        Index("idx_seed_model_feedback_model_name", "model_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_contexts.id", ondelete="RESTRICT"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    response_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    response_ref: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_model_thread_ref: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_model_message_ref: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    context: Mapped[SeedContext] = relationship(back_populates="model_feedback")

    @validates("response_text")
    def validate_response_text(self, _: str, value: str) -> str:
        if not value or not value.strip():
            raise SeedValidationError("SeedModelFeedback.response_text must not be blank")
        return value


class SeedKnowledgeNode(Base):
    __tablename__ = "seed_knowledge_nodes"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_seed_knowledge_nodes_status",
        ),
        UniqueConstraint("project_id", "slug", name="uq_seed_knowledge_nodes_project_slug"),
        Index("idx_seed_knowledge_nodes_project_id", "project_id"),
        Index("idx_seed_knowledge_nodes_kind", "kind"),
        Index("idx_seed_knowledge_nodes_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_projects.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    body_md: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=text("''"))
    body_md_hash: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    git_path: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=text("'draft'"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    project: Mapped[SeedProject] = relationship(back_populates="knowledge_nodes")
    context_links: Mapped[list[SeedContextNodeLink]] = relationship(
        back_populates="node", cascade="all, delete-orphan", passive_deletes=True
    )

    @validates("status")
    def validate_status(self, _: str, value: str) -> str:
        return _validate_in(value, KNOWLEDGE_NODE_STATUSES, "SeedKnowledgeNode.status")

    @validates("kind")
    def validate_kind(self, _: str, value: str) -> str:
        if not value or not value.strip():
            raise SeedValidationError("SeedKnowledgeNode.kind must not be blank")
        if ALLOWED_KNOWLEDGE_NODE_KINDS is not None and value not in ALLOWED_KNOWLEDGE_NODE_KINDS:
            allowed_display = ", ".join(sorted(ALLOWED_KNOWLEDGE_NODE_KINDS))
            raise SeedValidationError(
                f"SeedKnowledgeNode.kind must be one of: {allowed_display}"
            )
        return value


class SeedContextNodeLink(Base):
    __tablename__ = "seed_context_node_links"

    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('supports', 'quoted_in', 'related_to', 'promoted_to', 'originated')",
            name="ck_seed_context_node_links_relation_type",
        ),
        PrimaryKeyConstraint("context_id", "node_id", "relation_type"),
        Index("idx_seed_context_node_links_node_id", "node_id"),
        Index("idx_seed_context_node_links_relation_type", "relation_type"),
    )

    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_contexts.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seed_knowledge_nodes.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    context: Mapped[SeedContext] = relationship(back_populates="node_links")
    node: Mapped[SeedKnowledgeNode] = relationship(back_populates="context_links")

    @validates("relation_type")
    def validate_relation_type(self, _: str, value: str) -> str:
        return _validate_in(value, RELATION_TYPES, "SeedContextNodeLink.relation_type")


# -----------------------------------------------------------------------------
# App-layer mutation enforcement
# Database triggers remain the final safety net.
# -----------------------------------------------------------------------------


def _block_context_update(_: Any, __: Any, target: SeedContext) -> None:
    raise ImmutableRowError(
        f"seed_contexts is immutable after insert. context_id={target.id} cannot be updated."
    )



def _block_context_delete(_: Any, __: Any, target: SeedContext) -> None:
    raise ImmutableRowError(
        f"seed_contexts is immutable after insert. context_id={target.id} cannot be deleted."
    )



def _block_feedback_update(_: Any, __: Any, target: SeedModelFeedback) -> None:
    raise AppendOnlyRowError(
        f"seed_model_feedback is append-only. feedback_id={target.id} cannot be updated."
    )



def _block_feedback_delete(_: Any, __: Any, target: SeedModelFeedback) -> None:
    raise AppendOnlyRowError(
        f"seed_model_feedback is append-only. feedback_id={target.id} cannot be deleted."
    )


for _event_name in ("before_update",):
    event.listen(SeedContext, _event_name, _block_context_update)
    event.listen(SeedModelFeedback, _event_name, _block_feedback_update)

for _event_name in ("before_delete",):
    event.listen(SeedContext, _event_name, _block_context_delete)
    event.listen(SeedModelFeedback, _event_name, _block_feedback_delete)


# -----------------------------------------------------------------------------
# Mutable table updated_at maintenance in the app layer
# Database triggers still enforce the same behavior.
# -----------------------------------------------------------------------------


def _touch_updated_at(_: Any, __: Any, target: Any) -> None:
    target.updated_at = utcnow()


for _model in (SeedProject, SeedContextMetadata, SeedKnowledgeNode):
    event.listen(_model, "before_update", _touch_updated_at)


# -----------------------------------------------------------------------------
# Service helpers
# These keep the app aligned with the DB contract.
# -----------------------------------------------------------------------------


def create_context(session: Session, payload: ContextCreate) -> SeedContext:
    """
    Create an immutable context row and its metadata row together.
    This mirrors the adopted direction that every context should have metadata from birth.
    """
    context = SeedContext(
        project_id=payload.project_id,
        source_kind=payload.source_kind,
        source_uri=payload.source_uri,
        source_title=payload.source_title,
        source_span_start=payload.source_span_start,
        source_span_end=payload.source_span_end,
        selected_text=payload.selected_text,
        content_hash=payload.content_hash,
        source_external=payload.source_external or {},
        captured_at=payload.captured_at,
    )
    session.add(context)
    session.flush()
    if context.metadata_row is None:
        session.refresh(context)
    if payload.tags or payload.user_note or payload.destination:
        context.metadata_row.tags = payload.tags or []
        context.metadata_row.user_note = payload.user_note
        context.metadata_row.destination = payload.destination or []
    return context



def append_model_feedback(session: Session, payload: FeedbackCreate) -> SeedModelFeedback:
    feedback = SeedModelFeedback(
        context_id=payload.context_id,
        model_name=payload.model_name,
        model_version=payload.model_version,
        response_text=payload.response_text,
        response_ref=payload.response_ref,
        source_model_thread_ref=payload.source_model_thread_ref,
        source_model_message_ref=payload.source_model_message_ref,
    )
    session.add(feedback)
    return feedback



def add_context_node_link(
    session: Session,
    *,
    context_id: uuid.UUID,
    node_id: uuid.UUID,
    relation_type: str,
) -> SeedContextNodeLink:
    link = SeedContextNodeLink(
        context_id=context_id,
        node_id=node_id,
        relation_type=relation_type,
    )
    session.add(link)
    return link



def get_context_by_dedup_key(
    session: Session,
    *,
    project_id: uuid.UUID,
    source_kind: str,
    source_uri: str,
    source_span_start: int,
    source_span_end: int,
    content_hash: str,
) -> SeedContext | None:
    stmt = sa.select(SeedContext).where(
        SeedContext.project_id == project_id,
        SeedContext.source_kind == source_kind,
        SeedContext.source_uri == source_uri,
        SeedContext.source_span_start == source_span_start,
        SeedContext.source_span_end == source_span_end,
        SeedContext.content_hash == content_hash,
    )
    return session.scalar(stmt)



def create_or_reuse_context(session: Session, payload: ContextCreate) -> SeedContext:
    """
    Honors the proposed dedup rule in app logic before the DB unique constraint fires.
    If the same seed already exists, return it instead of planting a duplicate.
    """
    existing = get_context_by_dedup_key(
        session,
        project_id=payload.project_id,
        source_kind=payload.source_kind,
        source_uri=payload.source_uri,
        source_span_start=payload.source_span_start,
        source_span_end=payload.source_span_end,
        content_hash=payload.content_hash,
    )
    if existing is not None:
        return existing
    return create_context(session, payload)
