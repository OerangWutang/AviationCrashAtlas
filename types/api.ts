/**
 * TypeScript types mirroring the actual Pydantic DTOs in the Atlas backend.
 *
 * The backend emits snake_case; the frontend API client camelCases them.
 * These types reflect the FRONTEND (camelCase) shape.
 *
 * Source of truth:
 *   - src/atlas/presentation/api/schemas/conflicts.py
 *   - src/atlas/presentation/api/schemas/public.py
 *   - src/atlas/presentation/api/schemas/provenance.py
 *   - src/atlas/presentation/api/schemas/auth.py
 *   - src/atlas/domain/enums.py
 */

// ── Enums ───────────────────────────────────────────────────────────────────

export type Role = "analyst" | "reviewer" | "admin";
export type ConflictStatus = "OPEN" | "RESOLVED";
export type ClaimType = "RAW" | "CONFIRMED" | "MANUAL_OVERRIDE" | "SUPERSEDED";
export type ModifierType = "USER" | "INGESTION" | "SYSTEM";

// ── Auth ────────────────────────────────────────────────────────────────────

export interface CurrentUser {
  userId: string;
  email: string;
  displayName: string;
  role: Role;
  tenantId: string | null;
  tenantRole: string | null;
  permissions: Record<string, boolean>;
}

// ── Cases / Public Events ───────────────────────────────────────────────────

export interface PublicEventSummary {
  slug: string;
  title: string;
  shortSummary: string | null;
  eventDate: string | null;
  location: string | null;
  operator: string | null;
  aircraftType: string | null;
  fatalitiesTotal: number | null;
  confidence: string;
  hasUnresolvedConflicts: boolean;
  lastPublishedAt: string | null;
}

export interface PublicEventListResponse {
  items: PublicEventSummary[];
  limit: number;
  nextCursor: string | null;
}

export interface PublicEventEditorial {
  title: string;
  shortSummary: string | null;
  narrativeMarkdown: string | null;
}

export interface PublicEventDetail {
  slug: string;
  canonicalEventId: string;
  editorial: PublicEventEditorial;
  fields: Record<string, unknown>;
  completenessScore: number;
  confidence: string;
  unresolvedConflictFields: string[];
  projectionVersion: number;
  firstPublishedAt: string | null;
  lastPublishedAt: string | null;
  lastUpdatedAt: string;
}

// ── Claims ──────────────────────────────────────────────────────────────────

export interface ClaimSummary {
  id: string;
  eventId: string;
  fieldName: string;
  fieldValue: unknown | null;
  sourceId: string;
  rawSnapshotId: string | null;
  claimType: ClaimType;
  createdAt: string;
  createdBy: string | null;
  supersededByClaimId: string | null;
}

// ── Conflicts ───────────────────────────────────────────────────────────────

export interface ConflictSummary {
  id: string;
  eventId: string;
  fieldName: string;
  status: ConflictStatus;
  version: number;
  lastModifiedReason: string;
  lastModifiedNote: string | null;
  winningClaimId: string | null;
  resolvedAt: string | null;
  resolvedBy: string | null;
  createdAt: string;
  updatedAt: string;
  claimIds: string[];
}

export interface ProjectionFields {
  eventId: string;
  projectionVersion: number;
  fields: Record<string, unknown>;
  completenessScore: number;
  unresolvedConflictFields: string[];
  updatedAt: string;
}

export interface ActivityLogEntry {
  id: string;
  conflictId: string;
  sequence: number;
  fromStatus: ConflictStatus | null;
  toStatus: ConflictStatus;
  modifierType: ModifierType;
  modifierId: string | null;
  reason: string;
  versionAtMoment: number;
  claimsSnapshot: Record<string, unknown> | null;
  createdAt: string;
}

export interface ConflictHistoryResponse {
  conflictId: string;
  archiveAvailable: boolean;
  transitions: ActivityLogEntry[];
  pagination: { limit: number; nextCursor: string | null } | null;
}

// ── Conflict claim candidates ───────────────────────────────────────────────
//
// Returned by GET /api/v1/conflicts/{id}/candidates.  Each candidate carries
// a stable `claimId` that the resolve form should submit verbatim as
// `winning_claim_id` — never an index into ConflictSummary.claimIds or any
// other separate array.  This is the contract that closes the
// claimIds-vs-evidence-order bug.

