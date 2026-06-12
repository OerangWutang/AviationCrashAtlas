from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator


def _json_nesting_depth(value: Any, current: int = 0) -> int:
    """Compute max nesting depth of a JSON-compatible value."""
    if isinstance(value, dict):
        if not value:
            return current + 1
        return max(_json_nesting_depth(v, current + 1) for v in value.values())
    if isinstance(value, list):
        if not value:
            return current + 1
        return max(_json_nesting_depth(v, current + 1) for v in value)
    return current


# Maximum serialised size of a single claim field_value (50 KB).
_MAX_FIELD_VALUE_BYTES = 50 * 1024
# Maximum nesting depth of a claim field_value.
_MAX_FIELD_VALUE_DEPTH = 20


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    role: str
    key_id: UUID | None = None


@dataclass(frozen=True)
class CurrentTenantUser:
    """An authenticated caller acting inside a tenant scope.

    Constructed by ``get_current_tenant_user`` when the API key
    carries a non-null ``tenant_id`` + ``tenant_role`` pair AND a
    matching active ``TenantMembership`` row exists.  Carries both
    the system identity (``user_id``, ``role``) and the tenant
    identity (``tenant_id``, ``tenant_role``).

    The system ``role`` still governs public-side reads — a tenant
    caller acting as analyst can read public events the same way any
    other analyst can.  The tenant binding is purely additive.
    """

    user_id: UUID
    role: str
    tenant_id: UUID
    tenant_role: str


@dataclass(frozen=True)
class IngestionResult:
    event_id: UUID
    event_created: bool
    snapshot_created: bool
    idempotent_replay: bool = False
    # Backward-compatible primary review handle. When an ambiguous identity
    # match fans out to multiple candidate events, this is the first review id
    # and ``pending_review_ids`` contains the full set.
    pending_review_id: UUID | None = None
    pending_review_ids: tuple[UUID, ...] = ()
    attached_by: str = ""

    def __post_init__(self) -> None:
        """Keep legacy ``pending_review_id`` and new plural ids consistent."""
        ids = tuple(dict.fromkeys(self.pending_review_ids))
        primary = self.pending_review_id
        if primary is None and ids:
            primary = ids[0]
        if primary is not None:
            ids = (primary, *tuple(review_id for review_id in ids if review_id != primary))
        object.__setattr__(self, "pending_review_id", primary)
        object.__setattr__(self, "pending_review_ids", ids)


class IngestionClaimDTO(BaseModel):
    """Application-layer DTO for a single normalized/source claim.

    This validation intentionally lives below the HTTP layer so non-HTTP
    callers (CLI, tests, workers, future imports) cannot bypass the invariant
    that every claim has a meaningful field name.
    """

    field_name: str
    field_value: Any = None

    @field_validator("field_name")
    @classmethod
    def field_name_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field_name must not be blank")
        return value

    @field_validator("field_value")
    @classmethod
    def field_value_size_and_depth(cls, value: Any) -> Any:
        """Reject field values that are too large or too deeply nested.

        Large or deeply-nested JSON values can cause issues during:
        - projection serialisation (blowing the JSONB column or read buffer)
        - report generation (memory bloat rendering nested values)
        - API response serialisation (slow JSON encoding)
        - display in the frontend (deeply nested objects are not useful)
        """
        if value is None:
            return value

        # Check depth first (cheap, no serialisation)
        depth = _json_nesting_depth(value)
        if depth > _MAX_FIELD_VALUE_DEPTH:
            raise ValueError(
                f"field_value nesting depth {depth} exceeds maximum {_MAX_FIELD_VALUE_DEPTH}"
            )

        # Check serialised size
        serialised = json.dumps(value, default=str, separators=(",", ":"))
        size = len(serialised.encode())
        if size > _MAX_FIELD_VALUE_BYTES:
            raise ValueError(
                f"field_value serialised size {size} bytes exceeds maximum "
                f"{_MAX_FIELD_VALUE_BYTES} bytes"
            )
        return value


class ProjectionDTO(BaseModel):
    event_id: UUID
    projection_version: int
    fields: dict[str, Any]
    completeness_score: float
    unresolved_conflict_fields: list[str]
    updated_at: datetime


class ProjectionHistoryEntryDTO(BaseModel):
    id: UUID
    projection_version: int
    caused_by_conflict_id: UUID | None = None
    caused_by_ingestion_run_id: UUID | None = None
    changed_fields: list[str] | None = None
    created_at: datetime


class ConflictDTO(BaseModel):
    id: UUID
    event_id: UUID
    field_name: str
    status: str
    version: int
    winning_claim_id: UUID | None = None
    resolved_at: datetime | None = None
    resolved_by: UUID | None = None
    last_modified_reason: str | None = None
    last_modified_note: str | None = None
    claim_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


class ConflictHistoryEntryDTO(BaseModel):
    id: UUID
    conflict_id: UUID
    sequence: int
    from_status: str | None = None
    to_status: str
    modifier_type: str
    modifier_id: UUID | None = None
    reason: str | None = None
    version_at_moment: int
    claims_snapshot: dict[str, Any] | None = None
    created_at: datetime