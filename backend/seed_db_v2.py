from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from seed_models import Base


DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = "5432"
DEFAULT_DB_NAME = "postgres"
DEFAULT_DB_USER = "postgres"
DEFAULT_DB_PASSWORD = "postgres"


def get_database_url() -> str:
    """
    Resolution order:
    1. DATABASE_URL
    2. PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD

    Example:
      export DATABASE_URL='postgresql+psycopg://user:pass@127.0.0.1:5432/seed'
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    host = os.getenv("PGHOST", DEFAULT_DB_HOST)
    port = os.getenv("PGPORT", DEFAULT_DB_PORT)
    database = os.getenv("PGDATABASE", DEFAULT_DB_NAME)
    user = os.getenv("PGUSER", DEFAULT_DB_USER)
    password = os.getenv("PGPASSWORD", DEFAULT_DB_PASSWORD)
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


def build_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    return sa.create_engine(
        database_url or get_database_url(),
        echo=echo,
        future=True,
        pool_pre_ping=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_allowed_origins() -> list[str]:
    raw = os.getenv("SEED_CORS_ORIGINS", "*")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["*"]


def should_initialize_database() -> bool:
    return os.getenv("SEED_INIT_DB", "false").strip().lower() in {"1", "true", "yes", "on"}


def initialize_database(engine: Engine) -> None:
    """
    Explicit bootstrap path for development / first-run setup.

    Order:
    1. Ensure pgcrypto exists for gen_random_uuid().
    2. Create ORM-managed tables, constraints, and indexes.
    3. Install DB-side trigger functions.
    4. Install DB-side triggers, including metadata auto-create on context insert.

    This is intentionally gated by SEED_INIT_DB so startup does not run DDL
    on every restart in stable environments.
    """
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        for statement in _trigger_ddl_statements():
            conn.execute(sa.text(statement))


def _trigger_ddl_statements() -> Sequence[str]:
    return [
        """
        CREATE OR REPLACE FUNCTION seed_set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          NEW.updated_at := now();
          RETURN NEW;
        END;
        $$
        """,
        """
        CREATE OR REPLACE FUNCTION seed_block_update_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'Operation not allowed on immutable/append-only table: %', TG_TABLE_NAME;
        END;
        $$
        """,
        """
        CREATE OR REPLACE FUNCTION seed_create_context_metadata()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          INSERT INTO seed_context_metadata (context_id)
          VALUES (NEW.id)
          ON CONFLICT (context_id) DO NOTHING;
          RETURN NEW;
        END;
        $$
        """,
        "DROP TRIGGER IF EXISTS trg_seed_projects_updated_at ON seed_projects",
        """
        CREATE TRIGGER trg_seed_projects_updated_at
        BEFORE UPDATE ON seed_projects
        FOR EACH ROW
        EXECUTE FUNCTION seed_set_updated_at()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_contexts_no_update ON seed_contexts",
        """
        CREATE TRIGGER trg_seed_contexts_no_update
        BEFORE UPDATE ON seed_contexts
        FOR EACH ROW
        EXECUTE FUNCTION seed_block_update_delete()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_contexts_no_delete ON seed_contexts",
        """
        CREATE TRIGGER trg_seed_contexts_no_delete
        BEFORE DELETE ON seed_contexts
        FOR EACH ROW
        EXECUTE FUNCTION seed_block_update_delete()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_context_metadata_updated_at ON seed_context_metadata",
        """
        CREATE TRIGGER trg_seed_context_metadata_updated_at
        BEFORE UPDATE ON seed_context_metadata
        FOR EACH ROW
        EXECUTE FUNCTION seed_set_updated_at()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_context_metadata_autocreate ON seed_contexts",
        """
        CREATE TRIGGER trg_seed_context_metadata_autocreate
        AFTER INSERT ON seed_contexts
        FOR EACH ROW
        EXECUTE FUNCTION seed_create_context_metadata()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_model_feedback_no_update ON seed_model_feedback",
        """
        CREATE TRIGGER trg_seed_model_feedback_no_update
        BEFORE UPDATE ON seed_model_feedback
        FOR EACH ROW
        EXECUTE FUNCTION seed_block_update_delete()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_model_feedback_no_delete ON seed_model_feedback",
        """
        CREATE TRIGGER trg_seed_model_feedback_no_delete
        BEFORE DELETE ON seed_model_feedback
        FOR EACH ROW
        EXECUTE FUNCTION seed_block_update_delete()
        """,
        "DROP TRIGGER IF EXISTS trg_seed_knowledge_nodes_updated_at ON seed_knowledge_nodes",
        """
        CREATE TRIGGER trg_seed_knowledge_nodes_updated_at
        BEFORE UPDATE ON seed_knowledge_nodes
        FOR EACH ROW
        EXECUTE FUNCTION seed_set_updated_at()
        """,
    ]
