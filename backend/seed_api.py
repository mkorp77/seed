from __future__ import annotations

import ipaddress
import os
import re
import uuid
from collections.abc import Generator
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session, sessionmaker

import seed_crud as crud
from seed_domain import detect_domain, parse_tags_param
from seed_publish import ContextNotFoundError, VaultPublishError, publish_context_to_vault
from seed_schemas import (
    ContextCreate,
    ContextListItem,
    ContextListResponse,
    ContextPublishRequest,
    ContextPublishResponse,
    ContextRead,
    DomainDetectResponse,
    FeedbackCreate,
    FeedbackRead,
    MetadataUpdate,
    ProjectCreate,
    ProjectRead,
)


router = APIRouter(prefix="/api", tags=["seed"])


# App setup contract:
# app.state.session_factory must be set to a SQLAlchemy sessionmaker[Session]
# Example:
#   engine = sa.create_engine(DB_URL, future=True)
#   app = create_app(sessionmaker(bind=engine, autoflush=False, autocommit=False))


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


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectRead:
    project = crud.create_project(db, payload)
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectRead]:
    projects = crud.list_projects(db)
    return [ProjectRead.model_validate(project) for project in projects]


@router.post("/contexts", response_model=ContextRead, status_code=status.HTTP_201_CREATED)
def create_context(
    payload: ContextCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> ContextRead:
    context, created = crud.create_context_record(db, payload)
    context = crud.get_context(db, context.id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Context disappeared after create.")
    if not created:
        response.status_code = status.HTTP_200_OK
    return ContextRead.model_validate(context)


@router.patch("/contexts/{context_id}/metadata", response_model=ContextRead)
def update_context_metadata(
    context_id: uuid.UUID,
    payload: MetadataUpdate,
    db: Session = Depends(get_db),
) -> ContextRead:
    metadata = crud.update_context_metadata(db, context_id=context_id, payload=payload)
    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context metadata not found.")

    context = crud.get_context(db, context_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found.")
    return ContextRead.model_validate(context)


@router.post("/contexts/{context_id}/feedback", response_model=FeedbackRead, status_code=status.HTTP_201_CREATED)
def append_context_feedback(
    context_id: uuid.UUID,
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
) -> FeedbackRead:
    context = crud.get_context(db, context_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found.")

    feedback = crud.append_context_feedback(db, context_id=context_id, payload=payload)
    db.refresh(feedback)
    return FeedbackRead.model_validate(feedback)


@router.get("/contexts", response_model=ContextListResponse)
def list_contexts(
    project_id: uuid.UUID | None = None,
    tags: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ContextListResponse:
    contexts = crud.list_contexts(db, project_id=project_id, tags=tags)
    items = [ContextListItem.model_validate(context) for context in contexts]
    return ContextListResponse(items=items, total=len(items))


@router.get("/contexts/{context_id}", response_model=ContextRead)
def get_context(context_id: uuid.UUID, db: Session = Depends(get_db)) -> ContextRead:
    context = crud.get_context(db, context_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found.")
    return ContextRead.model_validate(context)


@router.get("/domains/detect", response_model=DomainDetectResponse)
def detect_domain_route(tags: str = Query(default="")) -> DomainDetectResponse:
    detection = detect_domain(parse_tags_param(tags))
    return DomainDetectResponse(
        domain=detection.domain,
        confidence=detection.confidence,
        matching_tags=detection.matching_tags,
    )


@router.post("/contexts/{context_id}/publish", response_model=ContextPublishResponse)
def publish_context_route(
    context_id: uuid.UUID,
    body: ContextPublishRequest | None = None,
    db: Session = Depends(get_db),
) -> ContextPublishResponse:
    body = body or ContextPublishRequest()
    try:
        result = publish_context_to_vault(
            db,
            context_id,
            domain=body.domain,
            subdirectory=body.subdirectory,
        )
    except ContextNotFoundError:
        raise HTTPException(status_code=404, detail="Context not found")
    except VaultPublishError as exc:
        raise HTTPException(status_code=500, detail=f"Vault publish failed: {exc}")
    return ContextPublishResponse(**result)


SAVE_FOLDER = os.environ.get("SEED_SAVE_FOLDER", r"D:\Source-Shared\Seed Web Capture")


def _is_private_url(url: str) -> bool:
    """Block scraping of internal/private URLs."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0", ""):
            return True
        if hostname.startswith("192.168.") or hostname.startswith("10.") or hostname.startswith("172."):
            return True
        try:
            ip = ipaddress.ip_address(hostname)
            return ip.is_private or ip.is_loopback or ip.is_reserved
        except ValueError:
            pass
        return False
    except Exception:
        return True


@router.post("/scrape")
async def scrape_proxy(request: Request):
    """Proxy scrape requests to the internal scraper container."""
    body = await request.json()
    url = body.get("url", "")
    if _is_private_url(url):
        raise HTTPException(status_code=403, detail="Private/internal URLs are not allowed")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "http://127.0.0.1:3000/scrape",
                json=body,
            )
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Scraper service unavailable")
    except httpx.ReadTimeout:
        raise HTTPException(status_code=504, detail="Scraper timeout")


@router.post("/save-to-folder")
async def save_to_folder(request: Request):
    """Save markdown content to the shared folder on SOURCE."""
    body = await request.json()
    filename = body.get("filename", "")
    content = body.get("content", "")
    if not filename or not content:
        raise HTTPException(status_code=422, detail="filename and content required")
    filename = re.sub(r'[<>:"/\\|?*]', '-', filename)
    os.makedirs(SAVE_FOLDER, exist_ok=True)
    filepath = os.path.join(SAVE_FOLDER, filename)
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filepath)
        n = 2
        while os.path.exists(f"{base}-{n}{ext}"):
            n += 1
        filepath = f"{base}-{n}{ext}"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": filepath, "size": len(content)}



def create_app(session_factory: sessionmaker[Session]) -> FastAPI:
    app = FastAPI(title="Seed API", version="0.1.0")
    app.state.session_factory = session_factory
    app.include_router(router)
    return app
