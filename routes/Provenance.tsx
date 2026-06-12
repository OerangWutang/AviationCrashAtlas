import { useState, useEffect, useMemo } from "react";
import { useParams } from "react-router-dom";
import { useCaseProvenance } from "../features/cases/api";
import { CaseWorkspaceLayout } from "../components/CaseWorkspaceLayout";
import { StatusChip } from "../components/StatusChip";
import { EmptyState, TableSkeleton, DetailSkeleton } from "../components/Feedback";
import {
  EvidencePanel,
  CopyableHash,
  fromProvenanceClaim,
  type ClaimHistoryEntry,
} from "../components/EvidencePanel";
import {
  Link2,
  GitPullRequestArrow,
  FileText,
  Clock,
  Shield,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import type { ProvenanceResponse, ConflictSummary } from "../types/api";

// ── Types ──────────────────────────────────────────────────────────────────────

type ProvenanceSection =
  | "claims"
  | "conflicts"
  | "projection_history"
  | "claim_histories";

const SECTIONS: ProvenanceSection[] = [
  "claims",
  "conflicts",
  "projection_history",
  "claim_histories",
];

const SECTION_LABELS: Record<ProvenanceSection, string> = {
  claims: "Claims",
  conflicts: "Conflicts",
  projection_history: "Projection history",
  claim_histories: "Claim history",
};

const SECTION_ICONS: Record<ProvenanceSection, React.ReactNode> = {
  claims: <FileText className="w-3.5 h-3.5" />,
  conflicts: <GitPullRequestArrow className="w-3.5 h-3.5" />,
  projection_history: <Clock className="w-3.5 h-3.5" />,
  claim_histories: <Link2 className="w-3.5 h-3.5" />,
};

// ── SectionNav ─────────────────────────────────────────────────────────────────

function SectionNav({
  active,
  onSelect,
  counts,
}: {
  active: ProvenanceSection;
  onSelect: (s: ProvenanceSection) => void;
  counts: Record<ProvenanceSection, number>;
}) {
  return (
    <div className="px-3 py-2 border-b border-atlas-200 bg-atlas-50/50">
      <div className="flex items-center gap-1 flex-wrap">
        {SECTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onSelect(s)}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 text-2xs font-medium rounded transition-colors ${
              active === s
                ? "bg-atlas-200 text-atlas-900"
                : "text-atlas-500 hover:text-atlas-700 hover:bg-atlas-100"
            }`}
          >
            {SECTION_ICONS[s]}
            {SECTION_LABELS[s]}
            <span className="text-atlas-400">({counts[s]})</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── List views ─────────────────────────────────────────────────────────────────

function ClaimsList({
  claims,
  selectedIdx,
  onSelect,
}: {
  claims: Record<string, unknown>[];
  selectedIdx: number | null;
  onSelect: (idx: number) => void;
}) {
  return (
    <div>
      {claims.map((claim, idx) => {
        const fieldName = String(claim.fieldName ?? claim.field_name ?? "—");
        const fieldValue = String(claim.fieldValue ?? claim.field_value ?? "—");
        const claimType = String(claim.claimType ?? claim.claim_type ?? "RAW");

        return (
          <button
            key={idx}
            onClick={() => onSelect(idx)}
            className={`w-full text-left px-4 py-2.5 border-b border-atlas-100 transition-colors border-l-2 ${
              selectedIdx === idx
                ? "bg-atlas-100/80 border-l-atlas-500"
                : "hover:bg-atlas-50/50 border-l-transparent"
            }`}
          >
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-xs font-medium text-atlas-800 flex-1 truncate">
                {fieldName.replace(/_/g, " ")}
              </span>
              <StatusChip status={claimType} />
            </div>
            <p className="text-2xs text-atlas-600 truncate">{fieldValue}</p>
          </button>
        );
      })}
    </div>
  );
}

function ConflictsList({
  conflicts,
  selectedIdx,
  onSelect,
}: {
  conflicts: ConflictSummary[];
  selectedIdx: number | null;
  onSelect: (idx: number) => void;
}) {
  return (
    <div>
      {conflicts.map((c, idx) => (
        <button
          key={c.id}
          onClick={() => onSelect(idx)}
          className={`w-full text-left px-4 py-2.5 border-b border-atlas-100 transition-colors border-l-2 ${
            selectedIdx === idx
              ? "bg-atlas-100/80 border-l-atlas-500"
              : "hover:bg-atlas-50/50 border-l-transparent"
          }`}
        >
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-medium text-atlas-800 flex-1 truncate">
              {c.fieldName.replace(/_/g, " ")}
            </span>
            <StatusChip status={c.status} />
          </div>
          <div className="flex items-center gap-2 text-2xs text-atlas-500">
            <span>{c.claimIds.length} claims</span>
            <span>·</span>
            <span>v{c.version}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

function ProjectionHistoryList({
  history,
  selectedIdx,
  onSelect,
}: {
  history: { version: number; createdAt: string; reason: string }[];
  selectedIdx: number | null;
  onSelect: (idx: number) => void;
}) {
  return (
    <div>
      {history.map((entry, idx) => (
        <button
          key={idx}
          onClick={() => onSelect(idx)}
          className={`w-full text-left px-4 py-2.5 border-b border-atlas-100 transition-colors border-l-2 ${
            selectedIdx === idx
              ? "bg-atlas-100/80 border-l-atlas-500"
              : "hover:bg-atlas-50/50 border-l-transparent"
          }`}
        >
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-medium text-atlas-800">
              Version {entry.version}
            </span>
          </div>
          <p className="text-2xs text-atlas-500 truncate">
            {entry.createdAt ? new Date(entry.createdAt).toLocaleString() : "—"}
            {entry.reason && ` · ${entry.reason}`}
          </p>
        </button>
      ))}
    </div>
  );
}

function ClaimHistoryList({
  histories,
  selectedIdx,
  onSelect,
}: {
  histories: Record<string, unknown>[];
  selectedIdx: number | null;
  onSelect: (idx: number) => void;
}) {
  return (
    <div>
      {histories.map((h, idx) => {
        const action = String(h.action ?? "");
        const fromType = h.from_claim_type ? String(h.from_claim_type) : null;
        const toType = String(h.to_claim_type ?? "");
        const createdAt = String(h.created_at ?? "");
        const modifierType = String(h.modifier_type ?? "");

        return (
          <button
            key={idx}
            onClick={() => onSelect(idx)}
            className={`w-full text-left px-4 py-2.5 border-b border-atlas-100 transition-colors border-l-2 ${
              selectedIdx === idx
                ? "bg-atlas-100/80 border-l-atlas-500"
                : "hover:bg-atlas-50/50 border-l-transparent"
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              {action === "superseded" ? (
                <AlertTriangle className="w-3 h-3 text-superseded-400 flex-shrink-0" />
              ) : (
                <CheckCircle2 className="w-3 h-3 text-resolved-400 flex-shrink-0" />
              )}
              <span className="text-xs font-medium text-atlas-800 capitalize flex-1">
                {action}
              </span>
              <span className="text-2xs text-atlas-400">{modifierType}</span>
            </div>
            <div className="flex items-center gap-1 ml-5 flex-wrap">
              {fromType && (
                <>
                  <StatusChip status={fromType} />
                  <ArrowRight className="w-2.5 h-2.5 text-atlas-400 mx-0.5" />
                </>
              )}
              <StatusChip status={toType} />
            </div>
            <p className="text-2xs text-atlas-400 mt-1 ml-5">
              {createdAt ? new Date(createdAt).toLocaleDateString() : "—"}
            </p>
          </button>
        );
      })}
    </div>
  );
}

