import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useCaseDetail } from "../features/cases/api";
import {
  useConflictListBySlug,
  useConflictHistory,
  useConflictCandidates,
  useResolveConflict,
  useReopenConflict,
  parse409,
} from "../features/conflicts/api";
import { useAuth } from "../features/auth/AuthProvider";
import { CaseWorkspaceLayout } from "../components/CaseWorkspaceLayout";
import { StatusChip, ReliabilityChip } from "../components/StatusChip";
import {
  EmptyState,
  TableSkeleton,
  DetailSkeleton,
  ErrorPanel,
  ConflictModifiedBanner,
} from "../components/Feedback";
import { ConfirmDialog } from "../components/ConfirmDialog";
import {
  GitPullRequestArrow,
  CheckCircle2,
  RotateCcw,
} from "lucide-react";
import type {
  ConflictSummary,
  ConflictModified409,
  ConflictClaimCandidate,
  ActivityLogEntry,
} from "../types/api";

// ── Candidate Picker ──────────────────────────────────────────────────────

function CandidatePicker({
  candidates,
  selectedId,
  winningClaimId,
  disabled,
  onChange,
}: {
  candidates: ConflictClaimCandidate[];
  selectedId: string | null;
  winningClaimId: string | null;
  disabled: boolean;
  onChange: (claimId: string) => void;
}) {
  return (
    <div className="space-y-2">
      {candidates.map((c) => (
        <label
          key={c.claimId}
          className={`flex items-start gap-3 p-3 rounded-lg border transition-colors ${
            disabled ? "cursor-default" : "cursor-pointer"
          } ${
            selectedId === c.claimId
              ? "border-blue-400 bg-blue-50"
              : "border-atlas-200 bg-white hover:bg-atlas-50"
          }`}
        >
          <input
            type="radio"
            name="winner"
            value={c.claimId}
            checked={selectedId === c.claimId}
            disabled={disabled}
            onChange={() => onChange(c.claimId)}
            className="mt-0.5 accent-blue-600"
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-xs font-medium text-atlas-800 truncate">
                {c.sourceName}
              </span>
              <ReliabilityChip tier={c.sourceReliabilityTier} />
              {winningClaimId === c.claimId && (
                <span className="text-[10px] font-medium text-green-700 bg-green-100 px-1.5 py-0.5 rounded">
                  current winner
                </span>
              )}
              {c.isSuperseded && (
                <span className="text-[10px] font-medium text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded">
                  superseded
                </span>
              )}
            </div>
            <div className="text-xs font-mono text-atlas-700 bg-atlas-50 px-2 py-1 rounded break-all">
              {c.fieldValue == null ? (
                <span className="italic text-atlas-400">null</span>
              ) : (
                String(c.fieldValue)
              )}
            </div>
            <p className="text-[10px] text-atlas-400 mt-1">
              {c.claimType} · {new Date(c.createdAt).toLocaleString()}
            </p>
          </div>
        </label>
      ))}
    </div>
  );
}

// ── Candidate Compare Grid ──────────────────────────────────────────────────

