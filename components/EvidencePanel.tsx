/**
 * EvidencePanel — the shared evidence surface used across Claims, Conflicts,
 * Timeline, Provenance, and Reports.
 *
 * Design goals:
 *   - Every fact visibly traceable to its source without breaking workflow
 *   - Audit hash copyable and prominently placed
 *   - Epistemic discipline: evidence-support confidence, never causal probability
 *   - Works from three data modes:
 *       1. PublicEvidenceClaims  — from /evidence endpoint (no IDs; read-only)
 *       2. ConflictCandidates    — from /candidates endpoint (has IDs; resolve form)
 *       3. ProvenanceClaims      — from /provenance endpoint (full chain)
 *
 * Layout (top → bottom):
 *   Source header    title + kind + reliability tier
 *   Values block     raw value vs projected/winning value
 *   Metadata         ingestion timestamp + claim type lifecycle state
 *   Claim history    created/superseded/confirmed entries (when available)
 *   Linked conflicts chips that cross-link (when available)
 *   Audit hash       monospace + copy button (when available)
 */

import { useState, useCallback } from "react";
import {
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  Link2,
  Clock,
  GitPullRequestArrow,
  FileText,
  Shield,
} from "lucide-react";
import { StatusChip, ReliabilityChip } from "./StatusChip";
import type {
  EvidenceClaimItem,
  EvidenceSourceItem,
  ConflictClaimCandidate,
  ConflictSummary,
} from "../types/api";

// ── Shared types ─────────────────────────────────────────────────────────────

/** A source-backed claim in the simplest possible shape, usable from any mode. */
export interface NormalisedClaim {
  /** Stable claim ID — present only for authenticated candidates/provenance claims. */
  claimId?: string;
  fieldName: string;
  fieldValue: unknown;
  claimType: string;
  sourceName: string;
  sourceKind?: string;
  sourceReliabilityTier: number;
  isWinning: boolean;
  isSuperseded: boolean;
  createdAt: string;
  supersededByClaimId?: string | null;
  /** Claim history entries, available from provenance data. */
  history?: ClaimHistoryEntry[];
  /** Linked conflict IDs, available from provenance data. */
  linkedConflictIds?: string[];
  /** Audit hash, available from provenance data. */
  auditHash?: string;
}

export interface ClaimHistoryEntry {
  action: string;
  reason: string;
  toClaimType: string;
  fromClaimType?: string | null;
  createdAt: string;
}

/** Convert a public EvidenceClaimItem to a NormalisedClaim. */
export function fromPublicClaim(c: EvidenceClaimItem): NormalisedClaim {
  return {
    fieldName: c.fieldName,
    fieldValue: c.fieldValue,
    claimType: c.claimType,
    sourceName: c.sourceName,
    sourceKind: c.sourceKind,
    sourceReliabilityTier: c.sourceReliabilityTier,
    isWinning: c.isWinning,
    isSuperseded: c.isSuperseded,
    createdAt: c.createdAt,
  };
}

/** Convert a ConflictClaimCandidate to a NormalisedClaim. */
export function fromCandidate(c: ConflictClaimCandidate): NormalisedClaim {
  return {
    claimId: c.claimId,
    fieldName: c.fieldName,
    fieldValue: c.fieldValue,
    claimType: c.claimType,
    sourceName: c.sourceName,
    sourceReliabilityTier: c.sourceReliabilityTier,
    isWinning: c.isWinning,
    isSuperseded: c.isSuperseded,
    createdAt: c.createdAt,
    supersededByClaimId: c.supersededByClaimId,
  };
}

/** Convert a raw provenance claim dict to a NormalisedClaim. */
export function fromProvenanceClaim(c: Record<string, unknown>): NormalisedClaim {
  const sourceName =
    String(c.sourceName ?? c.source_name ?? "Unknown source");
  const fieldValue = c.fieldValue ?? c.field_value;
  const claimType = String(c.claimType ?? c.claim_type ?? "RAW");
  const createdAt = String(c.createdAt ?? c.created_at ?? "");

  return {
    claimId: c.id ? String(c.id) : undefined,
    fieldName: String(c.fieldName ?? c.field_name ?? ""),
    fieldValue,
    claimType,
    sourceName,
    sourceReliabilityTier: Number(c.sourceReliabilityTier ?? c.source_reliability_tier ?? 3),
    isWinning: Boolean(c.isWinning ?? c.is_winning ?? false),
    isSuperseded: claimType === "SUPERSEDED",
    createdAt,
    supersededByClaimId: c.supersededByClaimId
      ? String(c.supersededByClaimId)
      : null,
  };
}

// ── Sub-components ────────────────────────────────────────────────────────────

