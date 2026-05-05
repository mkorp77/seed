from __future__ import annotations

import uuid
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session, selectinload

from seed_models import (
    ContextCreate as ModelContextCreate,
    FeedbackCreate as ModelFeedbackCreate,
    SeedContext,
    SeedContextMetadata,
    SeedModelFeedback,
    SeedProject,
    append_model_feedback,
    create_or_reuse_context,
)
from seed_schemas import ContextCreate, FeedbackCreate, MetadataUpdate, ProjectCreate


_CONTEXT_LOAD_OPTIONS = (
    selectinload(SeedContext.metadata_row),
    selectinload(SeedContext.model_feedback),
)


def create_project(session: Session, payload: ProjectCreate) -> SeedProject:
    project = SeedProject(
        slug=payload.slug.strip(),
        name=payload.name.strip(),
        description=payload.description,
        parent_project_id=payload.parent_project_id,
        status=payload.status,
    )
    session.add(project)
    session.flush()
    return project


def list_projects(session: Session) -> Sequence[SeedProject]:
    stmt = sa.select(SeedProject).order_by(SeedProject.created_at.desc(), SeedProject.slug.asc())
    return session.scalars(stmt).all()


def create_context_record(session: Session, payload: ContextCreate) -> tuple[SeedContext, bool]:
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
        return existing, False

    context = create_or_reuse_context(
        session,
        ModelContextCreate(
            project_id=payload.project_id,
            source_kind=payload.source_kind,
            source_uri=payload.source_uri,
            source_title=payload.source_title,
            source_span_start=payload.source_span_start,
            source_span_end=payload.source_span_end,
            selected_text=payload.selected_text,
            content_hash=payload.content_hash,
            captured_at=payload.captured_at,
            source_external=payload.source_external,
            tags=payload.tags,
            user_note=payload.user_note,
            destination=payload.destination,
        ),
    )
    session.flush()
    return context, True



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
    stmt = (
        sa.select(SeedContext)
        .options(*_CONTEXT_LOAD_OPTIONS)
        .where(
            SeedContext.project_id == project_id,
            SeedContext.source_kind == source_kind,
            SeedContext.source_uri == source_uri,
            SeedContext.source_span_start == source_span_start,
            SeedContext.source_span_end == source_span_end,
            SeedContext.content_hash == content_hash,
        )
    )
    return session.scalar(stmt)



def get_context(session: Session, context_id: uuid.UUID) -> SeedContext | None:
    stmt = (
        sa.select(SeedContext)
        .options(*_CONTEXT_LOAD_OPTIONS)
        .where(SeedContext.id == context_id)
    )
    return session.scalar(stmt)



def list_contexts(
    session: Session,
    *,
    project_id: uuid.UUID | None = None,
    tags: list[str] | None = None,
) -> Sequence[SeedContext]:
    stmt = sa.select(SeedContext).options(*_CONTEXT_LOAD_OPTIONS)

    if project_id is not None:
        stmt = stmt.where(SeedContext.project_id == project_id)

    normalized_tags = [tag.strip() for tag in (tags or []) if tag and tag.strip()]
    if normalized_tags:
        stmt = stmt.join(SeedContextMetadata, SeedContextMetadata.context_id == SeedContext.id)
        stmt = stmt.where(SeedContextMetadata.tags.overlap(normalized_tags))

    stmt = stmt.order_by(SeedContext.captured_at.desc(), SeedContext.created_at.desc())
    return session.scalars(stmt).all()



def update_context_metadata(
    session: Session,
    *,
    context_id: uuid.UUID,
    payload: MetadataUpdate,
) -> SeedContextMetadata | None:
    metadata = session.get(SeedContextMetadata, context_id)
    if metadata is None:
        return None

    if payload.tags is not None:
        metadata.tags = payload.tags
    if payload.user_note is not None:
        metadata.user_note = payload.user_note
    if payload.destination is not None:
        metadata.destination = payload.destination

    session.flush()
    return metadata



def append_context_feedback(
    session: Session,
    *,
    context_id: uuid.UUID,
    payload: FeedbackCreate,
) -> SeedModelFeedback:
    feedback = append_model_feedback(
        session,
        ModelFeedbackCreate(
            context_id=context_id,
            model_name=payload.model_name,
            model_version=payload.model_version,
            response_text=payload.response_text,
            response_ref=payload.response_ref,
            source_model_thread_ref=payload.source_model_thread_ref,
            source_model_message_ref=payload.source_model_message_ref,
        ),
    )
    session.flush()
    return feedback