// ── Detail views ──────────────────────────────────────────────────────────────

function ClaimHistoryDetail({ item }: { item: Record<string, unknown> }) {
  const action = String(item.action ?? "");
  const fromType = item.from_claim_type ? String(item.from_claim_type) : null;
  const toType = String(item.to_claim_type ?? "");
  const reason = String(item.reason ?? "");
  const modifierType = String(item.modifier_type ?? "SYSTEM");
  const modifierId = item.modifier_id ? String(item.modifier_id) : null;
  const createdAt = String(item.created_at ?? "");
  const claimId = item.claim_id ? String(item.claim_id) : null;
  const fromValue = item.from_value;
  const toValue = item.to_value;
  const rowHash = item.row_hash ? String(item.row_hash) : null;
  const prevHash = item.prev_hash ? String(item.prev_hash) : null;

  return (
    <div className="p-5 space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-atlas-900 mb-3">
          State transition
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          {fromType && (
            <>
              <StatusChip status={fromType} />
              <ArrowRight className="w-3 h-3 text-atlas-400" />
            </>
          )}
          <StatusChip status={toType} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
            Action
          </p>
          <p className="text-xs text-atlas-800 mt-0.5 capitalize">
            {action || "—"}
          </p>
        </div>
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
            Modifier
          </p>
          <p className="text-xs text-atlas-800 mt-0.5">{modifierType}</p>
        </div>
      </div>

      {(fromValue != null || toValue != null) && (
        <div className="border border-atlas-200 rounded p-3 space-y-3">
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
            Values
          </p>
          {fromValue != null && (
            <div>
              <p className="text-2xs text-atlas-500 mb-0.5">Before</p>
              <p className="text-xs font-mono text-atlas-700 break-all">
                {typeof fromValue === "object"
                  ? JSON.stringify(fromValue)
                  : String(fromValue)}
              </p>
            </div>
          )}
          {toValue != null && (
            <div>
              <p className="text-2xs text-atlas-500 mb-0.5">After</p>
              <p className="text-xs font-mono text-atlas-700 break-all">
                {typeof toValue === "object"
                  ? JSON.stringify(toValue)
                  : String(toValue)}
              </p>
            </div>
          )}
        </div>
      )}

      {reason && (
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
            Reason
          </p>
          <p className="text-xs text-atlas-600 italic">"{reason}"</p>
        </div>
      )}

      <div className="space-y-1.5 border-t border-atlas-100 pt-3">
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
        <div className="flex justify-between text-2xs">
          <span className="text-atlas-500">When</span>
          <span className="text-atlas-700">
            {createdAt ? new Date(createdAt).toLocaleString() : "—"}
          </span>
        </div>
      </div>

      {/* HMAC chain links */}
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
              <p className="text-2xs text-atlas-500 mb-1">Previous hash (prev_hash)</p>
              <CopyableHash value={prevHash} />
            </div>
          )}
          {rowHash && (
            <div>
              <p className="text-2xs text-atlas-500 mb-1">This entry (row_hash)</p>
              <CopyableHash value={rowHash} />
            </div>
          )}
          <p className="text-2xs text-atlas-400 leading-relaxed">
            Each entry's row_hash = HMAC-SHA256 over this row. The next entry's
            prev_hash must equal this row_hash for the chain to be intact.
          </p>
        </div>
      )}
    </div>
  );
}

