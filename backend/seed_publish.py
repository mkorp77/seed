from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from seed_domain import DEFAULT_DOMAIN, detect_domain


VAULT_ROOT = Path(os.getenv("SEED_VAULT_PATH", "D:/Seed/vault"))


class ContextNotFoundError(Exception):
    pass


class VaultPublishError(Exception):
    pass


_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _safe_path_part(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not _SAFE_PATH_PART.fullmatch(value):
        raise VaultPublishError(f"Invalid {field_name}: {value!r}")
    return value


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read first matching attribute or dict key from ORM objects/dicts."""
    for name in names:
        if obj is None:
            continue
        if isinstance(obj, dict) and name in obj:
            value = obj.get(name)
            if value is not None:
                return value
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            dt = _utc_now()
    else:
        dt = _utc_now()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(title: str, max_chars: int = 50) -> str:
    base = (title or "untitled-context").strip()[:max_chars].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return slug or "untitled-context"


def _frontmatter_scalar(value: Any) -> str:
    if value is None:
        return '""'
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def _frontmatter_list(values: list[str]) -> str:
    return "[" + ", ".join(str(v).replace(",", "").strip() for v in values if str(v).strip()) + "]"


def _source_host(url: str) -> str:
    try:
        return urlparse(url).netloc or "source"
    except Exception:
        return "source"


def _load_context_and_metadata(db: Session, context_id: UUID | str) -> tuple[Any, Any]:
    """
    Assumes the Book 1 ORM exposes SeedContext and SeedContextMetadata.

    If the repo used different class names, change only these imports/queries.
    The rest of Book 2 does not depend on exact column layout.
    """
    from seed_models import SeedContext, SeedContextMetadata  # local import avoids import cycles

    context = db.query(SeedContext).filter(SeedContext.id == context_id).first()
    if context is None:
        raise ContextNotFoundError(str(context_id))

    metadata = (
        db.query(SeedContextMetadata)
        .filter(SeedContextMetadata.context_id == context_id)
        .first()
    )

    return context, metadata


def _extract_payload(context: Any, metadata: Any) -> dict[str, Any]:
    source = _as_dict(_get(context, "source", default={}))
    content = _as_dict(_get(context, "content", default={}))
    meta_obj = _get(context, "context_metadata", "metadata_record", default=None)

    # Prefer metadata table tags; fall back to context/content source shapes.
    tags = (
        _as_list(_get(metadata, "tags", default=None))
        or _as_list(_get(meta_obj, "tags", default=None))
        or _as_list(_get(context, "tags", default=None))
        or _as_list(_get(content, "tags", default=None))
    )

    title = (
        _get(metadata, "title", default=None)
        or _get(context, "title", default=None)
        or _get(context, "source_title", default=None)
        or _get(source, "title", "source_title", default=None)
        or _get(content, "title", default=None)
        or "Untitled Context"
    )

    source_url = (
        _get(context, "source_url", default=None)
        or _get(context, "source_uri", default=None)
        or _get(source, "url", "source_url", default=None)
        or ""
    )

    selected_text = (
        _get(context, "selected_text", "text", default=None)
        or _get(content, "selected_text", "text", "body", "content", default=None)
        or ""
    )

    captured_at = _to_datetime(
        _get(context, "captured_at", default=None)
        or _get(source, "captured_at", default=None)
        or _get(context, "created_at", default=None)
    )

    content_hash = (
        _get(context, "content_hash", default=None)
        or _get(content, "content_hash", "hash", default=None)
        or ""
    )

    capture_method = (
        _get(context, "capture_method", default=None)
        or _get(source, "capture_method", "kind", default=None)
        or "chrome_extension"
    )

    return {
        "context_id": str(_get(context, "id")),
        "title": str(title),
        "source_url": str(source_url),
        "selected_text": str(selected_text),
        "captured_at": captured_at,
        "tags": tags,
        "content_hash": str(content_hash),
        "capture_method": str(capture_method),
    }


def build_markdown(payload: dict[str, Any], domain: str) -> str:
    title = payload["title"]
    source_url = payload["source_url"]
    captured_at = payload["captured_at"]
    captured_iso = _iso_z(captured_at)
    captured_human = captured_at.strftime("%B %-d, %Y") if os.name != "nt" else captured_at.strftime("%B %#d, %Y")

    return f"""---
source_url: {_frontmatter_scalar(source_url)}
title: {_frontmatter_scalar(title)}
captured_at: {captured_iso}
tags: {_frontmatter_list(payload["tags"])}
domain: {domain}
content_hash: {_frontmatter_scalar(payload["content_hash"])}
seed_context_id: {payload["context_id"]}
capture_method: {payload["capture_method"]}
---

# {title}

{payload["selected_text"]}

---
*Source: [{_source_host(source_url)}]({source_url})*
*Captured: {captured_human}*
"""


def _unique_path(target_dir: Path, filename: str) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    i = 2
    while True:
        next_candidate = target_dir / f"{stem}-{i}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        i += 1


def publish_context_to_vault(
    db: Session,
    context_id: UUID | str,
    *,
    domain: str | None = None,
    subdirectory: str = "raw",
) -> dict[str, Any]:
    context, metadata = _load_context_and_metadata(db, context_id)
    payload = _extract_payload(context, metadata)

    resolved_domain = _safe_path_part(domain or detect_domain(payload["tags"]).domain or DEFAULT_DOMAIN, "domain")
    safe_subdir = _safe_path_part(subdirectory or "raw", "subdirectory")

    captured_date = payload["captured_at"].date().isoformat()
    filename = f"{captured_date}-{slugify(payload['title'])}.md"

    vault_root = Path(os.getenv("SEED_VAULT_PATH", str(VAULT_ROOT)))
    target_dir = vault_root / resolved_domain / safe_subdir

    # Book 2 brief says directory structure is set up by Code. Fail loudly if missing.
    if not target_dir.exists() or not target_dir.is_dir():
        raise VaultPublishError(f"Vault target directory does not exist: {target_dir}")

    target_path = _unique_path(target_dir, filename)
    markdown = build_markdown(payload, resolved_domain)

    try:
        target_path.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        raise VaultPublishError(str(exc)) from exc

    file_size = target_path.stat().st_size
    rel_path = target_path.relative_to(vault_root).as_posix()

    return {
        "context_id": payload["context_id"],
        "domain": resolved_domain,
        "subdirectory": safe_subdir,
        "file_path": rel_path,
        "file_size": file_size,
        "published_at": _iso_z(_utc_now()),
    }
