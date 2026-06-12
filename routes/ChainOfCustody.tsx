import { useState, useEffect, useMemo, type ComponentType } from "react";
import { useParams } from "react-router-dom";
import { useCaseProvenance } from "../features/cases/api";
import { CaseWorkspaceLayout } from "../components/CaseWorkspaceLayout";
import { StatusChip } from "../components/StatusChip";
import { EmptyState, DetailSkeleton } from "../components/Feedback";
import { CopyableHash } from "../components/EvidencePanel";
import {
  Clock,
  FileText,
  GitPullRequestArrow,
  Link2,
  Shield,
  XCircle,
  ArrowRight,
} from "lucide-react";
import type { ProvenanceResponse, ConflictSummary } from "../types/api";

// ── Types ──────────────────────────────────────────────────────────────────────

type EntryType = "claim" | "conflict" | "projection" | "claim_history";
type FilterKey = "all" | EntryType;

interface TimelineEntry {
  id: string;
  ts: string;
  type: EntryType;
  label: string;
  sublabel: string;
  status?: string;
  raw: Record<string, unknown>;
}

// ── Timeline builder ───────────────────────────────────────────────────────────

function buildTimeline(data: ProvenanceResponse): TimelineEntry[] {
  const entries: TimelineEntry[] = [];

  for (let i = 0; i < data.claims.length; i++) {
    const c = data.claims[i];
    if (!c) continue;
    const fv = c.fieldValue;
    const valStr =
      fv == null ? "—" : typeof fv === "object" ? JSON.stringify(fv) : String(fv);
    entries.push({
      id: `claim-${i}`,
      ts: String(c.createdAt ?? ""),
      type: "claim",
      label: String(c.fieldName ?? "").replace(/_/g, " ") || "claim",
      sublabel: valStr,
      status: String(c.claimType ?? "RAW"),
      raw: c,
    });
  }

  for (const c of data.conflicts) {
    entries.push({
      id: `conflict-open-${c.id}`,
      ts: c.createdAt,
      type: "conflict",
      label: `${c.fieldName.replace(/_/g, " ")} — opened`,
      sublabel: `${c.claimIds.length} claim${c.claimIds.length !== 1 ? "s" : ""} competing`,
      status: "OPEN",
      raw: c as unknown as Record<string, unknown>,
    });
    if (c.resolvedAt) {
      entries.push({
        id: `conflict-resolved-${c.id}`,
        ts: c.resolvedAt,
        type: "conflict",
        label: `${c.fieldName.replace(/_/g, " ")} — resolved`,
        sublabel: c.resolvedBy ? `by ${c.resolvedBy.slice(0, 8)}…` : "by system",
        status: "RESOLVED",
        raw: c as unknown as Record<string, unknown>,
      });
    }
  }

  for (let i = 0; i < data.projectionHistory.length; i++) {
    const ph = data.projectionHistory[i];
    if (!ph) continue;
    entries.push({
      id: `projection-${i}`,
      ts: ph.createdAt,
      type: "projection",
      label: `Projection v${ph.version}`,
      sublabel: ph.reason || "Automatic rebuild",
      raw: ph as unknown as Record<string, unknown>,
    });
  }

  for (let i = 0; i < data.claimHistories.length; i++) {
    const h = data.claimHistories[i];
    if (!h) continue;
    const action = String(h.action ?? "change");
    const toType = String(h.toClaimType ?? h.to_claim_type ?? "");
    entries.push({
      id: `history-${i}`,
      ts: String(h.createdAt ?? h.created_at ?? ""),
      type: "claim_history",
      label: `Claim ${action}`,
      sublabel: toType,
      status: toType || undefined,
      raw: h,
    });
  }

  entries.sort((a, b) => {
    if (!a.ts && !b.ts) return 0;
    if (!a.ts) return 1;
    if (!b.ts) return -1;
    return a.ts.localeCompare(b.ts);
  });

  return entries;
}

// ── Visual config ─────────────────────────────────────────────────────────────

const TYPE_CONFIG: Record<
  EntryType,
  { icon: ComponentType<{ className?: string }>; dotCls: string; label: string }
