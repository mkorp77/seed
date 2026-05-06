"""Book 4 authentication middleware and admin API-key routes."""

from __future__ import annotations

import hmac
import inspect
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select

from seed_models import SeedApiKey
from seed_schemas_book4 import (
    FORMAT_VOCABULARY,
    PERMISSION_VOCABULARY,
    SeedApiKeyCreate,
    SeedApiKeyCreated,
    SeedApiKeyPublic,
    SeedApiKeyUpdate,
)

try:
    import bcrypt  # type: ignore
except Exception:  # pragma: no cover
    bcrypt = None  # type: ignore

from seed_deps import get_db


API_KEY_TOKEN_PREFIX = "seed_pk_"
DEFAULT_PUBLIC_DOMAINS = ["seed"]
DEFAULT_ROLE_FORMATS = {
    "public": ["plain", "json"],
    "reader": ["plain", "json", "markdown", "skill"],
    "model": ["plain", "json", "markdown", "skill"],
    "admin": ["plain", "json", "markdown", "skill"],
    "*": ["plain", "json", "markdown", "skill"],
}

router = APIRouter(prefix="/admin/keys", tags=["admin-keys"])


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def db_execute(db: Any, statement: Any, params: Optional[dict[str, Any]] = None) -> Any:
    if params is None:
        return await _maybe_await(db.execute(statement))
    return await _maybe_await(db.execute(statement, params))


async def db_commit(db: Any) -> None:
    await _maybe_await(db.commit())


async def db_rollback(db: Any) -> None:
    rollback = getattr(db, "rollback", None)
    if rollback:
        await _maybe_await(rollback())


async def db_refresh(db: Any, obj: Any) -> None:
    refresh = getattr(db, "refresh", None)
    if refresh:
        await _maybe_await(refresh(obj))


def db_add(db: Any, obj: Any) -> None:
    db.add(obj)


async def scalar_one_or_none(db: Any, statement: Any) -> Any:
    result = await db_execute(db, statement)
    return result.scalar_one_or_none()


async def scalars_all(db: Any, statement: Any) -> list[Any]:
    result = await db_execute(db, statement)
    return list(result.scalars().all())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _load_role_formats() -> dict[str, list[str]]:
    raw = os.getenv("SEED_ROLE_FORMATS_JSON")
    if not raw:
        return DEFAULT_ROLE_FORMATS
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("SEED_ROLE_FORMATS_JSON must be valid JSON") from exc
    merged = {**DEFAULT_ROLE_FORMATS}
    for role, formats in data.items():
        if not isinstance(role, str) or not isinstance(formats, list):
            raise RuntimeError("SEED_ROLE_FORMATS_JSON must map role strings to format arrays")
        normalized = [str(fmt) for fmt in formats]
        unknown = set(normalized) - FORMAT_VOCABULARY
        if unknown:
            raise RuntimeError(f"Unknown formats in SEED_ROLE_FORMATS_JSON: {sorted(unknown)}")
        merged[role] = normalized
    return merged


def _parse_csv_env(name: str, default: Sequence[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def validate_permissions(permissions: Iterable[str]) -> list[str]:
    values = [str(p) for p in permissions]
    unknown = set(values) - PERMISSION_VOCABULARY
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permissions: {sorted(unknown)}",
        )
    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="permissions cannot be empty")
    return values


def validate_format(fmt: str) -> str:
    if fmt not in FORMAT_VOCABULARY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"format must be one of {sorted(FORMAT_VOCABULARY)}",
        )
    return fmt


def hash_secret(secret: str) -> str:
    if bcrypt is not None:
        return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    try:  # pragma: no cover
        from passlib.hash import bcrypt as passlib_bcrypt  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Install bcrypt or passlib[bcrypt] to manage Seed API keys") from exc
    return passlib_bcrypt.hash(secret)


def verify_secret(secret: str, stored_hash: str) -> bool:
    try:
        if bcrypt is not None:
            return bool(bcrypt.checkpw(secret.encode("utf-8"), stored_hash.encode("utf-8")))
        from passlib.hash import bcrypt as passlib_bcrypt  # type: ignore  # pragma: no cover

        return bool(passlib_bcrypt.verify(secret, stored_hash))  # pragma: no cover
    except Exception:
        return False