function CandidateCompareGrid({
  candidates,
  selectedId,
  winningClaimId,
  disabled,
  onChange,
}: {
  candidates: ConflictClaimCandidate[];
  selectedId: string | null;
  winningClaimId: string | null;
  disabled: boolean;
  onChange: (claimId: string) => void;
}) {
  const valueStrings = candidates.map((c) =>
    c.fieldValue == null ? null : String(c.fieldValue),
  );
  const allSame = valueStrings.every((v) => v === valueStrings[0]);

  return (
    <div className="space-y-2">
      <p className={`text-[10px] font-medium ${allSame ? "text-atlas-400" : "text-amber-600"}`}>
        {allSame ? "All sources report the same value" : "Values differ across sources"}
      </p>
      <div
        className="grid gap-2"
        style={{
          gridTemplateColumns: `repeat(${Math.min(candidates.length, 4)}, minmax(0, 1fr))`,
        }}
      >
        {candidates.map((c, i) => {
          const isSelected = selectedId === c.claimId;
          const isWinner = winningClaimId === c.claimId;

          return (
            <label
              key={c.claimId}
              className={`flex flex-col rounded-lg border p-3 transition-colors ${
                disabled ? "cursor-default" : "cursor-pointer"
              } ${
                isSelected
                  ? "border-blue-400 bg-blue-50"
                  : "border-atlas-200 bg-white hover:bg-atlas-50"
              }`}
            >
              {/* Source name + reliability */}
              <span className="text-xs font-medium text-atlas-800 leading-tight mb-1.5 truncate">
                {c.sourceName}
              </span>
              <div className="mb-2">
                <ReliabilityChip tier={c.sourceReliabilityTier} />
              </div>

              {/* Field value — highlighted when values differ */}
              <div
                className={`font-mono text-xs px-2 py-1.5 rounded mb-2 break-all flex-1 ${
                  !allSame
                    ? "bg-amber-50 border border-amber-200 text-amber-900"
                    : "bg-atlas-50 text-atlas-700"
                }`}
              >
                {c.fieldValue == null ? (
                  <span className="italic text-atlas-400">null</span>
                ) : (
                  valueStrings[i]
                )}
              </div>

              {/* Status badges */}
              <div className="flex flex-wrap gap-1 mb-2 min-h-[1.125rem]">
                {isWinner && (
                  <span className="text-[10px] font-medium text-green-700 bg-green-100 px-1.5 py-0.5 rounded">
                    current winner
                  </span>
                )}
                {c.isSuperseded && (
                  <span className="text-[10px] font-medium text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded">
                    superseded
                  </span>
                )}
              </div>

              {/* Claim metadata */}
              <p className="text-[10px] text-atlas-400 mb-2">
                {c.claimType} · {new Date(c.createdAt).toLocaleString()}
              </p>

              {/* Radio — clicking the card or radio selects the candidate */}
              <div className="flex items-center gap-1.5 pt-2 border-t border-atlas-100 mt-auto">
                <input
                  type="radio"
                  name="winner"
                  value={c.claimId}
                  checked={isSelected}
                  disabled={disabled}
                  onChange={() => onChange(c.claimId)}
                  className="accent-blue-600"
                />
                <span className="text-[10px] text-atlas-500">
                  {isSelected ? "Selected" : "Select as winner"}
                </span>
              </div>
            </label>
          );
        })}
      </div>
    </div>
  );
}

// ── Conflict Table (left rail) ──────────────────────────────────────────────