/** Copyable monospace value (for hashes, IDs). */
export function CopyableHash({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(() => {
    void navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [value]);

  return (
    <div className="flex items-center gap-2 bg-atlas-50 border border-atlas-200 rounded px-3 py-2">
      <span className="font-mono text-2xs text-atlas-700 flex-1 break-all leading-relaxed">
        {value}
      </span>
      <button
        onClick={copy}
        className="flex-shrink-0 text-atlas-400 hover:text-atlas-700 transition-colors focus-ring rounded p-0.5"
        title="Copy to clipboard"
      >
        {copied ? (
          <Check className="w-3.5 h-3.5 text-resolved-500" />
        ) : (
          <Copy className="w-3.5 h-3.5" />
        )}
      </button>
    </div>
  );
}

/** Section header with optional toggle for collapsible sections. */
function SectionHeader({
  icon,
  label,
  count,
  open,
  onToggle,
}: {
  icon: React.ReactNode;
  label: string;
  count?: number;
  open?: boolean;
  onToggle?: () => void;
}) {
  const content = (
    <div className="flex items-center gap-2">
      <span className="text-atlas-400">{icon}</span>
      <span className="text-2xs font-semibold text-atlas-600 uppercase tracking-wider flex-1">
        {label}
        {count !== undefined && (
          <span className="ml-1 font-normal text-atlas-400">({count})</span>
        )}
      </span>
      {onToggle && (
        <span className="text-atlas-400">
          {open ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5" />
          )}
        </span>
      )}
    </div>
  );

  if (onToggle) {
    return (
      <button
        onClick={onToggle}
        className="w-full text-left py-2 hover:bg-atlas-50/50 transition-colors rounded -mx-1 px-1 focus-ring"
      >
        {content}
      </button>
    );
  }

  return <div className="py-2">{content}</div>;
}

/** Claim lifecycle state badge — more descriptive than raw ClaimType. */
function LifecycleBadge({ claimType, isWinning }: { claimType: string; isWinning: boolean }) {
  if (isWinning && claimType !== "SUPERSEDED") {
    return (
      <span className="inline-flex items-center gap-1 chip chip-resolved">
        <CheckCircle2 className="w-3 h-3" />
        Active winner
      </span>
    );
  }
  return <StatusChip status={claimType} />;
}

// ── Main EvidencePanel ────────────────────────────────────────────────────────

interface EvidencePanelProps {
  /** The claim to display evidence for. Null shows the empty state. */
  claim: NormalisedClaim | null;
  /** Additional source items from the public evidence endpoint, for kind info. */
  sources?: EvidenceSourceItem[];
  /** All conflicts for the case, for linking from the panel. */
  conflicts?: ConflictSummary[];
  /** Called when the user clicks a linked conflict chip. */
  onConflictClick?: (conflictId: string) => void;
  /** Title override — defaults to the field name. */
  title?: string;
}

export function EvidencePanel({
  claim,
  sources = [],
  conflicts = [],
  onConflictClick,
  title,
}: EvidencePanelProps) {
  const [historyOpen, setHistoryOpen] = useState(false);

  if (!claim) {
    return (
      <div className="p-4 flex flex-col items-center justify-center min-h-[12rem] text-center">
        {title && (
          <p className="text-xs font-semibold text-atlas-700 mb-2">{title}</p>
        )}
        <Shield className="w-6 h-6 text-atlas-300 mb-2" strokeWidth={1.5} />
        <p className="text-xs text-atlas-500">
          Select a claim to view its evidence chain
        </p>
        <p className="text-2xs text-atlas-400 mt-1">
          Every fact is traceable to its source
        </p>
      </div>
    );
  }

  // Enrich source kind from the public sources list if available
  const sourceItem = sources.find((s) => s.name === claim.sourceName);
  const sourceKind = claim.sourceKind ?? sourceItem?.kind ?? null;

  // Linked conflicts: either explicit IDs on the claim, or infer from conflicts
  // by matching field name
  const linkedConflicts =
    claim.linkedConflictIds
      ? conflicts.filter((c) => claim.linkedConflictIds!.includes(c.id))
      : conflicts.filter(
          (c) => c.fieldName === claim.fieldName,
        );

  const hasHistory = (claim.history?.length ?? 0) > 0;

  return (
    <div className="p-4 space-y-4">
      {/* ── Header ── */}
      <div>
        <h3 className="text-xs font-semibold text-atlas-800 mb-0.5">
          {title ?? claim.fieldName.replace(/_/g, " ")}
        </h3>
        <p className="text-2xs text-atlas-500">Evidence chain</p>
      </div>

      {/* ── Source ── */}
      <div className="border border-atlas-200 rounded p-3 space-y-2">
        <SectionHeader
          icon={<FileText className="w-3.5 h-3.5" />}
          label="Source"
        />
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-atlas-800 truncate">
              {claim.sourceName}
            </p>
            {sourceKind && (
              <p className="text-2xs text-atlas-500 mt-0.5">{sourceKind}</p>
            )}
          </div>
          <ReliabilityChip tier={claim.sourceReliabilityTier} />
        </div>
      </div>

      {/* ── Value ── */}
      <div className="border border-atlas-200 rounded p-3 space-y-2">
        <SectionHeader
          icon={<FileText className="w-3.5 h-3.5" />}
          label="Value"
        />
        <div>
          <p className="text-sm font-medium text-atlas-900 break-words">
            {claim.fieldValue != null ? String(claim.fieldValue) : (
              <span className="text-atlas-400 italic">null</span>
            )}
          </p>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <LifecycleBadge
              claimType={claim.claimType}
              isWinning={claim.isWinning}
            />
            {claim.isSuperseded && claim.supersededByClaimId && (
              <span className="text-2xs text-superseded-500">
                superseded by {claim.supersededByClaimId.slice(0, 8)}…
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ── Metadata ── */}
      <div className="border border-atlas-200 rounded p-3 space-y-2">
        <SectionHeader
          icon={<Clock className="w-3.5 h-3.5" />}
          label="Metadata"
        />
        <div className="space-y-1.5">
          <div className="flex justify-between text-2xs">
            <span className="text-atlas-500">Ingested</span>
            <span className="text-atlas-700 font-medium">
              {claim.createdAt
                ? new Date(claim.createdAt).toLocaleString()
                : "—"}
            </span>
          </div>
          {claim.claimId && (
            <div className="flex justify-between text-2xs gap-2">
              <span className="text-atlas-500 flex-shrink-0">Claim ID</span>
              <span className="font-mono text-atlas-600 text-right break-all">
                {claim.claimId.slice(0, 8)}…
              </span>
            </div>
          )}
          <div className="flex justify-between text-2xs">
            <span className="text-atlas-500">Evidence support</span>
            <span className="text-atlas-700 font-medium">
              Tier {claim.sourceReliabilityTier}
            </span>
          </div>
        </div>
      </div>

      {/* ── Linked conflicts ── */}
      {linkedConflicts.length > 0 && (
        <div className="border border-disputed-200 rounded p-3 space-y-2 bg-disputed-50/30">
          <SectionHeader
            icon={<GitPullRequestArrow className="w-3.5 h-3.5" />}
            label="Linked conflicts"
            count={linkedConflicts.length}
          />
          <div className="flex flex-wrap gap-1.5">
            {linkedConflicts.map((c) => (
              <button
                key={c.id}
                onClick={() => onConflictClick?.(c.id)}
                className={`inline-flex items-center gap-1 px-2 py-1 text-2xs font-medium rounded border transition-colors focus-ring ${
                  c.status === "OPEN"
                    ? "chip-disputed hover:bg-disputed-200"
                    : "chip-resolved hover:bg-resolved-200"
                } ${onConflictClick ? "cursor-pointer" : "cursor-default"}`}
              >
                {c.status === "OPEN" ? (
                  <AlertTriangle className="w-2.5 h-2.5" />
                ) : (
                  <CheckCircle2 className="w-2.5 h-2.5" />
                )}
                {c.fieldName.replace(/_/g, " ")}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Claim history (collapsible) ── */}
      {hasHistory && (
        <div className="border border-atlas-200 rounded p-3">
          <SectionHeader
            icon={<Link2 className="w-3.5 h-3.5" />}
            label="Claim history"
            count={claim.history!.length}
            open={historyOpen}
            onToggle={() => setHistoryOpen((v) => !v)}
          />
          {historyOpen && (
            <div className="mt-2 space-y-2">
              {claim.history!.map((entry, i) => (
                <div
                  key={i}
                  className="flex gap-3 py-1.5 border-t border-atlas-100 first:border-t-0"
                >
                  <div className="flex-shrink-0 mt-0.5">
                    {entry.toClaimType === "SUPERSEDED" ? (
                      <AlertTriangle className="w-3 h-3 text-superseded-400" />
                    ) : (
                      <CheckCircle2 className="w-3 h-3 text-resolved-500" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      {entry.fromClaimType && (
                        <>
                          <StatusChip status={entry.fromClaimType} />
                          <span className="text-atlas-400 text-2xs">→</span>
                        </>
                      )}
                      <StatusChip status={entry.toClaimType} />
                    </div>
                    {entry.reason && (
                      <p className="text-2xs text-atlas-600 mt-0.5 italic">
                        "{entry.reason}"
                      </p>
                    )}
                    <p className="text-2xs text-atlas-400 mt-0.5">
                      {new Date(entry.createdAt).toLocaleString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Audit hash ── */}
      {claim.auditHash && (
        <div className="border border-atlas-200 rounded p-3 space-y-2">
          <SectionHeader
            icon={<Shield className="w-3.5 h-3.5" />}
            label="Audit hash"
          />
          <CopyableHash value={claim.auditHash} />
          <p className="text-2xs text-atlas-400">
            SHA-256 of the raw payload at ingestion. Copy to verify independently.
          </p>
        </div>
      )}

      {/* ── Epistemic disclaimer ── */}
      <p className="text-2xs text-atlas-400 italic leading-relaxed">
        Evidence-support confidence only — not causal probability or legal conclusion.
      </p>
    </div>
  );
}

// ── Multi-claim panel (for field-level view in Claims screen) ─────────────────

interface MultiClaimPanelProps {
  fieldName: string | null;
  claims: NormalisedClaim[];
  sources?: EvidenceSourceItem[];
  conflicts?: ConflictSummary[];
  onConflictClick?: (conflictId: string) => void;
}

/**
 * Shows all claims for a field grouped by source.
 * Used in the Claims screen right panel when no single claim is selected.
 */
export function MultiClaimPanel({
  fieldName,
  claims,
  sources = [],
  conflicts = [],
  onConflictClick,
}: MultiClaimPanelProps) {
  const [selectedClaimId, setSelectedClaimId] = useState<string | number | null>(null);

  if (!fieldName) {
    return (
      <div className="p-4 flex flex-col items-center justify-center min-h-[12rem] text-center">
        <Shield className="w-6 h-6 text-atlas-300 mb-2" strokeWidth={1.5} />
        <p className="text-xs text-atlas-500">Select a field to view evidence</p>
      </div>
    );
  }

  const fieldClaims = claims.filter((c) => c.fieldName === fieldName);

  if (fieldClaims.length === 0) {
    return (
      <div className="p-4">
        <p className="text-xs text-atlas-500 text-center py-8">
          No evidence claims for this field
        </p>
      </div>
    );
  }

  // If a specific claim is selected, show its full EvidencePanel
  const selectedClaim = selectedClaimId != null
    ? fieldClaims.find((c, i) => (c.claimId ?? i) === selectedClaimId)
    : null;

  if (selectedClaim) {
    return (
      <div>
        <button
          onClick={() => setSelectedClaimId(null)}
          className="flex items-center gap-1.5 px-4 py-2.5 w-full text-left text-2xs text-atlas-500 hover:text-atlas-700 border-b border-atlas-100 transition-colors"
        >
          <ChevronRight className="w-3 h-3 rotate-180" />
          All claims for {fieldName.replace(/_/g, " ")}
        </button>
        <EvidencePanel
          claim={selectedClaim}
          sources={sources}
          conflicts={conflicts}
          onConflictClick={onConflictClick}
          title={fieldName.replace(/_/g, " ")}
        />
      </div>
    );
  }

  // List all claims for the field, grouped by source
  const bySource = new Map<string, NormalisedClaim[]>();
  for (const c of fieldClaims) {
    const list = bySource.get(c.sourceName) ?? [];
    list.push(c);
    bySource.set(c.sourceName, list);
  }

  return (
    <div className="p-4 space-y-3">
      <div>
        <h3 className="text-xs font-semibold text-atlas-800">
          {fieldName.replace(/_/g, " ")}
        </h3>
        <p className="text-2xs text-atlas-500 mt-0.5">
          {fieldClaims.length} claim{fieldClaims.length !== 1 ? "s" : ""} ·{" "}
          {bySource.size} source{bySource.size !== 1 ? "s" : ""}
        </p>
      </div>

      {[...bySource.entries()].map(([sourceName, sc]) => {
        const first = sc[0]!;
        return (
          <div
            key={sourceName}
            className="border border-atlas-200 rounded overflow-hidden"
          >
            {/* Source header */}
            <div className="flex items-center gap-2 px-3 py-2 bg-atlas-50/60 border-b border-atlas-100">
              <span className="text-xs font-medium text-atlas-800 flex-1 truncate">
                {sourceName}
              </span>
              <ReliabilityChip tier={first.sourceReliabilityTier} />
            </div>

            {/* Claims from this source */}
            {sc.map((c, i) => (
              <button
                key={c.claimId ?? i}
                onClick={() => setSelectedClaimId(c.claimId ?? i)}
                className="w-full text-left px-3 py-2 border-t border-atlas-100 first:border-t-0 hover:bg-atlas-50/50 transition-colors group"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm text-atlas-800 flex-1 truncate">
                    {c.fieldValue != null ? String(c.fieldValue) : (
                      <span className="text-atlas-400 italic">null</span>
                    )}
                  </span>
                  <ChevronRight className="w-3 h-3 text-atlas-300 group-hover:text-atlas-500 flex-shrink-0" />
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <LifecycleBadge claimType={c.claimType} isWinning={c.isWinning} />
                  <span className="text-2xs text-atlas-400">
                    {new Date(c.createdAt).toLocaleDateString()}
                  </span>
                </div>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}