export interface ConflictClaimCandidate {
  claimId: string;
  fieldName: string;
  fieldValue: unknown | null;
  sourceId: string;
  sourceName: string;
  sourceReliabilityTier: number;
  claimType: ClaimType;
  createdAt: string;
  /** True iff conflict.winningClaimId === claimId. Always false on OPEN. */
  isWinning: boolean;
  /** True iff this claim's own claim_type is SUPERSEDED. */
  isSuperseded: boolean;
  supersededByClaimId: string | null;
}

export interface ConflictCandidatesResponse {
  conflictId: string;
  fieldName: string;
  candidates: ConflictClaimCandidate[];
}

// ── Resolve / Reopen ────────────────────────────────────────────────────────

export interface ResolveRequest {
  expected_version: number;
  winning_claim_id?: string;
  manual_override_value?: unknown;
  reason: string;
}

export interface ReopenRequest {
  expected_version: number;
  reason: string;
}

export interface ResolveResponse {
  conflict: ConflictSummary;
  accidentRecord: ProjectionFields;
}

/** The 409 body when a conflict was modified between load and submit. */
export interface ConflictModified409 {
  detail: string;
  modifierReason: string | null;
  currentVersion: number;
  latestActivity: ActivityLogEntry | null;
  conflict: ConflictSummary;
  accidentRecord: ProjectionFields;
}

// ── Provenance ──────────────────────────────────────────────────────────────

export interface ProvenanceResponse {
  eventId: string;
  absorbedEventId: string | null;
  canonicalized: boolean;
  projection: Record<string, unknown> | null;
  claims: Record<string, unknown>[];
  claimHistories: Record<string, unknown>[];
  conflicts: ConflictSummary[];
  conflictActivityLogs: Record<string, unknown>[];
  projectionHistory: { version: number; createdAt: string; reason: string }[];
  pagination: {
    limit: number;
    nextCursor: string | null;
    nextCursors: Record<string, string | null>;
    hasMore: boolean;
  };
  archiveAvailable: boolean;
}

// ── Timeline ────────────────────────────────────────────────────────────────

export interface TimelineEvent {
  eventType: string;
  occurredAt: string | null;
  timestampPrecision: string;
  sequenceIndex: number | null;
  description: string | null;
}

export interface TimelineResponse {
  slug: string;
  canonicalEventId: string;
  events: TimelineEvent[];
  truncated: boolean;
}

// ── Evidence (public) ───────────────────────────────────────────────────────

export interface EvidenceClaimItem {
  fieldName: string;
  fieldValue: unknown;
  claimType: string;
  sourceName: string;
  sourceKind: string;
  sourceReliabilityTier: number;
  isWinning: boolean;
  isSuperseded: boolean;
  createdAt: string;
}

export interface EvidenceSourceItem {
  name: string;
  kind: string;
  reliabilityTier: number;
}

export interface EvidenceResponse {
  slug: string;
  canonicalEventId: string;
  claims: EvidenceClaimItem[];
  sources: EvidenceSourceItem[];
  claimCount: number;
  truncated: boolean;
}

// ── Audit ───────────────────────────────────────────────────────────────────

export interface AuditFieldRow {
  fieldName: string;
  currentValue: unknown;
  isDisputed: boolean;
  isManuallyOverridden: boolean;
  confidence: string;
  plainEnglish: string;
}

export interface PageAuditResponse {
  slug: string;
  canonicalEventId: string;
  summary: string;
  confidence: string;
  confidenceMeaning: string;
  projectionVersion: number;
  lastUpdatedAt: string;
  fields: AuditFieldRow[];
}

export interface FieldExplanationWinner {
  fieldName: string;
  currentValue: unknown;
  plainEnglish: string;
  sourceName: string;
  sourceKind: string;
}

export interface FieldExplanationLoser {
  sourceName: string;
  sourceKind: string;
  reportedValue: unknown;
  plainEnglish: string;
}

export interface FieldExplanationConflict {
  status: string;
  plainEnglish: string;
  resolvedAt: string | null;
}

export interface FieldExplanationResponse {
  eventId: string;
  fieldName: string;
  hasWinner: boolean;
  winner: FieldExplanationWinner | null;
  losers: FieldExplanationLoser[];
  losersTruncated: boolean;
  conflict: FieldExplanationConflict | null;
}