function ConflictTable({
  conflicts,
  selectedId,
  onSelect,
  filter,
  onFilterChange,
}: {
  conflicts: ConflictSummary[];
  selectedId: string | null;
  onSelect: (c: ConflictSummary) => void;
  filter: string;
  onFilterChange: (v: string) => void;
}) {
  const filtered = filter
    ? conflicts.filter((c) =>
        c.fieldName.toLowerCase().includes(filter.toLowerCase()),
      )
    : conflicts;

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 pt-4 pb-2">
        <input
          type="text"
          placeholder="Search conflicts…"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          className="w-full px-3 py-1.5 text-xs border border-atlas-200 rounded-md focus:outline-none focus:border-atlas-400"
        />
      </div>

      <div className="px-4 pb-2 text-[10px] text-atlas-400 uppercase tracking-wider">
        {filtered.length} / {conflicts.length} conflict(s)
      </div>

      <div className="flex-1 overflow-y-auto">
        {filtered.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c)}
            className={`w-full text-left px-4 py-2.5 border-b border-atlas-100 hover:bg-atlas-50 transition-colors ${
              selectedId === c.id ? "bg-atlas-100" : ""
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-atlas-800">
                {c.fieldName.replace(/_/g, " ")}
              </span>
              <StatusChip status={c.status} />
            </div>
            <p className="text-[10px] text-atlas-400 mt-0.5">
              v{c.version} · {c.claimIds.length} claim(s)
            </p>
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="px-4 py-6 text-center text-xs text-atlas-400">
            {filter ? "No conflicts match filter" : "No conflicts"}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Conflict Detail (center panel) ─────────────────────────────────────────

function ConflictDetail({
  conflict,
  onConflictModified,
  userRole,
}: {
  conflict: ConflictSummary;
  onConflictModified: (e: ConflictModified409) => void;
  userRole: string;
}) {
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    conflict.winningClaimId,
  );
  const [resolutionNote, setResolutionNote] = useState("");
  const [showResolve, setShowResolve] = useState(false);
  const [showReopen, setShowReopen] = useState(false);
  const [viewMode, setViewMode] = useState<"list" | "compare">("list");

  const resolveMutation = useResolveConflict(conflict.eventId);
  const reopenMutation = useReopenConflict(conflict.eventId);
  const { data: history, isLoading: historyLoading } = useConflictHistory(conflict.id);
  const { data: candidatesData, isLoading: candidatesLoading } =
    useConflictCandidates(conflict.id);
  const candidates = candidatesData?.candidates ?? [];

  // Reset selected candidate when switching between conflicts.
  useEffect(() => {
    setSelectedCandidateId(conflict.winningClaimId);
    setResolutionNote("");
  }, [conflict.id, conflict.winningClaimId]);

  const handleResolve = useCallback(async () => {
    if (!selectedCandidateId) return;
    try {
      await resolveMutation.mutateAsync({
        conflictId: conflict.id,
        payload: {
          expected_version: conflict.version,
          winning_claim_id: selectedCandidateId,
          reason: resolutionNote || "Resolved by reviewer",
        },
      });
      setShowResolve(false);
      setResolutionNote("");
    } catch (err: unknown) {
      const parsed = parse409(err);
      if (parsed) onConflictModified(parsed);
    }
  }, [conflict, selectedCandidateId, resolutionNote, resolveMutation, onConflictModified]);

  const handleReopen = useCallback(async () => {
    try {
      await reopenMutation.mutateAsync({
        conflictId: conflict.id,
        payload: {
          expected_version: conflict.version,
          reason: resolutionNote || "Reopened for review",
        },
      });
      setShowReopen(false);
      setResolutionNote("");
    } catch (err: unknown) {
      const parsed = parse409(err);
      if (parsed) onConflictModified(parsed);
    }
  }, [conflict, resolutionNote, reopenMutation, onConflictModified]);

  const canResolve = userRole === "admin" || userRole === "reviewer";
  const isResolved = conflict.status === "RESOLVED";

  const selectedCandidateName =
    candidates.find((c) => c.claimId === selectedCandidateId)?.sourceName ?? "";

  return (
    <div className="p-5 overflow-y-auto h-full">
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-1">
          <h2 className="text-sm font-semibold text-atlas-800">
            {conflict.fieldName.replace(/_/g, " ")}
          </h2>
          <StatusChip status={conflict.status} />
        </div>
        <p className="text-[10px] text-atlas-400">
          v{conflict.version} · Created {new Date(conflict.createdAt).toLocaleString()}
          {isResolved && conflict.resolvedAt && (
            <> · Resolved {new Date(conflict.resolvedAt).toLocaleString()}</>
          )}
        </p>
      </div>

      {/* Competing claims */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-atlas-700">Competing claims</h3>
          {candidates.length > 1 && (
            <div className="flex rounded border border-atlas-200 overflow-hidden">
              {(["list", "compare"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setViewMode(mode)}
                  className={`px-2.5 py-0.5 text-[10px] font-medium transition-colors ${
                    viewMode === mode
                      ? "bg-atlas-800 text-white"
                      : "bg-white text-atlas-500 hover:bg-atlas-50"
                  }`}
                >
                  {mode === "list" ? "List" : "Compare"}
                </button>
              ))}
            </div>
          )}
        </div>
        {candidatesLoading && <DetailSkeleton />}
        {!candidatesLoading && candidates.length === 0 && (
          <p className="text-xs text-atlas-400">No candidate claims found</p>
        )}
        {candidates.length > 0 &&
          (viewMode === "compare" ? (
            <CandidateCompareGrid
              candidates={candidates}
              selectedId={selectedCandidateId}
              winningClaimId={conflict.winningClaimId}
              disabled={isResolved && !canResolve}
              onChange={setSelectedCandidateId}
            />
          ) : (
            <CandidatePicker
              candidates={candidates}
              selectedId={selectedCandidateId}
              winningClaimId={conflict.winningClaimId}
              disabled={isResolved && !canResolve}
              onChange={setSelectedCandidateId}
            />
          ))}
      </div>

      {/* Resolution actions */}
      {canResolve && (
        <div className="mb-5">
          <label className="block text-xs font-medium text-atlas-700 mb-1">
            Resolution note
          </label>
          <textarea
            rows={2}
            value={resolutionNote}
            onChange={(e) => setResolutionNote(e.target.value)}
            placeholder="Reason for this resolution…"
            className="w-full px-2 py-1.5 text-xs border border-atlas-200 rounded-md resize-none focus:outline-none focus:border-atlas-400"
          />
          <div className="flex items-center gap-2 mt-2">
            {!isResolved ? (
              <button
                onClick={() => setShowResolve(true)}
                disabled={!selectedCandidateId}
                className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-green-700 bg-green-50 rounded-md hover:bg-green-100 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <CheckCircle2 className="w-3 h-3" />
                Resolve
              </button>
            ) : (
              <button
                onClick={() => setShowReopen(true)}
                className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-amber-700 bg-amber-50 rounded-md hover:bg-amber-100"
              >
                <RotateCcw className="w-3 h-3" />
                Reopen
              </button>
            )}
          </div>
        </div>
      )}

      {/* Activity log */}
      <div>
        <h3 className="text-xs font-semibold text-atlas-700 mb-2">Activity log</h3>
        {historyLoading && <DetailSkeleton />}
        {!historyLoading && history?.transitions?.length === 0 && (
          <p className="text-xs text-atlas-400">No activity recorded</p>
        )}
        {history?.transitions?.map((entry: ActivityLogEntry) => (
          <div
            key={entry.id}
            className="flex items-start gap-2 py-2 border-b border-atlas-100 last:border-0"
          >
            <div className="w-2 h-2 rounded-full bg-atlas-300 mt-1 shrink-0" />
            <div>
              <div className="flex items-center gap-1">
                {entry.fromStatus && <StatusChip status={entry.fromStatus} />}
                {entry.fromStatus && (
                  <span className="text-[10px] text-atlas-300">→</span>
                )}
                <StatusChip status={entry.toStatus} />
              </div>
              {entry.reason && (
                <p className="text-[10px] text-atlas-500 mt-0.5">{entry.reason}</p>
              )}
              <p className="text-[10px] text-atlas-400 mt-0.5">
                {new Date(entry.createdAt).toLocaleString()}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Resolve confirmation */}
      {showResolve && (
        <ConfirmDialog
          title="Resolve Conflict"
          message={`Set "${selectedCandidateName}" as the winning source for this field?`}
          detail={resolutionNote || undefined}
          confirmLabel="Resolve"
          loading={resolveMutation.isPending}
          onConfirm={handleResolve}
          onCancel={() => setShowResolve(false)}
        />
      )}

      {/* Reopen confirmation */}
      {showReopen && (
        <ConfirmDialog
          title="Reopen Conflict"
          message="Reopen this conflict for re-review?"
          detail={resolutionNote || undefined}
          confirmLabel="Reopen"
          loading={reopenMutation.isPending}
          onConfirm={handleReopen}
          onCancel={() => setShowReopen(false)}
        />
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────

export default function Conflicts() {
  const { slug } = useParams<{ slug: string }>();
  const [searchParams] = useSearchParams();
  const { data: _caseData, isLoading: caseLoading } = useCaseDetail(slug ?? "");
  const { user } = useAuth();
  const [selected, setSelected] = useState<ConflictSummary | null>(null);
  const [filter, setFilter] = useState("");
  const [modifiedError, setModifiedError] = useState<ConflictModified409 | null>(null);

  const {
    data: conflicts,
    isLoading,
    error,
    refetch,
  } = useConflictListBySlug(slug ?? "");

  // Pre-select a conflict when navigating from the Claims page (?conflict=<id>).
  // A ref tracks the last-handled id so a conflicts refetch doesn't reset the
  // user's selection, but navigating here with a *different* id does update it.
  const initConflictId = searchParams.get("conflict");
  const handledConflictIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!initConflictId || !conflicts?.length) return;
    if (handledConflictIdRef.current === initConflictId) return;
    const match = conflicts.find((c) => c.id === initConflictId);
    if (match) {
      handledConflictIdRef.current = initConflictId;
      setSelected(match);
    }
  }, [conflicts, initConflictId]);

  // j/k keyboard navigation through the conflict list.
  useEffect(() => {
    if (!conflicts?.length) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        const idx = conflicts.findIndex((c) => c.id === selected?.id);
        setSelected(conflicts[(idx + 1) % conflicts.length] ?? null);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const idx = conflicts.findIndex((c) => c.id === selected?.id);
        setSelected(conflicts[(idx - 1 + conflicts.length) % conflicts.length] ?? null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [conflicts, selected]);

  const handleConflictModified = useCallback(
    (err: ConflictModified409) => {
      setModifiedError(err);
      refetch();
    },
    [refetch],
  );

  if (isLoading || caseLoading) {
    return (
      <CaseWorkspaceLayout
        left={<TableSkeleton rows={8} />}
        center={<DetailSkeleton />}
      />
    );
  }

  if (error) {
    return (
      <CaseWorkspaceLayout
        left={<div />}
        center={<ErrorPanel message="Failed to load conflicts" />}
      />
    );
  }

  return (
    <CaseWorkspaceLayout
      left={
        <div className="h-full flex flex-col">
          {modifiedError && (
            <ConflictModifiedBanner
              message={modifiedError.detail}
              modifierReason={modifiedError.modifierReason}
              onDismiss={() => setModifiedError(null)}
            />
          )}
          <ConflictTable
            conflicts={conflicts ?? []}
            selectedId={selected?.id ?? null}
            onSelect={(c) => setSelected(c)}
            filter={filter}
            onFilterChange={setFilter}
          />
        </div>
      }
      center={
        selected ? (
          <ConflictDetail
            conflict={selected}
            onConflictModified={handleConflictModified}
            userRole={user?.role ?? "analyst"}
          />
        ) : (
          <div className="h-full flex items-center justify-center">
            <EmptyState
              icon={<GitPullRequestArrow className="w-8 h-8" />}
              title="Select a conflict"
              description="Choose a conflict from the list to see competing claims, resolve, or review history."
            />
          </div>
        )
      }
    />
  );
}