> = {
  claim: {
    icon: FileText,
    dotCls: "text-blue-600 bg-blue-50 ring-1 ring-blue-100",
    label: "Claim",
  },
  conflict: {
    icon: GitPullRequestArrow,
    dotCls: "text-amber-600 bg-amber-50 ring-1 ring-amber-100",
    label: "Conflict",
  },
  projection: {
    icon: Clock,
    dotCls: "text-purple-600 bg-purple-50 ring-1 ring-purple-100",
    label: "Projection",
  },
  claim_history: {
    icon: Link2,
    dotCls: "text-green-600 bg-green-50 ring-1 ring-green-100",
    label: "History",
  },
};

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "claim", label: "Claims" },
  { key: "conflict", label: "Conflicts" },
  { key: "projection", label: "Projections" },
  { key: "claim_history", label: "History" },
];

// ── Left panel: filter bar ─────────────────────────────────────────────────────

function FilterBar({
  active,
  counts,
  onSelect,
}: {
  active: FilterKey;
  counts: Record<FilterKey, number>;
  onSelect: (k: FilterKey) => void;
}) {
  return (
    <div className="px-3 py-2 border-b border-atlas-200 bg-atlas-50/50 flex items-center gap-1 flex-wrap">
      {FILTERS.map((f) => (
        <button
          key={f.key}
          onClick={() => onSelect(f.key)}
          className={`flex items-center gap-1 px-2 py-1 text-2xs font-medium rounded transition-colors ${
            active === f.key
              ? "bg-atlas-200 text-atlas-900"
              : "text-atlas-500 hover:text-atlas-700 hover:bg-atlas-100"
          }`}
        >
          {f.label}
          <span className="text-atlas-400">({counts[f.key]})</span>
        </button>
      ))}
    </div>
  );
}

// ── Entry row ──────────────────────────────────────────────────────────────────

function EntryRow({
  entry,
  isSelected,
  isLast,
  onClick,
}: {
  entry: TimelineEntry;
  isSelected: boolean;
  isLast: boolean;
  onClick: () => void;
}) {
  const cfg = TYPE_CONFIG[entry.type];
  const Icon = cfg.icon;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onClick()}
      className={`relative px-4 pt-3 pb-6 cursor-pointer select-none transition-colors ${
        isSelected ? "bg-atlas-100/60" : "hover:bg-atlas-50/50"
      }`}
    >
      {/* Vertical connector: from below this dot to the bottom of the row.
          The next row's pt-3 provides the visual gap to its dot. */}
      {!isLast && (
        <div className="absolute left-8 top-[44px] bottom-0 w-px bg-atlas-200 pointer-events-none" />
      )}

      <div className="flex gap-3">
        {/* Icon dot */}
        <div
          className={`relative z-10 w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${cfg.dotCls}`}
        >
          <Icon className="w-3.5 h-3.5" />
        </div>

        {/* Text */}
        <div className="flex-1 min-w-0 pt-0.5">
          <div className="flex items-start gap-1.5">
            <span
              className={`text-xs font-medium flex-1 min-w-0 leading-snug ${
                isSelected ? "text-atlas-900" : "text-atlas-800"
              }`}
            >
              {entry.label}
            </span>
            {entry.status && <StatusChip status={entry.status} />}
          </div>
          <p className="text-2xs text-atlas-500 truncate mt-0.5">{entry.sublabel}</p>
          <p className="text-2xs text-atlas-400 mt-0.5 tabular-nums">
            {entry.ts ? new Date(entry.ts).toLocaleString() : "—"}
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Detail panel ───────────────────────────────────────────────────────────────