def parse_seed_api_key(raw_token: str) -> Tuple[str, str]:
    if not raw_token.startswith(API_KEY_TOKEN_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key prefix")
    rest = raw_token[len(API_KEY_TOKEN_PREFIX) :]
    if "_" not in rest:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed API key")
    key_prefix, secret = rest.split("_", 1)
    if not key_prefix or not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed API key")
    return key_prefix, secret


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, sep, token = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expected Bearer token")
    return token.strip()


def api_key_is_current(api_key: SeedApiKey) -> bool:
    if not getattr(api_key, "active", False):
        return False
    if getattr(api_key, "revoked_at", None) is not None:
        return False
    expires_at = _aware(getattr(api_key, "expires_at", None))
    if expires_at is not None and expires_at <= utcnow():
        return False
    return True


async def get_api_key(
    authorization: Optional[str] = Header(None),
    db: Any = Depends(get_db),
) -> Optional[SeedApiKey]:
    """Parse Bearer token, lookup by prefix, verify secret against bcrypt hash."""

    token = bearer_token(authorization)
    if token is None:
        return None

    key_prefix, secret = parse_seed_api_key(token)
    api_key = await scalar_one_or_none(db, select(SeedApiKey).where(SeedApiKey.key_prefix == key_prefix))
    if api_key is None or not verify_secret(secret, api_key.key_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not api_key_is_current(api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key inactive, expired, or revoked")

    api_key.last_used_at = utcnow()
    try:
        await db_commit(db)
    except Exception:
        await db_rollback(db)
        # Auth should not fail only because telemetry failed.
    return api_key


def has_permission(api_key: Optional[SeedApiKey], permission: str) -> bool:
    if api_key is None:
        return False
    permissions: Set[str] = set(getattr(api_key, "permissions", []) or [])
    return "admin" in permissions or permission in permissions


def require_permission(permission: str) -> Callable[..., Any]:
    async def dependency(api_key: Optional[SeedApiKey] = Depends(get_api_key)) -> SeedApiKey:
        if api_key is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Authentication required")
        if not has_permission(api_key, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")
        return api_key

    return dependency


def public_domains() -> list[str]:
    return _parse_csv_env("SEED_PUBLIC_DOMAINS", DEFAULT_PUBLIC_DOMAINS)


def visible_domains_for_key(api_key: Optional[SeedApiKey]) -> list[str]:
    """
    Domain allowlist for this request.

    INFERRED: an authenticated key with domains=[] sees no private domains. Use ["*"]
    for a service key that should see all domains. Unauthenticated callers see only
    SEED_PUBLIC_DOMAINS, defaulting to seed.
    """

    if api_key is None:
        return public_domains()
    domains = list(getattr(api_key, "domains", []) or [])
    return domains


def role_for_key(api_key: Optional[SeedApiKey]) -> str:
    return getattr(api_key, "role", None) or "public"


def default_format_for_key(api_key: Optional[SeedApiKey]) -> str:
    if api_key is None:
        return "plain"
    return validate_format(getattr(api_key, "format", "plain") or "plain")


def allowed_formats_for_key(api_key: Optional[SeedApiKey]) -> list[str]:
    role = role_for_key(api_key)
    formats_by_role = _load_role_formats()
    allowed = list(formats_by_role.get(role, formats_by_role.get("*", ["plain"])))
    default_fmt = default_format_for_key(api_key)
    if default_fmt not in allowed:
        allowed.append(default_fmt)
    return allowed


def resolve_format(requested_format: Optional[str], api_key: Optional[SeedApiKey]) -> str:
    fmt = requested_format or default_format_for_key(api_key)
    validate_format(fmt)
    if fmt not in allowed_formats_for_key(api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Format not allowed for role: {fmt}")
    return fmt


def api_base_url() -> str:
    return os.getenv("SEED_API_BASE_URL", "https://api.seed.wiki").rstrip("/")


def deep_link(node_id: Any) -> str:
    return f"{api_base_url()}/api/nodes/{node_id}"


def to_public_api_key(api_key: SeedApiKey) -> SeedApiKeyPublic:
    data = {
        "id": api_key.id,
        "name": api_key.name,
        "key_prefix": api_key.key_prefix,
        "role": api_key.role,
        "domains": list(api_key.domains or []),
        "project_ids": list(api_key.project_ids or []) if api_key.project_ids is not None else None,
        "permissions": list(api_key.permissions or []),
        "format": api_key.format,
        "active": api_key.active,
        "created_at": api_key.created_at,
        "last_used_at": api_key.last_used_at,
        "expires_at": api_key.expires_at,
        "revoked_at": api_key.revoked_at,
        "notes": api_key.notes,
    }
    if hasattr(SeedApiKeyPublic, "model_validate"):
        return SeedApiKeyPublic.model_validate(data)  # type: ignore[attr-defined]
    return SeedApiKeyPublic.parse_obj(data)  # type: ignore[attr-defined]


async def _require_seed_admin_key(authorization: Optional[str] = Header(None)) -> None:
    expected = os.getenv("SEED_ADMIN_KEY")
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SEED_ADMIN_KEY is not configured")
    token = bearer_token(authorization)
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin token")


@router.post("", response_model=SeedApiKeyCreated, dependencies=[Depends(_require_seed_admin_key)])
async def create_admin_api_key(payload: SeedApiKeyCreate, db: Any = Depends(get_db)) -> SeedApiKeyCreated:
    validate_permissions(payload.permissions)
    validate_format(payload.format)

    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    raw_key = f"{API_KEY_TOKEN_PREFIX}{prefix}_{secret}"

    row = SeedApiKey(
        name=payload.name,
        key_prefix=prefix,
        key_hash=hash_secret(secret),
        role=payload.role,
        domains=payload.domains,
        project_ids=payload.project_ids,
        permissions=payload.permissions,
        format=payload.format,
        active=True,
        expires_at=payload.expires_at,
        notes=payload.notes,
    )
    db_add(db, row)
    try:
        await db_commit(db)
        await db_refresh(db, row)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create API key") from exc

    return SeedApiKeyCreated(key=raw_key, record=to_public_api_key(row))


@router.get("", response_model=List[SeedApiKeyPublic], dependencies=[Depends(_require_seed_admin_key)])
async def list_admin_api_keys(db: Any = Depends(get_db)) -> list[SeedApiKeyPublic]:
    rows = await scalars_all(db, select(SeedApiKey).order_by(SeedApiKey.created_at.desc()))
    return [to_public_api_key(row) for row in rows]


@router.patch("/{key_id}", response_model=SeedApiKeyPublic, dependencies=[Depends(_require_seed_admin_key)])
async def update_admin_api_key(key_id: UUID, payload: SeedApiKeyUpdate, db: Any = Depends(get_db)) -> SeedApiKeyPublic:
    row = await scalar_one_or_none(db, select(SeedApiKey).where(SeedApiKey.id == key_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    data = payload.dict(exclude_unset=True) if hasattr(payload, "dict") else payload.model_dump(exclude_unset=True)
    if "permissions" in data and data["permissions"] is not None:
        data["permissions"] = validate_permissions(data["permissions"])
    if "format" in data and data["format"] is not None:
        data["format"] = validate_format(data["format"])

    allowed_fields = {"name", "domains", "project_ids", "permissions", "format", "active", "expires_at", "notes"}
    for field, value in data.items():
        if field in allowed_fields:
            setattr(row, field, value)

    try:
        await db_commit(db)
        await db_refresh(db, row)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update API key") from exc
    return to_public_api_key(row)


@router.delete("/{key_id}", response_model=SeedApiKeyPublic, dependencies=[Depends(_require_seed_admin_key)])
async def revoke_admin_api_key(key_id: UUID, db: Any = Depends(get_db)) -> SeedApiKeyPublic:
    row = await scalar_one_or_none(db, select(SeedApiKey).where(SeedApiKey.id == key_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    row.revoked_at = utcnow()
    row.active = False
    try:
        await db_commit(db)
        await db_refresh(db, row)
    except Exception as exc:
        await db_rollback(db)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke API key") from exc
    return to_public_api_key(row)