function DetailView({
  section,
  data,
  index,
}: {
  section: ProvenanceSection;
  data: ProvenanceResponse;
  index: number | null;
}) {
  if (index === null) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-atlas-500">
        Select an item to view details
        <span className="ml-2 text-2xs text-atlas-400">(j / k to navigate)</span>
      </div>
    );
  }

  if (section === "claims") {
    const item = data.claims[index];
    if (!item) return null;
    const renderKV = (obj: Record<string, unknown>) =>
      Object.entries(obj).map(([key, value]) => {
        if (value == null) return null;
        if (typeof value === "object" && !Array.isArray(value)) return null;
        return (
          <div key={key}>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
              {key.replace(/([A-Z])/g, " $1").replace(/_/g, " ")}
            </p>
            <p className="text-xs text-atlas-800 mt-0.5 font-mono">
              {Array.isArray(value) ? value.join(", ") : String(value)}
            </p>
          </div>
        );
      });
    return (
      <div className="p-5">
        <h3 className="text-sm font-semibold text-atlas-900 mb-4">Claim detail</h3>
        <div className="space-y-2">{renderKV(item)}</div>
      </div>
    );
  }

  if (section === "conflicts") {
    const item = data.conflicts[index];
    if (!item) return null;
    return (
      <div className="p-5">
        <h3 className="text-sm font-semibold text-atlas-900 mb-1">
          {item.fieldName.replace(/_/g, " ")}
        </h3>
        <div className="flex items-center gap-2 mb-4">
          <StatusChip status={item.status} />
          <span className="text-2xs text-atlas-500">v{item.version}</span>
        </div>
        <div className="space-y-2">
          {item.lastModifiedReason && (
            <p className="text-xs text-atlas-600 italic">"{item.lastModifiedReason}"</p>
          )}
          <div className="flex justify-between text-2xs">
            <span className="text-atlas-500">Claims</span>
            <span className="text-atlas-700">{item.claimIds.length}</span>
          </div>
          {item.resolvedAt && (
            <div className="flex justify-between text-2xs">
              <span className="text-atlas-500">Resolved</span>
              <span className="text-atlas-700">
                {new Date(item.resolvedAt).toLocaleDateString()}
              </span>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (section === "projection_history") {
    const item = data.projectionHistory[index];
    if (!item) return null;
    return (
      <div className="p-5 space-y-3">
        <h3 className="text-sm font-semibold text-atlas-900">
          Projection v{item.version}
        </h3>
        <div className="space-y-2">
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
              When
            </p>
            <p className="text-xs text-atlas-800 mt-0.5">
              {item.createdAt ? new Date(item.createdAt).toLocaleString() : "—"}
            </p>
          </div>
          {item.reason && (
            <div>
              <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
                Reason
              </p>
              <p className="text-xs text-atlas-600 italic mt-0.5">"{item.reason}"</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (section === "claim_histories") {
    const item = data.claimHistories[index];
    if (!item) return null;
    return <ClaimHistoryDetail item={item} />;
  }

  return null;
}

// ── Main Provenance page ──────────────────────────────────────────────────────

export default function Provenance() {
  const { slug } = useParams<{ slug: string }>();
  const { data: provenance, isLoading } = useCaseProvenance(slug);
  const [activeSection, setActiveSection] =
    useState<ProvenanceSection>("claims");
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  // Build claim_id → ClaimHistoryEntry[] for attaching history to claim EvidencePanel
  const historiesByClaimId = useMemo(() => {
    const map = new Map<string, ClaimHistoryEntry[]>();
    for (const h of provenance?.claimHistories ?? []) {
      const claimId = String(h.claim_id ?? "");
      if (!claimId) continue;
      const entry: ClaimHistoryEntry = {
        action: String(h.action ?? ""),
        reason: String(h.reason ?? ""),
        toClaimType: String(h.to_claim_type ?? ""),
        fromClaimType: h.from_claim_type ? String(h.from_claim_type) : null,
        createdAt: String(h.created_at ?? ""),
      };
      const bucket = map.get(claimId) ?? [];
      bucket.push(entry);
      map.set(claimId, bucket);
    }
    return map;
  }, [provenance?.claimHistories]);

  // Items in the active section drive keyboard navigation
  const currentItems = useMemo(() => {
    if (!provenance) return [];
    switch (activeSection) {
      case "claims":
        return provenance.claims;
      case "conflicts":
        return provenance.conflicts;
      case "projection_history":
        return provenance.projectionHistory;
      case "claim_histories":
        return provenance.claimHistories;
    }
  }, [provenance, activeSection]);

  // j/k keyboard navigation
  useEffect(() => {
    if (!currentItems.length) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((prev) => ((prev ?? -1) + 1) % currentItems.length);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx(
          (prev) => ((prev ?? 0) - 1 + currentItems.length) % currentItems.length,
        );
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [currentItems]);

  const counts: Record<ProvenanceSection, number> = {
    claims: provenance?.claims.length ?? 0,
    conflicts: provenance?.conflicts.length ?? 0,
    projection_history: provenance?.projectionHistory.length ?? 0,
    claim_histories: provenance?.claimHistories.length ?? 0,
  };

  const handleSectionChange = (s: ProvenanceSection) => {
    setActiveSection(s);
    setSelectedIdx(null);
  };

  if (isLoading) {
    return (
      <CaseWorkspaceLayout
        left={<TableSkeleton rows={8} />}
        center={<DetailSkeleton />}
      />
    );
  }

  if (!provenance) {
    return (
      <CaseWorkspaceLayout
        left={
          <EmptyState
            icon={<Link2 className="w-8 h-8" strokeWidth={1.25} />}
            title="No provenance data"
            description="Provenance appears after evidence is ingested."
          />
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            No data
          </div>
        }
      />
    );
  }

  // For the claims section: build a NormalisedClaim with attached history
  const selectedClaim =
    activeSection === "claims" && selectedIdx !== null
      ? provenance.claims[selectedIdx]
      : null;
  const normalisedClaim = selectedClaim
    ? {
        ...fromProvenanceClaim(selectedClaim),
        history: (() => {
          const claimId = String(selectedClaim.id ?? "");
          const entries = historiesByClaimId.get(claimId);
          return entries?.length ? entries : undefined;
        })(),
      }
    : null;

  const leftContent = (
    <div>
      <SectionNav
        active={activeSection}
        onSelect={handleSectionChange}
        counts={counts}
      />
      {activeSection === "claims" && (
        <ClaimsList
          claims={provenance.claims}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
        />
      )}
      {activeSection === "conflicts" && (
        <ConflictsList
          conflicts={provenance.conflicts}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
        />
      )}
      {activeSection === "projection_history" && (
        <ProjectionHistoryList
          history={provenance.projectionHistory}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
        />
      )}
      {activeSection === "claim_histories" && (
        <ClaimHistoryList
          histories={provenance.claimHistories}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
        />
      )}
    </div>
  );

  return (
    <CaseWorkspaceLayout
      left={leftContent}
      center={
        <DetailView
          section={activeSection}
          data={provenance}
          index={selectedIdx}
        />
      }
      right={
        normalisedClaim ? (
          <EvidencePanel
            claim={normalisedClaim}
            conflicts={provenance.conflicts as unknown as ConflictSummary[]}
          />
        ) : (
          <div className="p-4 space-y-4">
            <div className="flex justify-between text-xs">
              <span className="text-atlas-500">Event ID</span>
              <span className="font-mono text-atlas-700 text-2xs">
                {provenance.eventId.slice(0, 12)}…
              </span>
            </div>
            {provenance.absorbedEventId && (
              <div className="flex justify-between text-xs">
                <span className="text-atlas-500">Absorbed from</span>
                <span className="font-mono text-atlas-700 text-2xs">
                  {provenance.absorbedEventId.slice(0, 12)}…
                </span>
              </div>
            )}
            {provenance.pagination.hasMore && (
              <p className="text-2xs text-disputed-600 bg-disputed-50 border border-disputed-200 rounded px-3 py-2">
                More provenance data is available via pagination cursors.
              </p>
            )}
            {activeSection === "claim_histories" && (
              <div className="flex items-start gap-2 bg-atlas-50 border border-atlas-200 rounded px-3 py-2">
                <Shield className="w-3.5 h-3.5 text-atlas-400 flex-shrink-0 mt-0.5" />
                <p className="text-2xs text-atlas-500">
                  All history entries are HMAC-protected. Select an entry to
                  verify its row_hash and prev_hash chain links.
                </p>
              </div>
            )}
            <p className="text-2xs text-atlas-400 italic">
              {activeSection === "claims"
                ? "Select a claim to view its full evidence chain."
                : "Select an item to view details."}
            </p>
          </div>
        )
      }
    />
  );
}
