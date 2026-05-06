"""Book 4 brain endpoint and format renderers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import desc, func, nullslast, or_, select

from seed_auth import (
    db_execute,
    deep_link,
    get_api_key,
    get_db,
    has_permission,
    resolve_format,
    role_for_key,
    visible_domains_for_key,
)
from seed_models import SeedApiKey

try:  # Prefer the repo's canonical model if present.
    from seed_models import SeedKnowledgeNode  # type: ignore
except Exception:  # pragma: no cover
    try:
        from models import SeedKnowledgeNode  # type: ignore
    except Exception:  # pragma: no cover
        from seed_models_book4_delta import SeedKnowledgeNode

from seed_schemas_book4 import BrainNode, BrainResponse

router = APIRouter(prefix="/brain", tags=["brain"])


def _node_col(name: str) -> Any:
    col = getattr(SeedKnowledgeNode, name, None)
    if col is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SeedKnowledgeNode.{name} is missing")
    return col


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dt_value(value: Any) -> Optional[datetime]:
    return value if isinstance(value, datetime) else None


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _published_filter() -> Any:
    status_col = getattr(SeedKnowledgeNode, "status", None)
    if status_col is None:
        return _node_col("published_at").isnot(None)
    return status_col == "published"


def _domain_filter(stmt: Any, visible_domains: list[str], requested_domain: Optional[str]) -> Any:
    domain_col = _node_col("domain")
    if requested_domain:
        if "*" not in visible_domains and requested_domain not in visible_domains:
            return stmt.where(False)
        return stmt.where(domain_col == requested_domain)
    if "*" in visible_domains:
        return stmt
    if not visible_domains:
        return stmt.where(False)
    return stmt.where(domain_col.in_(visible_domains))


def _topic_filter(stmt: Any, topic: Optional[str]) -> Any:
    if not topic:
        return stmt
    title = func.coalesce(_node_col("title"), "")
    summary = func.coalesce(_node_col("summary_500"), "")
    body = func.coalesce(_node_col("body_md"), "")
    search_text = func.concat_ws(" ", title, summary, body)
    vector = func.to_tsvector("english", search_text)
    query = func.plainto_tsquery("english", topic)
    pattern = f"%{topic}%"
    return stmt.where(
        or_(
            vector.op("@@")(query),
            _node_col("title").ilike(pattern),
            _node_col("summary_500").ilike(pattern),
        )
    )


def _order_statement(stmt: Any) -> Any:
    published_at = getattr(SeedKnowledgeNode, "published_at", None)
    updated_at = getattr(SeedKnowledgeNode, "updated_at", None)
    if published_at is not None and updated_at is not None:
        return stmt.order_by(nullslast(desc(published_at)), nullslast(desc(updated_at)), _node_col("title").asc())
    if published_at is not None:
        return stmt.order_by(nullslast(desc(published_at)), _node_col("title").asc())
    return stmt.order_by(_node_col("title").asc())


async def _load_nodes(db: Any, api_key: Optional[SeedApiKey], domain: Optional[str], topic: Optional[str]) -> list[Any]:
    stmt = select(SeedKnowledgeNode).where(_published_filter())
    stmt = _domain_filter(stmt, visible_domains_for_key(api_key), domain)
    stmt = _topic_filter(stmt, topic)
    stmt = _order_statement(stmt).limit(250)
    result = await db_execute(db, stmt)
    return list(result.scalars().all())


def _last_updated(nodes: Iterable[Any]) -> Optional[datetime]:
    values: list[datetime] = []
    for node in nodes:
        for attr in ("last_verified_at", "published_at", "updated_at", "created_at"):
            value = _dt_value(getattr(node, attr, None))
            if value:
                values.append(value)
    return max(values) if values else None


def _to_brain_node(node: Any) -> BrainNode:
    return BrainNode(
        id=getattr(node, "id"),
        slug=_text_value(getattr(node, "slug", "")),
        title=_text_value(getattr(node, "title", "")),
        summary_500=_text_value(getattr(node, "summary_500", "")),
        deep_link=deep_link(getattr(node, "id")),
        kind=_text_value(getattr(node, "kind", "wiki_entry")) or "wiki_entry",
        published_at=getattr(node, "published_at", None),
        last_verified_at=getattr(node, "last_verified_at", None),
    )


def _etag_seed(fmt: str, domain: Optional[str], topic: Optional[str], role: str, nodes: Iterable[Any]) -> str:
    material = {
        "format": fmt,
        "domain": domain,
        "topic": topic,
        "role": role,
        "nodes": [
            {
                "id": str(getattr(node, "id", "")),
                "slug": getattr(node, "slug", ""),
                "summary_500": getattr(node, "summary_500", ""),
                "status": getattr(node, "status", ""),
                "body_md_hash": getattr(node, "body_md_hash", ""),
                "published_at": _iso(getattr(node, "published_at", None)),
                "last_verified_at": _iso(getattr(node, "last_verified_at", None)),
                "updated_at": _iso(getattr(node, "updated_at", None)),
            }
            for node in nodes
        ],
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]
    return f'W/"{digest}"'


def _brain_payload(
    *, domain: Optional[str], fmt: str, etag: str, nodes: list[Any]
) -> BrainResponse:
    brain_nodes = [_to_brain_node(node) for node in nodes]
    return BrainResponse(
        domain=domain,
        format=fmt,
        etag=etag,
        last_updated=_last_updated(nodes),
        node_count=len(brain_nodes),
        nodes=brain_nodes,
    )


def _render_plain(payload: BrainResponse) -> str:
    lines = [
        "Seed Brain",
        f"Domain: {payload.domain or 'all-visible'}",
        f"ETag: {payload.etag}",
        f"Last updated: {payload.last_updated.isoformat() if payload.last_updated else 'unknown'}",
        f"Node count: {payload.node_count}",
        "",
    ]
    for node in payload.nodes:
        lines.extend(
            [
                f"## {node.title}",
                f"Slug: {node.slug}",
                f"Kind: {node.kind}",
                f"Published: {node.published_at.isoformat() if node.published_at else 'unknown'}",
                f"Verified: {node.last_verified_at.isoformat() if node.last_verified_at else 'unknown'}",
                f"Link: {node.deep_link}",
                node.summary_500.strip() or "No summary.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_skill(payload: BrainResponse) -> str:
    lines = [
        f"# Seed Brain Skill — {payload.domain or 'all-visible'}",
        "",
        "## Purpose",
        "Use this skill as a compact, verified knowledge index for the visible Seed domain set.",
        "",
        "## Retrieval Contract",
        f"- Format: {payload.format}",
        f"- ETag: {payload.etag}",
        f"- Last updated: {payload.last_updated.isoformat() if payload.last_updated else 'unknown'}",
        f"- Node count: {payload.node_count}",
        "",
        "## Nodes",
    ]
    if not payload.nodes:
        lines.append("No published nodes matched this request.")
    for node in payload.nodes:
        lines.extend(
            [
                "",
                f"### {node.title}",
                f"- ID: `{node.id}`",
                f"- Slug: `{node.slug}`",
                f"- Kind: `{node.kind}`",
                f"- Deep link: {node.deep_link}",
                f"- Published: {node.published_at.isoformat() if node.published_at else 'unknown'}",
                f"- Verified: {node.last_verified_at.isoformat() if node.last_verified_at else 'unknown'}",
                "",
                node.summary_500.strip() or "No summary.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def _render_markdown(payload: BrainResponse) -> str:
    lines = [
        "---",
        f"domain: {_yaml_scalar(payload.domain)}",
        f"format: {_yaml_scalar(payload.format)}",
        f"etag: {_yaml_scalar(payload.etag)}",
        f"last_updated: {_yaml_scalar(payload.last_updated.isoformat() if payload.last_updated else None)}",
        f"node_count: {payload.node_count}",
        "---",
        "",
        f"# Seed Brain — {payload.domain or 'all-visible'}",
        "",
    ]
    for node in payload.nodes:
        lines.extend(
            [
                f"## {node.title}",
                "",
                f"- **ID:** `{node.id}`",
                f"- **Slug:** `{node.slug}`",
                f"- **Kind:** `{node.kind}`",
                f"- **Deep link:** {node.deep_link}",
                f"- **Published:** {node.published_at.isoformat() if node.published_at else 'unknown'}",
                f"- **Verified:** {node.last_verified_at.isoformat() if node.last_verified_at else 'unknown'}",
                "",
                node.summary_500.strip() or "No summary.",
                "",
            ]
        )
    if not payload.nodes:
        lines.append("No published nodes matched this request.\n")
    return "\n".join(lines).rstrip() + "\n"


def render_brain(payload: BrainResponse, fmt: str) -> Response:
    headers = {"ETag": payload.etag}
    if payload.last_updated:
        headers["Last-Modified"] = payload.last_updated.isoformat()
    if fmt == "json":
        return JSONResponse(content=jsonable_encoder(payload), headers=headers)
    if fmt == "skill":
        return PlainTextResponse(_render_skill(payload), media_type="text/markdown; charset=utf-8", headers=headers)
    if fmt == "markdown":
        return PlainTextResponse(_render_markdown(payload), media_type="text/markdown; charset=utf-8", headers=headers)
    return PlainTextResponse(_render_plain(payload), media_type="text/plain; charset=utf-8", headers=headers)


@router.get("")
async def get_brain(
    domain: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    format_: Optional[str] = Query(None, alias="format"),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    db: Any = Depends(get_db),
    api_key: Optional[SeedApiKey] = Depends(get_api_key),
) -> Response:
    if api_key is not None and not has_permission(api_key, "read"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission: read")
    fmt = resolve_format(format_, api_key)
    nodes = await _load_nodes(db, api_key, domain, topic)
    etag = _etag_seed(fmt, domain, topic, role_for_key(api_key), nodes)

    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    payload = _brain_payload(domain=domain, fmt=fmt, etag=etag, nodes=nodes)
    return render_brain(payload, fmt)
