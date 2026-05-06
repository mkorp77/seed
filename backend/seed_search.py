"""Book 4 Postgres full-text search endpoint."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.sql import literal

from seed_auth import db_execute, deep_link, get_api_key, get_db, has_permission, visible_domains_for_key
from seed_models import SeedApiKey
from seed_schemas_book4 import SearchResponse, SearchResult

try:  # Prefer the repo's canonical model if present.
    from seed_models import SeedKnowledgeNode  # type: ignore
except Exception:  # pragma: no cover
    try:
        from models import SeedKnowledgeNode  # type: ignore
    except Exception:  # pragma: no cover
        from seed_models_book4_delta import SeedKnowledgeNode

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


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


def _search_vector() -> Any:
    return func.to_tsvector(
        "english",
        func.concat_ws(
            " ",
            func.coalesce(_node_col("title"), literal("")),
            func.coalesce(_node_col("summary_500"), literal("")),
            func.coalesce(_node_col("body_md"), literal("")),
        ),
    )


def _to_search_result(node: Any, score: Any) -> SearchResult:
    try:
        relevance_score = float(score or 0.0)
    except Exception:
        relevance_score = 0.0
    return SearchResult(
        id=getattr(node, "id"),
        slug=str(getattr(node, "slug", "")),
        title=str(getattr(node, "title", "")),
        summary_500=str(getattr(node, "summary_500", "") or ""),
        relevance_score=relevance_score,
        domain=str(getattr(node, "domain", "seed") or "seed"),
        deep_link=deep_link(getattr(node, "id")),
    )


@router.get("", response_model=SearchResponse)
async def search_nodes(
    q: str = Query(..., min_length=1),
    domain: Optional[str] = Query(None),
    db: Any = Depends(get_db),
    api_key: Optional[SeedApiKey] = Depends(get_api_key),
) -> SearchResponse:
    if api_key is not None and not has_permission(api_key, "read"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing permission: read")

    vector = _search_vector()
    tsquery = func.plainto_tsquery("english", q)
    score = func.ts_rank_cd(vector, tsquery).label("relevance_score")

    stmt = select(SeedKnowledgeNode, score).where(_published_filter()).where(vector.op("@@")(tsquery))
    stmt = _domain_filter(stmt, visible_domains_for_key(api_key), domain)
    stmt = stmt.order_by(desc(score), _node_col("title").asc()).limit(50)

    result = await db_execute(db, stmt)
    rows = list(result.all())
    if not rows:
        logger.info("seed_search.no_results", extra={"query": q, "domain": domain})
        return SearchResponse(query=q, domain=domain, results=[], degraded=False)

    return SearchResponse(
        query=q,
        domain=domain,
        results=[_to_search_result(node, row_score) for node, row_score in rows],
        degraded=False,
    )
