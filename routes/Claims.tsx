import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useCaseDetail, useCaseEvidence } from "../features/cases/api";
import { useConflictListBySlug } from "../features/conflicts/api";
import { CaseWorkspaceLayout } from "../components/CaseWorkspaceLayout";
import { StatusChip, ReliabilityChip } from "../components/StatusChip";
import { EmptyState, TableSkeleton, DetailSkeleton } from "../components/Feedback";
import {
  MultiClaimPanel,
  fromPublicClaim,
  type NormalisedClaim,
} from "../components/EvidencePanel";
import { FileText, AlertTriangle, Search, X } from "lucide-react";
import type { EvidenceClaimItem, ConflictSummary } from "../types/api";

// ── Claims table (left rail) ──────────────────────────────────────────────────

function ClaimsTable({
  sortedFields,
  claimsByField,
  selectedField,
  onSelect,
  disputedSet,
  conflicts,
  onConflictNavigate,
}: {
  sortedFields: string[];
  claimsByField: Map<string, EvidenceClaimItem[]>;
  selectedField: string | null;
  onSelect: (field: string) => void;
  disputedSet: Set<string>;
  conflicts: ConflictSummary[];
  onConflictNavigate: (conflictId: string, e: React.MouseEvent) => void;
}) {
  const [query, setQuery] = useState("");

  const fields = query
    ? sortedFields.filter((f) => f.toLowerCase().includes(query.toLowerCase()))
    : sortedFields;

  return (
    <div className="flex flex-col h-full">
      {/* Search input */}
      <div className="px-3 py-2.5 border-b border-atlas-100 bg-white flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-atlas-400 pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter fields…"
            className="w-full pl-8 pr-7 py-1.5 text-xs border border-atlas-200 rounded bg-atlas-50/50 placeholder-atlas-400 focus:outline-none focus:ring-1 focus:ring-atlas-300 focus:border-atlas-300"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-atlas-400 hover:text-atlas-600"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
        <p className="text-2xs text-atlas-400 mt-1.5">
          {fields.length} field{fields.length !== 1 ? "s" : ""}
          {disputedSet.size > 0 && (
            <span className="ml-1 text-disputed-500">
              · {disputedSet.size} disputed
            </span>
          )}
        </p>
      </div>

      {/* Field list */}
      <div className="flex-1 overflow-y-auto">
        {fields.length === 0 ? (
          <p className="text-xs text-atlas-400 text-center py-8 px-4">
            No fields match "{query}"
          </p>
        ) : (
          fields.map((field) => {
            const fieldClaims = claimsByField.get(field) ?? [];
            const winning = fieldClaims.find((c) => c.isWinning);
            const isDisputed = disputedSet.has(field);
            const isSelected = selectedField === field;
            const conflict = !isDisputed
              ? conflicts.find((c) => c.fieldName === field)
              : undefined;

            return (
              <button
                key={field}
                onClick={() => onSelect(field)}
                className={`w-full text-left px-4 py-2.5 border-b border-atlas-100 transition-colors border-l-2 ${
                  isSelected
                    ? "bg-atlas-100/80 border-l-atlas-500"
                    : "hover:bg-atlas-50/50 border-l-transparent"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-atlas-800 flex-1 truncate">
                    {field.replace(/_/g, " ")}
                  </span>
                  {isDisputed && (
                    <button
                      onClick={(e) => {
                        const c = conflicts.find((c) => c.fieldName === field);
                        if (c) onConflictNavigate(c.id, e);
                        else e.stopPropagation();
                      }}
                      title={`View conflict for ${field.replace(/_/g, " ")}`}
                      className="flex-shrink-0 p-0.5 rounded hover:bg-disputed-100 transition-colors focus-ring"
                    >
                      <AlertTriangle className="w-3 h-3 text-disputed-500" />
                    </button>
                  )}
                </div>
                <p className="text-xs text-atlas-600 mt-0.5 truncate">
                  {winning
                    ? String(winning.fieldValue ?? "—")
                    : isDisputed
                      ? "Disputed"
                      : "—"}
                </p>
                <div className="flex items-center gap-1.5 mt-1">
                  {winning && <StatusChip status={winning.claimType} />}
                  {fieldClaims.length > 1 && (
                    <span className="text-2xs text-atlas-400">
                      {fieldClaims.length} claims
                    </span>
                  )}
                  {conflict?.status === "RESOLVED" && (
                    <span className="text-2xs text-resolved-500">resolved</span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

// ── Claim detail (center panel) ───────────────────────────────────────────────

function ClaimDetail({
  fieldName,
  claims,
}: {
  fieldName: string;
  claims: EvidenceClaimItem[];
}) {
  const fieldClaims = claims
    .filter((c) => c.fieldName === fieldName)
    .sort((a, b) => {
      if (a.isWinning && !b.isWinning) return -1;
      if (!a.isWinning && b.isWinning) return 1;
      return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
    });

  const sourceCount = new Set(fieldClaims.map((c) => c.sourceName)).size;

  return (
    <div className="p-5">
      <h2 className="text-sm font-semibold text-atlas-900 mb-1">
        {fieldName.replace(/_/g, " ")}
      </h2>
      <p className="text-xs text-atlas-500 mb-4">
        {fieldClaims.length} claim{fieldClaims.length !== 1 ? "s" : ""} from{" "}
        {sourceCount} source{sourceCount !== 1 ? "s" : ""}
      </p>

      <div className="space-y-3">
        {fieldClaims.map((claim, i) => (
          <div
            key={i}
            className={`border rounded p-3 ${
              claim.isWinning
                ? "border-resolved-300 bg-resolved-50/50"
                : claim.isSuperseded
                  ? "border-gray-200 bg-gray-50/30 opacity-60"
                  : "border-atlas-200"
            }`}
          >
            <div className="flex items-center gap-2 mb-2">
              <StatusChip status={claim.claimType} />
              {claim.isWinning && (
                <span className="text-2xs font-medium text-resolved-600">
                  Active winner
                </span>
              )}
              {claim.isSuperseded && (
                <span className="text-2xs text-superseded-500">Superseded</span>
              )}
            </div>
            <p className="text-sm font-medium text-atlas-900 mb-2">
              {String(claim.fieldValue ?? "null")}
            </p>
            <div className="flex items-center gap-3 text-2xs text-atlas-500">
              <span className="font-medium text-atlas-700">{claim.sourceName}</span>
              <span>{claim.sourceKind}</span>
              <ReliabilityChip tier={claim.sourceReliabilityTier} />
              <span>{new Date(claim.createdAt).toLocaleDateString()}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Claims page ──────────────────────────────────────────────────────────

export default function Claims() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const { data: detail } = useCaseDetail(slug);
  const { data: evidence, isLoading: evidenceLoading } = useCaseEvidence(slug);
  const { data: conflictList } = useConflictListBySlug(slug);
  const [selectedField, setSelectedField] = useState<string | null>(null);

  const rawClaims = evidence?.claims ?? [];
  const disputedFields = detail?.unresolvedConflictFields ?? [];
  const conflicts = conflictList ?? [];

  const normalisedClaims = useMemo(
    () => rawClaims.map(fromPublicClaim),
    [rawClaims],
  );

  const claimsByField = useMemo(() => {
    const map = new Map<string, EvidenceClaimItem[]>();
    for (const c of rawClaims) {
      const bucket = map.get(c.fieldName);
      if (bucket) bucket.push(c);
      else map.set(c.fieldName, [c]);
    }
    return map;
  }, [rawClaims]);

  const disputedSet = useMemo(
    () => new Set(disputedFields),
    [disputedFields],
  );

  // Disputed fields first, then alphabetical — single source of truth for sort.
  // Both keyboard nav and ClaimsTable consume this directly.
  const sortedFields = useMemo(() => {
    const all = [...claimsByField.keys()];
    return [
      ...all.filter((f) => disputedSet.has(f)).sort(),
      ...all.filter((f) => !disputedSet.has(f)).sort(),
    ];
  }, [claimsByField, disputedSet]);

  // j/k keyboard navigation
  useEffect(() => {
    if (!sortedFields.length) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        const idx = sortedFields.indexOf(selectedField ?? "");
        setSelectedField(sortedFields[(idx + 1) % sortedFields.length] ?? null);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const idx = sortedFields.indexOf(selectedField ?? "");
        setSelectedField(
          sortedFields[(idx - 1 + sortedFields.length) % sortedFields.length] ?? null,
        );
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [sortedFields, selectedField]);

  // Single handler for all conflict navigation (direct ID or disputed-field button).
  const navigateToConflict = useCallback(
    (conflictId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      if (slug) navigate(`/app/cases/${slug}/conflicts?conflict=${conflictId}`);
    },
    [slug, navigate],
  );

  // MultiClaimPanel uses string-only conflictId (no event), so wrap here.
  const handleConflictClick = useCallback(
    (conflictId: string) => {
      if (slug) navigate(`/app/cases/${slug}/conflicts?conflict=${conflictId}`);
    },
    [slug, navigate],
  );

  if (evidenceLoading) {
    return (
      <CaseWorkspaceLayout
        left={<TableSkeleton rows={10} />}
        center={<DetailSkeleton />}
      />
    );
  }

  if (rawClaims.length === 0) {
    return (
      <CaseWorkspaceLayout
        left={
          <EmptyState
            icon={<FileText className="w-8 h-8" strokeWidth={1.25} />}
            title="No claims for this case"
            description="Claims appear when evidence is ingested."
          />
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            No data to display
          </div>
        }
      />
    );
  }

  return (
    <CaseWorkspaceLayout
      left={
        <ClaimsTable
          sortedFields={sortedFields}
          claimsByField={claimsByField}
          selectedField={selectedField}
          onSelect={setSelectedField}
          disputedSet={disputedSet}
          conflicts={conflicts}
          onConflictNavigate={navigateToConflict}
        />
      }
      center={
        selectedField ? (
          <ClaimDetail fieldName={selectedField} claims={rawClaims} />
        ) : (
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            Select a field to view its claims
            <span className="ml-2 text-2xs text-atlas-400">(j / k to navigate)</span>
          </div>
        )
      }
      right={
        <MultiClaimPanel
          fieldName={selectedField}
          claims={normalisedClaims}
          sources={evidence?.sources}
          conflicts={conflicts}
          onConflictClick={handleConflictClick}
        />
      }
    />
  );
}