function ClaimDetail({ raw }: { raw: Record<string, unknown> }) {
  const createdAt = raw.createdAt == null ? "" : String(raw.createdAt);
  const supersededByClaimId =
    raw.supersededByClaimId == null ? "" : String(raw.supersededByClaimId);
  const field = (key: string) =>
    raw[key] != null ? (
      <div key={key}>
        <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
          {key.replace(/([A-Z])/g, " $1").trim()}
        </p>
        <p className="text-xs text-atlas-800 mt-0.5 font-mono break-all">
          {typeof raw[key] === "object" ? JSON.stringify(raw[key]) : String(raw[key])}
        </p>
      </div>
    ) : null;

  return (
    <div className="p-5 space-y-3">
      <h3 className="text-sm font-semibold text-atlas-900 mb-4">Claim</h3>
      <div className="space-y-3">
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Field</p>
          <p className="text-xs text-atlas-800 mt-0.5">
            {String(raw.fieldName ?? "").replace(/_/g, " ") || "—"}
          </p>
        </div>
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Value</p>
          <p className="text-xs text-atlas-800 mt-0.5 font-mono break-all">
            {raw.fieldValue == null
              ? "—"
              : typeof raw.fieldValue === "object"
              ? JSON.stringify(raw.fieldValue)
              : String(raw.fieldValue)}
          </p>
        </div>
        {field("claimType")}
        {field("sourceId")}
        {field("rawSnapshotId")}
        {field("createdBy")}
        {createdAt && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Created at</p>
            <p className="text-xs text-atlas-800 mt-0.5">
              {new Date(createdAt).toLocaleString()}
            </p>
          </div>
        )}
        {supersededByClaimId && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Superseded by</p>
            <p className="text-xs font-mono text-atlas-700 mt-0.5">
              {supersededByClaimId.slice(0, 8)}…
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function ConflictDetail({ raw }: { raw: Record<string, unknown> }) {
  const c = raw as unknown as ConflictSummary;
  return (
    <div className="p-5 space-y-3">
      <h3 className="text-sm font-semibold text-atlas-900">
        {c.fieldName?.replace(/_/g, " ") ?? "Conflict"}
      </h3>
      <div className="flex items-center gap-2">
        <StatusChip status={c.status} />
        <span className="text-2xs text-atlas-500">v{c.version}</span>
      </div>
      <div className="space-y-2 pt-1">
        {c.lastModifiedReason && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Reason</p>
            <p className="text-xs text-atlas-600 italic mt-0.5">"{c.lastModifiedReason}"</p>
          </div>
        )}
        {c.lastModifiedNote && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Note</p>
            <p className="text-xs text-atlas-600 mt-0.5">{c.lastModifiedNote}</p>
          </div>
        )}
        <div className="border-t border-atlas-100 pt-2 space-y-1.5">
          <div className="flex justify-between text-2xs">
            <span className="text-atlas-500">Claims</span>
            <span className="text-atlas-700">{c.claimIds?.length ?? 0}</span>
          </div>
          {c.winningClaimId && (
            <div className="flex justify-between text-2xs">
              <span className="text-atlas-500">Winning claim</span>
              <span className="font-mono text-atlas-700">{c.winningClaimId.slice(0, 8)}…</span>
            </div>
          )}
          <div className="flex justify-between text-2xs">
            <span className="text-atlas-500">Opened</span>
            <span className="text-atlas-700">{new Date(c.createdAt).toLocaleString()}</span>
          </div>
          {c.resolvedAt && (
            <div className="flex justify-between text-2xs">
              <span className="text-atlas-500">Resolved</span>
              <span className="text-atlas-700">{new Date(c.resolvedAt).toLocaleString()}</span>
            </div>
          )}
          {c.resolvedBy && (
            <div className="flex justify-between text-2xs">
              <span className="text-atlas-500">Resolved by</span>
              <span className="font-mono text-atlas-700">{c.resolvedBy.slice(0, 8)}…</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ProjectionDetail({ raw }: { raw: Record<string, unknown> }) {
  const version = Number(raw.version ?? 0);
  const reason = String(raw.reason ?? "");
  const createdAt = String(raw.createdAt ?? "");
  return (
    <div className="p-5 space-y-3">
      <h3 className="text-sm font-semibold text-atlas-900">Projection v{version}</h3>
      <div className="space-y-3">
        {createdAt && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Built at</p>
            <p className="text-xs text-atlas-800 mt-0.5">{new Date(createdAt).toLocaleString()}</p>
          </div>
        )}
        {reason && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Reason</p>
            <p className="text-xs text-atlas-600 italic mt-0.5">"{reason}"</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ClaimHistoryDetail({ raw }: { raw: Record<string, unknown> }) {
  const action = String(raw.action ?? "");
  const fromType = raw.fromClaimType ?? raw.from_claim_type;
  const fromTypeText = fromType == null ? "" : String(fromType);
  const toType = String(raw.toClaimType ?? raw.to_claim_type ?? "");
  const reason = String(raw.reason ?? "");
  const modifierType = String(raw.modifierType ?? raw.modifier_type ?? "SYSTEM");
  const modifierId = raw.modifierId == null && raw.modifier_id == null
    ? ""
    : String(raw.modifierId ?? raw.modifier_id);
  const createdAt = String(raw.createdAt ?? raw.created_at ?? "");
  const claimId = raw.claimId == null && raw.claim_id == null
    ? ""
    : String(raw.claimId ?? raw.claim_id);
  const rowHash = raw.rowHash == null && raw.row_hash == null
    ? ""
    : String(raw.rowHash ?? raw.row_hash);
  const prevHash = raw.prevHash == null && raw.prev_hash == null
    ? ""
    : String(raw.prevHash ?? raw.prev_hash);

  return (
    <div className="p-5 space-y-4">
      <h3 className="text-sm font-semibold text-atlas-900 capitalize">
        Claim {action || "state change"}
      </h3>

      {/* State transition */}
      <div className="flex items-center gap-2 flex-wrap">
        {fromTypeText && (
          <>
            <StatusChip status={fromTypeText} />
            <ArrowRight className="w-3 h-3 text-atlas-400 flex-shrink-0" />
          </>
        )}
        {toType && <StatusChip status={toType} />}
      </div>

      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Modifier</p>
            <p className="text-xs text-atlas-800 mt-0.5">{modifierType}</p>
          </div>
          {createdAt && (
            <div>
              <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">When</p>
              <p className="text-xs text-atlas-800 mt-0.5">{new Date(createdAt).toLocaleString()}</p>
            </div>
          )}
        </div>
        {reason && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">Reason</p>
            <p className="text-xs text-atlas-600 italic mt-0.5">"{reason}"</p>
          </div>
        )}
        <div className="border-t border-atlas-100 pt-2 space-y-1.5">
          {claimId && (
            <div className="flex justify-between text-2xs">
              <span className="text-atlas-500">Claim</span>
              <span className="font-mono text-atlas-600">{claimId.slice(0, 8)}…</span>
            </div>
          )}
          {modifierId && (
            <div className="flex justify-between text-2xs">
              <span className="text-atlas-500">By</span>
              <span className="font-mono text-atlas-600">{modifierId.slice(0, 8)}…</span>
            </div>
          )}
        </div>
      </div>

      {/* HMAC audit chain */}
      {(rowHash || prevHash) && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Shield className="w-3.5 h-3.5 text-atlas-400 flex-shrink-0" />
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
              Audit chain
            </p>
          </div>
          {prevHash && (
            <div>
              <p className="text-2xs text-atlas-500 mb-1">Previous hash</p>
              <CopyableHash value={prevHash} />
            </div>
          )}
          {rowHash && (
            <div>
              <p className="text-2xs text-atlas-500 mb-1">This entry</p>
              <CopyableHash value={rowHash} />
            </div>
          )}
          <p className="text-2xs text-atlas-400 leading-relaxed">
            Each entry's row_hash = HMAC-SHA256 over this row. The next entry's prev_hash must
            equal this row_hash for the chain to be intact.
          </p>
        </div>
      )}
    </div>
  );
}

function EntryDetail({ entry }: { entry: TimelineEntry | null }) {
  if (!entry) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-atlas-500">
        Select an event to view details
        <span className="ml-2 text-2xs text-atlas-400">(j / k to navigate)</span>
      </div>
    );
  }
  switch (entry.type) {
    case "claim":
      return <ClaimDetail raw={entry.raw} />;
    case "conflict":
      return <ConflictDetail raw={entry.raw} />;
    case "projection":
      return <ProjectionDetail raw={entry.raw} />;
    case "claim_history":
      return <ClaimHistoryDetail raw={entry.raw} />;
  }
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ChainOfCustody() {
  const { slug } = useParams<{ slug: string }>();
  const { data, isLoading, error } = useCaseProvenance(slug);

  const [filter, setFilter] = useState<FilterKey>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const allEntries = useMemo(() => (data ? buildTimeline(data) : []), [data]);

  const filtered = useMemo(
    () => (filter === "all" ? allEntries : allEntries.filter((e) => e.type === filter)),
    [allEntries, filter],
  );

  const selectedEntry = useMemo(
    () => filtered.find((e) => e.id === selectedId) ?? null,
    [filtered, selectedId],
  );

  const counts = useMemo<Record<FilterKey, number>>(
    () => ({
      all: allEntries.length,
      claim: allEntries.filter((e) => e.type === "claim").length,
      conflict: allEntries.filter((e) => e.type === "conflict").length,
      projection: allEntries.filter((e) => e.type === "projection").length,
      claim_history: allEntries.filter((e) => e.type === "claim_history").length,
    }),
    [allEntries],
  );

  // j/k keyboard navigation
  useEffect(() => {
    if (!filtered.length) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedId((prev) => {
          const idx = filtered.findIndex((en) => en.id === prev);
          return filtered[Math.min(idx + 1, filtered.length - 1)]?.id ?? prev;
        });
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedId((prev) => {
          const idx = filtered.findIndex((en) => en.id === prev);
          return filtered[Math.max(idx - 1, 0)]?.id ?? prev;
        });
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filtered]);

  // Clear selection when filter changes
  useEffect(() => {
    setSelectedId(null);
  }, [filter]);

  if (isLoading) {
    return (
      <CaseWorkspaceLayout
        left={
          <div className="animate-pulse px-4 pt-4 space-y-5">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex gap-3">
                <div className="w-8 h-8 rounded-full bg-atlas-100 shrink-0" />
                <div className="flex-1 space-y-1.5 pt-1">
                  <div className="h-3 bg-atlas-100 rounded w-3/4" />
                  <div className="h-2.5 bg-atlas-100 rounded w-1/2" />
                  <div className="h-2 bg-atlas-100 rounded w-1/3" />
                </div>
              </div>
            ))}
          </div>
        }
        center={<DetailSkeleton />}
      />
    );
  }

  if (error) {
    return (
      <CaseWorkspaceLayout
        left={
          <EmptyState
            icon={<XCircle className="w-6 h-6" />}
            title="Failed to load chain of custody"
            description="Could not fetch provenance data."
          />
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">—</div>
        }
      />
    );
  }

  if (!data || allEntries.length === 0) {
    return (
      <CaseWorkspaceLayout
        left={
          <EmptyState
            icon={<Clock className="w-6 h-6" strokeWidth={1.25} />}
            title="No evidence activity"
            description="Claims, conflicts, and projection versions appear here as evidence is ingested."
          />
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">—</div>
        }
      />
    );
  }

  const left = (
    <div>
      <FilterBar active={filter} counts={counts} onSelect={setFilter} />
      {filtered.length === 0 ? (
        <EmptyState title="No events match this filter" />
      ) : (
        <div className="pt-3">
          {filtered.map((entry, i) => (
            <EntryRow
              key={entry.id}
              entry={entry}
              isSelected={entry.id === selectedId}
              isLast={i === filtered.length - 1}
              onClick={() =>
                setSelectedId(entry.id === selectedId ? null : entry.id)
              }
            />
          ))}
        </div>
      )}
      {data.pagination.hasMore && (
        <p className="text-2xs text-disputed-600 bg-disputed-50 border border-disputed-200 rounded mx-4 mt-2 mb-4 px-3 py-2">
          More events available. Use the Provenance view for pagination.
        </p>
      )}
    </div>
  );

  return (
    <CaseWorkspaceLayout
      left={left}
      center={<EntryDetail entry={selectedEntry} />}
    />
  );
}
