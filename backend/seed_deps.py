"""FastAPI request-scoped dependencies shared across routers.

Lives in its own module so that routers can depend on `get_db` without
importing each other (avoids circular imports between seed_api and seed_auth).
"""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


def get_db(request: Request) -> Generator[Session, None, None]:
    session_factory: sessionmaker | None = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise RuntimeError("app.state.session_factory is not configured")

    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
