import { useDeferredValue, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useCaseAudit, useFieldExplanation } from "../features/cases/api";
import { FullWidthLayout } from "../components/CaseWorkspaceLayout";
import {
  EmptyState,
  TableSkeleton,
  ErrorPanel,
  DetailSkeleton,
} from "../components/Feedback";
import { ConfidenceChip } from "../components/StatusChip";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Edit3,
  Filter,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import type { AuditFieldRow, FieldExplanationResponse } from "../types/api";

type StatusFilter = "all" | "disputed" | "overridden" | "ok";
type ConfidenceFilter = "all" | "high" | "medium" | "low" | "unknown";

const statusOptions: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "All fields" },
  { value: "disputed", label: "Disputed" },
  { value: "overridden", label: "Overrides" },
  { value: "ok", label: "OK" },
];

const confidenceOptions: Array<{ value: ConfidenceFilter; label: string }> = [
  { value: "all", label: "All confidence" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
  { value: "unknown", label: "Unknown" },
];

function fieldLabel(fieldName: string): string {
  return fieldName
    .split("_")
    .filter(Boolean)
    .map((part) => {
      const known = part.toLowerCase();
      if (known === "ntsb") return "NTSB";
      if (known === "iata") return "IATA";
      if (known === "ifr") return "IFR";
      if (known === "fo") return "FO";
      return known.charAt(0).toUpperCase() + known.slice(1);
    })
    .join(" ");
}

function formatValue(value: unknown): string {
  if (value === null || typeof value === "undefined") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value === "__DISPUTED__" ? "Disputed" : value;
  return String(value);
}

function fieldStatus(field: AuditFieldRow): StatusFilter {
  if (field.isDisputed) return "disputed";
  if (field.isManuallyOverridden) return "overridden";
  return "ok";
}

function statusChip(field: AuditFieldRow) {
  if (field.isDisputed) {
    return (
      <span className="inline-flex items-center gap-1 chip chip-disputed">
        <AlertTriangle className="w-3 h-3" />
        Disputed
      </span>
    );
  }
  if (field.isManuallyOverridden) {
    return (
      <span className="inline-flex items-center gap-1 chip bg-atlas-100 text-atlas-700 border border-atlas-200">
        <Edit3 className="w-3 h-3" />
        Override
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 chip chip-resolved">
      <CheckCircle2 className="w-3 h-3" />
      OK
    </span>
  );
}

function filterFields(
  fields: AuditFieldRow[],
  query: string,
  status: StatusFilter,
  confidence: ConfidenceFilter,
): AuditFieldRow[] {
  const normalizedQuery = query.trim().toLowerCase();
  return fields.filter((field) => {
    if (status !== "all" && fieldStatus(field) !== status) return false;
    if (confidence !== "all" && field.confidence !== confidence) return false;
    if (!normalizedQuery) return true;

    const haystack = [
      field.fieldName,
      fieldLabel(field.fieldName),
      formatValue(field.currentValue),
      field.plainEnglish,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(normalizedQuery);
  });
}

function ExplanationPanel({
  field,
  explanation,
  isLoading,
  isError,
  onRetry,
  onClose,
}: {
  field: AuditFieldRow | null;
  explanation: FieldExplanationResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  onClose: () => void;
}) {
  if (!field) {
    return (
      <aside className="bg-white border border-atlas-200 rounded p-5 h-fit">
        <div className="flex items-center gap-2 text-atlas-700 mb-2">
          <ShieldCheck className="w-4 h-4" />
          <h2 className="text-sm font-semibold">Field explanation</h2>
        </div>
        <p className="text-xs text-atlas-500">
          Select a field to see the winning source, competing evidence, and conflict context.
        </p>
      </aside>
    );
  }

  return (
    <aside className="bg-white border border-atlas-200 rounded h-fit overflow-hidden sticky top-6">
      <div className="flex items-start gap-3 justify-between px-4 py-3 border-b border-atlas-200 bg-atlas-50/60">
        <div>
          <p className="text-2xs font-medium uppercase tracking-wider text-atlas-500">
            Field explanation
          </p>
          <h2 className="text-sm font-semibold text-atlas-900 mt-0.5">
            {fieldLabel(field.fieldName)}
          </h2>
          <p className="text-2xs font-mono text-atlas-500 mt-0.5">{field.fieldName}</p>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-atlas-100 text-atlas-500 focus-ring"
          aria-label="Close field explanation"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="p-4 space-y-4">
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
            Current value
          </p>
          <p className="text-sm text-atlas-800 break-words">{formatValue(field.currentValue)}</p>
        </div>

        <div className="flex flex-wrap gap-2">
          {statusChip(field)}
          <ConfidenceChip confidence={field.confidence} />
        </div>

        <p className="text-xs text-atlas-600 leading-relaxed">{field.plainEnglish}</p>

        {isLoading && <DetailSkeleton />}

        {isError && (
          <ErrorPanel
            message="Failed to load this field explanation."
            onRetry={onRetry}
          />
        )}

        {explanation && (
          <div className="space-y-4">
            {explanation.winner ? (
              <section>
                <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
                  Winning source
                </p>
                <div className="rounded border border-atlas-200 p-3">
                  <p className="text-xs font-medium text-atlas-800">
                    {explanation.winner.sourceName}
                  </p>
                  <p className="text-2xs text-atlas-500 mt-0.5">
                    {explanation.winner.sourceKind}
                  </p>
                  <p className="text-xs text-atlas-600 mt-2 leading-relaxed">
                    {explanation.winner.plainEnglish}
                  </p>
                </div>
              </section>
            ) : (
              <p className="text-xs text-atlas-500">No winning claim is selected for this field.</p>
            )}

            {explanation.conflict && (
              <section>
                <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
                  Conflict
                </p>
                <div className="rounded border border-disputed-200 bg-disputed-50 p-3">
                  <p className="text-xs font-medium text-disputed-800">
                    {explanation.conflict.status}
                  </p>
                  <p className="text-xs text-disputed-700 mt-1 leading-relaxed">
                    {explanation.conflict.plainEnglish}
                  </p>
                </div>
              </section>
            )}

            {explanation.losers.length > 0 && (
              <section>
                <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
                  Other evidence
                </p>
                <div className="space-y-2">
                  {explanation.losers.map((loser, index) => (
                    <div key={`${loser.sourceName}-${index}`} className="rounded border border-atlas-200 p-3">
                      <p className="text-xs font-medium text-atlas-800">{loser.sourceName}</p>
                      <p className="text-2xs text-atlas-500 mt-0.5">
                        Reported: {formatValue(loser.reportedValue)}
                      </p>
                      <p className="text-xs text-atlas-600 mt-2 leading-relaxed">
                        {loser.plainEnglish}
                      </p>
                    </div>
                  ))}
                </div>
                {explanation.losersTruncated && (
                  <p className="text-2xs text-atlas-500 mt-2">Additional evidence is truncated.</p>
                )}
              </section>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

export default function Audit() {
  const { slug } = useParams<{ slug: string }>();
  const { data: audit, isLoading, error, refetch } = useCaseAudit(slug);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [confidenceFilter, setConfidenceFilter] = useState<ConfidenceFilter>("all");
  const [selectedFieldName, setSelectedFieldName] = useState<string | null>(null);
  const deferredQuery = useDeferredValue(query);

  const selectedField = useMemo(
    () => audit?.fields.find((field) => field.fieldName === selectedFieldName) ?? null,
    [audit?.fields, selectedFieldName],
  );

  const filteredFields = useMemo(
    () => filterFields(audit?.fields ?? [], deferredQuery, statusFilter, confidenceFilter),
    [audit?.fields, deferredQuery, statusFilter, confidenceFilter],
  );

  const disputedCount = audit?.fields.filter((field) => field.isDisputed).length ?? 0;
  const overrideCount = audit?.fields.filter((field) => field.isManuallyOverridden).length ?? 0;

  const {
    data: explanation,
    isLoading: explanationLoading,
    isError: explanationError,
    refetch: refetchExplanation,
  } = useFieldExplanation(slug, selectedFieldName);

  if (!slug) {
    return (
      <FullWidthLayout>
        <div className="max-w-5xl mx-auto p-6">
          <EmptyState
            icon={<Activity className="w-8 h-8" strokeWidth={1.25} />}
            title="Select a case to view its audit log"
            description="Audit summaries are scoped to a published case and its evidence-backed projection."
          />
        </div>
      </FullWidthLayout>
    );
  }

  return (
    <FullWidthLayout>
      <div className="max-w-7xl mx-auto p-6">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-atlas-900">Evidence Audit</h1>
          <p className="text-xs text-atlas-500 mt-0.5">
            Review projection fields, filter risky evidence, and open source-backed field explanations.
          </p>
        </div>

        {error && (
          <div className="mb-4">
            <ErrorPanel
              message="Failed to load audit data."
              onRetry={() => void refetch()}
            />
          </div>
        )}

        {isLoading && <TableSkeleton rows={8} />}

        {audit && (
          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_24rem] gap-5">
            <main className="min-w-0">
              <div className="bg-white border border-atlas-200 rounded p-4 mb-4">
                <div className="flex flex-wrap items-center gap-3 mb-3">
                  <ConfidenceChip confidence={audit.confidence} />
                  <span className="text-xs text-atlas-500">
                    Projection v{audit.projectionVersion}
                  </span>
                  <span className="text-xs text-atlas-500">
                    Updated {new Date(audit.lastUpdatedAt).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-atlas-700">{audit.summary}</p>
                <p className="text-xs text-atlas-500 mt-1">{audit.confidenceMeaning}</p>

                <div className="grid grid-cols-3 gap-2 mt-4">
                  <div className="rounded border border-atlas-200 p-3">
                    <p className="text-2xs uppercase tracking-wider text-atlas-500">Fields</p>
                    <p className="text-lg font-semibold text-atlas-900">{audit.fields.length}</p>
                  </div>
                  <div className="rounded border border-disputed-200 bg-disputed-50 p-3">
                    <p className="text-2xs uppercase tracking-wider text-disputed-600">Disputed</p>
                    <p className="text-lg font-semibold text-disputed-800">{disputedCount}</p>
                  </div>
                  <div className="rounded border border-atlas-200 p-3">
                    <p className="text-2xs uppercase tracking-wider text-atlas-500">Overrides</p>
                    <p className="text-lg font-semibold text-atlas-900">{overrideCount}</p>
                  </div>
                </div>
              </div>

              <div className="bg-white border border-atlas-200 rounded p-3 mb-4">
                <div className="flex flex-col lg:flex-row gap-3">
                  <label className="flex-1 relative">
                    <Search className="w-4 h-4 text-atlas-400 absolute left-3 top-1/2 -translate-y-1/2" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Search fields, values, or explanations…"
                      className="w-full pl-9 pr-3 py-2 text-sm border border-atlas-200 rounded focus-ring"
                    />
                  </label>

                  <div className="flex gap-2">
                    <label className="relative">
                      <Filter className="w-3.5 h-3.5 text-atlas-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                      <select
                        value={statusFilter}
                        onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
                        className="pl-8 pr-8 py-2 text-sm border border-atlas-200 rounded bg-white focus-ring"
                      >
                        {statusOptions.map((option) => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>

                    <select
                      value={confidenceFilter}
                      onChange={(event) => setConfidenceFilter(event.target.value as ConfidenceFilter)}
                      className="px-3 py-2 text-sm border border-atlas-200 rounded bg-white focus-ring"
                    >
                      {confidenceOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              {filteredFields.length === 0 ? (
                <EmptyState
                  icon={<Activity className="w-8 h-8" strokeWidth={1.25} />}
                  title="No audit fields match the current filters"
                  description="Clear search or switch filters to review more projection fields."
                />
              ) : (
                <div className="bg-white rounded border border-atlas-200 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-atlas-500 font-medium border-b border-atlas-200 bg-atlas-50/50">
                        <th className="px-4 py-2.5">Field</th>
                        <th className="px-4 py-2.5">Current value</th>
                        <th className="px-4 py-2.5">Status</th>
                        <th className="px-4 py-2.5">Confidence</th>
                        <th className="px-4 py-2.5">Assessment</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredFields.map((field) => {
                        const isSelected = field.fieldName === selectedFieldName;
                        return (
                          <tr
                            key={field.fieldName}
                            onClick={() => setSelectedFieldName(field.fieldName)}
                            className={`table-row-dense cursor-pointer transition-colors ${
                              isSelected ? "bg-atlas-50" : "hover:bg-atlas-50/60"
                            }`}
                          >
                            <td className="px-4 py-2.5">
                              <span className="text-xs font-medium text-atlas-800">
                                {fieldLabel(field.fieldName)}
                              </span>
                              <p className="text-2xs font-mono text-atlas-400 mt-0.5">
                                {field.fieldName}
                              </p>
                            </td>
                            <td className="px-4 py-2.5 text-xs text-atlas-700 max-w-xs truncate">
                              {formatValue(field.currentValue)}
                            </td>
                            <td className="px-4 py-2.5">
                              <div className="flex items-center gap-1.5">{statusChip(field)}</div>
                            </td>
                            <td className="px-4 py-2.5">
                              <ConfidenceChip confidence={field.confidence} />
                            </td>
                            <td className="px-4 py-2.5 text-xs text-atlas-600 max-w-sm">
                              {field.plainEnglish}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </main>

            <ExplanationPanel
              field={selectedField}
              explanation={explanation}
              isLoading={explanationLoading}
              isError={explanationError}
              onRetry={() => void refetchExplanation()}
              onClose={() => setSelectedFieldName(null)}
            />
          </div>
        )}
      </div>
    </FullWidthLayout>
  );
}
