"""
API key authentication for write endpoints.

Design
------
Raw keys are never stored.  The SHA-256 hex digest is stored in the
api_keys table; the raw key is shown to the operator exactly once at
creation time (via the atlas keygen CLI command or direct DB seeding).

Authentication is opt-in via settings.api_auth_enabled.  When disabled
(the default for local dev / tests), reviewer endpoints are accepted and the
operator identity falls back to the request-body resolved_by field so
existing call patterns keep working without a key.  Admin-only endpoints are
never allowed while auth is disabled; they require an explicit admin API key.

Roles
-----
  reviewer — may resolve conflicts
  admin    — superset of reviewer; reserved for future admin operations

Dependencies
------------
  get_operator(required=False)  — returns OperatorContext | None
  require_reviewer               — 401/403 if no valid reviewer-or-above key
  require_admin                  — 401/403 if no valid admin key; rejects auth-disabled mode

Usage in routes
---------------
  # Optional — present only when auth is enabled and a valid key was sent.
  op = Depends(get_operator())

  # Mandatory — 401 if no key, 403 if insufficient role.
  op = Depends(require_reviewer)

  # Admin-only — never falls back to unauthenticated local/dev mode.
  op = Depends(require_admin)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.config import get_settings
from atlas.db.engine import get_auth_db
from atlas.models.orm import ApiKey

log = structlog.get_logger(__name__)
settings = get_settings()

_key_header = APIKeyHeader(
    name=settings.api_key_header,
    auto_error=False,         # we handle missing keys ourselves
    description="Reviewer API key.  Required when API_AUTH_ENABLED=true.",
)


def _request_settings(request: Request):
    """Return the settings object bound to this FastAPI app instance."""
    return getattr(request.app.state, "settings", settings)


@dataclass(frozen=True, slots=True)
class OperatorContext:
    """Authenticated operator identity, injected by the auth dependency."""
    id: str            # operator_id from the ApiKey row
    role: str          # "reviewer" | "admin"
    key_id: str        # ApiKey.id (for audit / last_used update)


def _hash_key(raw_key: str) -> str:
    """Stable, constant-time-comparable SHA-256 hex digest of a raw key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def _record_key_use(
    db: AsyncSession,
    *,
    key_id: str,
    used_at: datetime,
) -> None:
    """Persist ApiKey.last_used_at in the dedicated auth session."""
    await db.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id)
        .values(last_used_at=used_at)
    )
    await db.commit()


async def _resolve_key(
    raw_key: str | None,
    db: AsyncSession,
) -> OperatorContext | None:
    """
    Look up a raw API key and return the operator context.

    Returns None when:
    - raw_key is absent or empty
    - no matching active key is found in the database

    Does not raise for absent, invalid, inactive, or expired keys — callers
    decide whether a missing operator is an error. Database write failures while
    persisting last_used_at are intentionally allowed to surface.
    """
    if not raw_key:
        return None

    key_hash = _hash_key(raw_key)
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
        )
    )
    api_key: ApiKey | None = result.scalar_one_or_none()
    if api_key is None:
        log.warning("auth.invalid_key", key_hash_prefix=key_hash[:8])
        return None

    # Expiry check — None means never expires.
    if api_key.expires_at is not None and api_key.expires_at < datetime.now(tz=UTC):
        log.warning(
            "auth.expired_key",
            key_hash_prefix=key_hash[:8],
            operator_id=api_key.operator_id,
            expired_at=api_key.expires_at.isoformat(),
        )
        return None

    # Persist audit metadata before returning the operator context.  Auth uses
    # a dedicated session, so this commit cannot accidentally persist unrelated
    # read-route ORM changes.
    operator_id = api_key.operator_id
    role = api_key.role
    key_id = api_key.id
    await _record_key_use(db, key_id=key_id, used_at=datetime.now(tz=UTC))

    return OperatorContext(
        id=operator_id,
        role=role,
        key_id=key_id,
    )


async def get_operator(
    request: Request,
    raw_key: Annotated[str | None, Security(_key_header)] = None,
    db: AsyncSession = Depends(get_auth_db),
) -> OperatorContext | None:
    """
    FastAPI dependency — returns the authenticated operator or None.

    When api_auth_enabled is False (local dev / tests), always returns None
    without hitting the database.  Callers that need a non-None operator
    should use require_reviewer instead.
    """
    current_settings = _request_settings(request)
    if not current_settings.api_auth_enabled:
        return None
    return await _resolve_key(raw_key, db)


async def require_reviewer(
    request: Request,
    raw_key: Annotated[str | None, Security(_key_header)] = None,
    db: AsyncSession = Depends(get_auth_db),
) -> OperatorContext:
    """
    FastAPI dependency — enforces reviewer-or-above authentication.

    When api_auth_enabled is False, returns a synthetic operator context
    so the endpoint can still derive resolved_by from request body without
    breaking the unauthenticated flow.

    When api_auth_enabled is True:
      - 401 if no key is provided
      - 401 if the key is invalid or inactive
      - 403 if the key's role is not reviewer or admin
    """
    current_settings = _request_settings(request)
    if not current_settings.api_auth_enabled:
        # Auth disabled: return a sentinel context; endpoint uses body.resolved_by.
        return OperatorContext(id="", role="reviewer", key_id="")

    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.  Pass your reviewer API key in the "
                   f"'{current_settings.api_key_header}' header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    op = await _resolve_key(raw_key, db)
    if op is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if op.role not in ("reviewer", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient role: '{op.role}'. "
                   "Conflict resolution requires 'reviewer' or 'admin'.",
        )

    log.info("auth.reviewer_authorized", operator_id=op.id, role=op.role)
    return op


async def require_admin(
    request: Request,
    raw_key: Annotated[str | None, Security(_key_header)] = None,
    db: AsyncSession = Depends(get_auth_db),
) -> OperatorContext:
    """
    FastAPI dependency — enforces admin-only authentication.

    Unlike require_reviewer, this dependency deliberately does NOT return a
    sentinel operator when API_AUTH_ENABLED=false.  Admin overrides mutate
    accepted claims and projection state, so allowing them in unauthenticated
    local/dev mode is too easy to accidentally expose in production.

    Behaviour:
      - 403 if auth is disabled
      - 401 if no key is provided
      - 401 if the key is invalid, inactive, or expired
      - 403 if the key's role is not exactly admin
    """
    current_settings = _request_settings(request)
    if not current_settings.api_auth_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Admin endpoints require API_AUTH_ENABLED=true and a valid "
                "admin API key. Auth-disabled local/dev mode may not call "
                "admin override operations."
            ),
        )

    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.  Pass your admin API key in the "
                   f"'{current_settings.api_key_header}' header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    op = await _resolve_key(raw_key, db)
    if op is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if op.role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient role: {op.role!r}. Admin operation requires 'admin'.",
        )

    if not op.id:
        raise HTTPException(
            status_code=403,
            detail="Admin API key is missing an operator_id and cannot be audited.",
        )

    log.info("auth.admin_authorized", operator_id=op.id, role=op.role)
    return op
