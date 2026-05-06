"""Book 4 knowledge node lifecycle routes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, nullslast, select, text

from seed_auth import (
    db_add,
    db_commit,
    db_execute,
    db_refresh,
    db_rollback,
    get_api_key,
    get_db,
    has_permission,
    require_permission,
    utcnow,
    visible_domains_for_key,
)
from seed_models import SeedApiKey
from seed_schemas_book4 import (
    NODE_STATUS_VOCABULARY,
    KnowledgeNodeCreate,
    KnowledgeNodeResponse,
    KnowledgeNodeUpdate,
    NodeLinkRequest,
    NodeLinkResponse,
)

try:  # Prefer the repo's canonical model if present.
    from seed_models import SeedKnowledgeNode  # type: ignore
except Exception:  # pragma: no cover
    try:
        from models import SeedKnowledgeNode  # type: ignore
    except Exception:  # pragma: no cover
        from seed_models_book4_delta import SeedKnowledgeNode

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nodes", tags=["nodes"])

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _node_col(name: str) -> Any:
    col = getattr(SeedKnowledgeNode, name, None)
    if col is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SeedKnowledgeNode.{name} is missing")
    return col


def _published_filter() -> Any:
    status_col = getattr(SeedKnowledgeNode, "status", None)
    if status_col is None:
        return _node_col("published_at").isnot(None)
    return status_col == "published"


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "untitled-node"


def _hash_body(body_md: str) -> str:
    return hashlib.sha256((body_md or "").encode("utf-8")).hexdigest()


def _validate_status(value: str) -> str:
    if value not in NODE_STATUS_VOCABULARY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of {sorted(NODE_STATUS_VOCABULARY)}",
        )
    return value


def _safe_identifier(name: str, label: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise RuntimeError(f"Unsafe {label} identifier: {name!r}")
    return name


def _link_table() -> str:
    return _safe_identifier(os.getenv("SEED_NODE_CONTEXT_LINK_TABLE", "seed_context_node_links"), "link table")


def _context_table() -> str:
    return _safe_identifier(os.getenv("SEED_CONTEXT_TABLE", "seed_context_records"), "context table")


def _permissions(api_key: Optional[SeedApiKey]) -> set[str]:
    return set(getattr(api_key, "permissions", []) or []) if api_key is not None else set()


def _domain_allowed(api_key: Optional[SeedApiKey], domain: str) -> bool:
    if api_key is not None and "admin" in _permissions(api_key):
        return True
    visible = visible_domains_for_key(api_key)
    return "*" in visible or domain in visible


def _assert_domain_allowed(api_key: Optional[SeedApiKey], domain: str, *, not_found: bool = False) -> None:
    if not _domain_allowed(api_key, domain):
        if not_found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Domain not allowed: {domain}")


def _ordered(stmt: Any) -> Any:
    updated_at = getattr(SeedKnowledgeNode, "updated_at", None)
    published_at = getattr(SeedKnowledgeNode, "published_at", None)
    if updated_at is not None and published_at is not None:
        return stmt.order_by(nullslast(desc(updated_at)), nullslast(desc(published_at)), _node_col("title").asc())
    if updated_at is not None:
        return stmt.order_by(nullslast(desc(updated_at)), _node_col("title").asc())
    return stmt.order_by(_node_col("title").asc())


async def _get_node(db: Any, node_id: UUID) -> Any:
    result = await db_execute(db, select(SeedKnowledgeNode).where(_node_col("id") == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


async def _get_node_for_key(
    db: Any,
    node_id: UUID,
    api_key: Optional[SeedApiKey],
    *, require_manager: bool = False,
) -> Any:
    node = await _get_node(db, node_id)
    _assert_domain_allowed(api_key, str(getattr(node, "domain", "seed") or "seed"), not_found=True)
    if require_manager:
        return node
    if not (api_key is not None and (has_permission(api_key, "nodes") or has_permission(api_key, "admin"))):
        if getattr(node, "status", None) != "published":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


async def _load_contexts(db: Any, node_id: UUID) -> list[dict[str, Any]]:
    link_table = _link_table()
    context_table = _context_table()
    stmt = text(
        f"""
        SELECT c.*
        FROM {context_table} c
        INNER JOIN {link_table} l ON l.context_id = c.id
        WHERE l.node_id = :node_id
        ORDER BY COALESCE(c.created_at, now()) DESC
        LIMIT 100
        """
    )
    try:
        result = await db_execute(db, stmt, {"node_id": str(node_id)})
        rows = list(result.mappings().all())
    except Exception as exc:
        logger.info("seed_nodes.context_load_skipped", extra={"node_id": str(node_id), "error": str(exc)})
        return []
    return [dict(row) for row in rows]


def _node_response(node: Any, contexts: Optional[list[dict[str, Any]]] = None) -> KnowledgeNodeResponse:
    data = {
        "id": getattr(node, "id"),
        "slug": getattr(node, "slug", ""),
        "title": getattr(node, "title", ""),
        "body_md": getattr(node, "body_md", "") or "",
        "summary_500": getattr(node, "summary_500", "") or "",
        "domain": getattr(node, "domain", "seed") or "seed",
        "kind": getattr(node, "kind", "wiki_entry") or "wiki_entry",
        "status": getattr(node, "status", "draft") or "draft",
        "tags": list(getattr(node, "tags", []) or []),
        "body_md_hash": getattr(node, "body_md_hash", None),
        "git_path": getattr(node, "git_path", None),
        "created_at": getattr(node, "created_at", None),
        "updated_at": getattr(node, "updated_at", None),
        "published_at": getattr(node, "published_at", None),
        "last_verified_at": getattr(node, "last_verified_at", None),
        "contexts": contexts or [],
    }
    if hasattr(KnowledgeNodeResponse, "model_validate"):
        return KnowledgeNodeResponse.model_validate(data)  # type: ignore[attr-defined]
    return KnowledgeNodeResponse.parse_obj(data)  # type: ignore[attr-defined]


def _touch_updated_at(node: Any) -> None:
    if hasattr(node, "updated_at"):
        setattr(node, "updated_at", utcnow())


def _yaml_string(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_list(values: list[Any]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(json.dumps(str(v), ensure_ascii=False) for v in values) + "]"


def _node_markdown(node: Any) -> str:
    body = getattr(node, "body_md", "") or ""
    summary = getattr(node, "summary_500", "") or ""
    lines = [
        "---",
        f"id: {_yaml_string(getattr(node, 'id', None))}",
        f"slug: {_yaml_string(getattr(node, 'slug', ''))}",
        f"title: {_yaml_string(getattr(node, 'title', ''))}",
        f"domain: {_yaml_string(getattr(node, 'domain', 'seed'))}",
        f"kind: {_yaml_string(getattr(node, 'kind', 'wiki_entry'))}",
        f"status: {_yaml_string(getattr(node, 'status', 'published'))}",
        f"tags: {_yaml_list(list(getattr(node, 'tags', []) or []))}",
        f"body_md_hash: {_yaml_string(getattr(node, 'body_md_hash', None))}",
        f"published_at: {_yaml_string(getattr(node, 'published_at', None).isoformat() if getattr(node, 'published_at', None) else None)}",
        f"last_verified_at: {_yaml_string(getattr(node, 'last_verified_at', None).isoformat() if getattr(node, 'last_verified_at', None) else None)}",
        "summary_500: |",
    ]
    if summary.strip():
        lines.extend([f"  {line}" for line in summary.splitlines()])
    else:
        lines.append("  ")
    lines.extend(["---", "", f"# {getattr(node, 'title', '')}", "", body.rstrip(), ""])
    return "\n".join(lines)


def _vault_root() -> Path:
    raw = os.getenv("SEED_VAULT_PATH")
    if not raw:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SEED_VAULT_PATH is not configured")
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_node_to_vault(node: Any) -> str:
    root = _vault_root()
    domain = _slugify(str(getattr(node, "domain", "seed") or "seed"))
    slug = _slugify(str(getattr(node, "slug", "untitled-node") or "untitled-node"))
    rel_path = Path("wiki") / domain / f"{slug}.md"
    dest = (root / rel_path).resolve()
    if root not in dest.parents and dest != root:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Resolved vault path escaped vault root")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_node_markdown(node), encoding="utf-8")
    return rel_path.as_posix()


def _git_commit(vault_root: Path, rel_path: str, message: str) -> None:
    if os.getenv("SEED_SKIP_GIT_COMMIT", "").lower() in {"1", "true", "yes"}:
        return
    try:
        subprocess.run(["git", "add", rel_path], cwd=str(vault_root), check=True, capture_output=True, text=True)
        status_run = subprocess.run(
            ["git", "status", "--porcelain", "--", rel_path],
            cwd=str(vault_root),
            check=True,
            capture_output=True,
            text=True,
        )
        if not status_run.stdout.strip():
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(vault_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Git publish failed: {detail}") from exc


@router.post("", response_model=KnowledgeNodeResponse)
async def create_node(
    payload: KnowledgeNodeCreate,
    db: Any = Depends(get_db),
    api_key: SeedApiKey = Depends(require_permission("nodes")),
) -> KnowledgeNodeResponse:
    _assert_domain_allowed(api_key, payload.domain)
    slug = _slugify(payload.slug or payload.title)
    now = utcnow()
    body_hash = _hash_body(payload.body_md)

    row = SeedKnowledgeNode(
        slug=slug,
        title=payload.title,
        body_md=payload.body_md,
        summary_500=payload.summary_500,
        domain=payload.domain,
        kind=payload.kind,
        tags=payload.tags,
        status="draft",
        body_md_hash=body_hash,
    )
    if hasattr(row, "created_at") and getattr(row, "created_at", None) is None:
        row.created_at = now
    if hasattr(row, "updated_at"):
        row.updated_at = now

    db_add(db, row)
    try:
        await db_commit(db)
        await db_refresh(db, row)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create node") from exc
    return _node_response(row)


@router.get("", response_model=List[KnowledgeNodeResponse])
async def list_nodes(
    domain: Optional[str] = Query(None),
    db: Any = Depends(get_db),
    api_key: Optional[SeedApiKey] = Depends(get_api_key),
) -> list[KnowledgeNodeResponse]:
    if api_key is not None and not has_permission(api_key, "read"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission: read")

    visible = visible_domains_for_key(api_key)
    stmt = select(SeedKnowledgeNode)
    if domain:
        if "*" not in visible and domain not in visible:
            return []
        stmt = stmt.where(_node_col("domain") == domain)
    elif "*" not in visible:
        if not visible:
            return []
        stmt = stmt.where(_node_col("domain").in_(visible))

    if not (api_key is not None and (has_permission(api_key, "nodes") or has_permission(api_key, "admin"))):
        stmt = stmt.where(_published_filter())

    stmt = _ordered(stmt).limit(500)
    result = await db_execute(db, stmt)
    rows = list(result.scalars().all())
    return [_node_response(row) for row in rows]


@router.get("/{node_id}", response_model=KnowledgeNodeResponse)
async def get_node(
    node_id: UUID,
    db: Any = Depends(get_db),
    api_key: Optional[SeedApiKey] = Depends(get_api_key),
) -> KnowledgeNodeResponse:
    if api_key is not None and not has_permission(api_key, "read"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission: read")
    node = await _get_node_for_key(db, node_id, api_key)
    contexts = await _load_contexts(db, node_id)
    return _node_response(node, contexts)


@router.patch("/{node_id}", response_model=KnowledgeNodeResponse)
async def update_node(
    node_id: UUID,
    payload: KnowledgeNodeUpdate,
    db: Any = Depends(get_db),
    api_key: SeedApiKey = Depends(require_permission("nodes")),
) -> KnowledgeNodeResponse:
    node = await _get_node_for_key(db, node_id, api_key, require_manager=True)
    _assert_domain_allowed(api_key, str(getattr(node, "domain", "seed") or "seed"), not_found=True)

    data = payload.dict(exclude_unset=True) if hasattr(payload, "dict") else payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        data["status"] = _validate_status(data["status"])
    if "domain" in data and data["domain"] is not None:
        _assert_domain_allowed(api_key, data["domain"])
    if "slug" in data and data["slug"]:
        data["slug"] = _slugify(data["slug"])

    allowed = {"slug", "title", "body_md", "summary_500", "domain", "kind", "tags", "status", "last_verified_at"}
    for field, value in data.items():
        if field not in allowed:
            continue
        setattr(node, field, value)

    if "body_md" in data:
        setattr(node, "body_md_hash", _hash_body(data["body_md"] or ""))
    _touch_updated_at(node)

    try:
        await db_commit(db)
        await db_refresh(db, node)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update node") from exc
    contexts = await _load_contexts(db, node_id)
    return _node_response(node, contexts)


@router.post("/{node_id}/publish", response_model=KnowledgeNodeResponse)
async def publish_node(
    node_id: UUID,
    db: Any = Depends(get_db),
    api_key: SeedApiKey = Depends(require_permission("publish")),
) -> KnowledgeNodeResponse:
    node = await _get_node_for_key(db, node_id, api_key, require_manager=True)
    _assert_domain_allowed(api_key, str(getattr(node, "domain", "seed") or "seed"), not_found=True)

    setattr(node, "status", "published")
    setattr(node, "published_at", utcnow())
    setattr(node, "body_md_hash", _hash_body(getattr(node, "body_md", "") or ""))
    _touch_updated_at(node)

    vault_root = _vault_root()
    rel_path = _write_node_to_vault(node)
    if hasattr(node, "git_path"):
        setattr(node, "git_path", rel_path)

    _git_commit(vault_root, rel_path, f"Publish node {getattr(node, 'slug', node_id)}")

    try:
        await db_commit(db)
        await db_refresh(db, node)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update published node") from exc

    contexts = await _load_contexts(db, node_id)
    return _node_response(node, contexts)


@router.post("/{node_id}/link", response_model=NodeLinkResponse)
async def link_context_to_node(
    node_id: UUID,
    payload: NodeLinkRequest,
    db: Any = Depends(get_db),
    api_key: SeedApiKey = Depends(require_permission("nodes")),
) -> NodeLinkResponse:
    node = await _get_node_for_key(db, node_id, api_key, require_manager=True)
    _assert_domain_allowed(api_key, str(getattr(node, "domain", "seed") or "seed"), not_found=True)

    stmt = text(
        f"INSERT INTO {_link_table()} (node_id, context_id) VALUES (:node_id, :context_id) ON CONFLICT DO NOTHING"
    )
    try:
        await db_execute(db, stmt, {"node_id": str(node_id), "context_id": str(payload.context_id)})
        await db_commit(db)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to link context to node") from exc
    return NodeLinkResponse(node_id=node_id, context_id=payload.context_id, linked=True)


@router.delete("/{node_id}/link/{ctx_id}", response_model=NodeLinkResponse)
async def unlink_context_from_node(
    node_id: UUID,
    ctx_id: UUID,
    db: Any = Depends(get_db),
    api_key: SeedApiKey = Depends(require_permission("nodes")),
) -> NodeLinkResponse:
    node = await _get_node_for_key(db, node_id, api_key, require_manager=True)
    _assert_domain_allowed(api_key, str(getattr(node, "domain", "seed") or "seed"), not_found=True)

    stmt = text(f"DELETE FROM {_link_table()} WHERE node_id = :node_id AND context_id = :context_id")
    try:
        await db_execute(db, stmt, {"node_id": str(node_id), "context_id": str(ctx_id)})
        await db_commit(db)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unlink context from node") from exc
    return NodeLinkResponse(node_id=node_id, context_id=ctx_id, linked=False)
