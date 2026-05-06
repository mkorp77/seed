"""
Book 4 SQLAlchemy model additions.

Integration notes:
- Prefer importing your existing project Base before this file is imported by Alembic/FastAPI.
- If your repo already defines SeedKnowledgeNode, paste the four Book 4 columns from
  SeedKnowledgeNodeBook4Columns into that existing model rather than registering the
  compatibility SeedKnowledgeNode class below.
- The compatibility SeedKnowledgeNode class is intentionally minimal and uses
  extend_existing=True so Book 4 routers can be type/syntax checked outside the repo.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime


try:  # Use the repo Base when available.
    from seed_database import Base  # type: ignore
except Exception:  # pragma: no cover - repo integration fallback
    try:
        from database import Base  # type: ignore
    except Exception:  # pragma: no cover - standalone fallback
        class Base(DeclarativeBase):
            pass


class SeedApiKey(Base):
    __tablename__ = "seed_api_keys"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    domains: Mapped[List[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    project_ids: Mapped[Optional[List[PyUUID]]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    permissions: Mapped[List[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{read}'::text[]")
    )
    format: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'plain'"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SeedKnowledgeNodeBook4Columns:
    """Paste/inherit these fields on the repo's existing SeedKnowledgeNode model."""

    domain: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'seed'"))
    summary_500: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SeedKnowledgeNode(Base, SeedKnowledgeNodeBook4Columns):
    """
    Minimal compatibility definition for Book 4 routers.

    In the real repo, replace this with the existing full model or remove this class
    after adding SeedKnowledgeNodeBook4Columns to the canonical model.
    """

    __tablename__ = "seed_knowledge_nodes"
    __table_args__ = {"extend_existing": True}

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'wiki_entry'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    body_md_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    git_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
