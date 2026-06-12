import { useState, useEffect, useMemo } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { useCaseDetail } from "../features/cases/api";
import { CaseWorkspaceLayout } from "../components/CaseWorkspaceLayout";
import { EmptyState, TableSkeleton, DetailSkeleton } from "../components/Feedback";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { RoleGate } from "../components/RoleGate";
import {
  Shield,
  ShieldOff,
  FileText,
  AlertTriangle,
  CheckCircle2,
  Clock,
  RefreshCw,
  XCircle,
  Lock,
  Unlock,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface DocumentItem {
  documentId: string;
  filename: string;
  sizeBytes: number;
  parseStatus: string;
  pageCount: number | null;
  sourceId: string | null;
  eventId: string | null;
  createdAt: string;
  updatedAt: string;
}

interface DocumentListResponse {
  documents: DocumentItem[];
  total: number;
}

type FilterKey = "all" | "parsed" | "failed" | "privileged";

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

function extractApiErrorMessage(err: unknown): string {
  if (err && typeof err === "object") {
    const e = err as {
      message?: string;
      detail?: { error?: { message?: string }; message?: string };
    };
    return (
      e.detail?.error?.message ??
      e.detail?.message ??
      e.message ??
      "Operation failed."
    );
  }
  return "Operation failed.";
}

// ── Status chip ───────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<
  string,
  { label: string; icon: React.ReactNode; cls: string }
> = {
  uploaded:     { label: "Uploaded",      icon: <Clock className="w-3 h-3" />,                         cls: "bg-atlas-50 text-atlas-600 border border-atlas-200" },
  parsing:      { label: "Parsing…",      icon: <RefreshCw className="w-3 h-3 animate-spin" />,        cls: "bg-atlas-50 text-atlas-600 border border-atlas-200" },
  parsed:       { label: "Parsed",        icon: <CheckCircle2 className="w-3 h-3" />,                  cls: "bg-green-50 text-green-700 border border-green-200" },
  parse_failed: { label: "Parse failed",  icon: <AlertTriangle className="w-3 h-3" />,                 cls: "bg-red-50 text-red-700 border border-red-200" },
  ingesting:    { label: "Ingesting…",    icon: <RefreshCw className="w-3 h-3 animate-spin" />,        cls: "bg-atlas-50 text-atlas-600 border border-atlas-200" },
  ingested:     { label: "Ingested",      icon: <CheckCircle2 className="w-3 h-3" />,                  cls: "bg-green-50 text-green-700 border border-green-200" },
  ingest_failed:{ label: "Ingest failed", icon: <AlertTriangle className="w-3 h-3" />,                 cls: "bg-red-50 text-red-700 border border-red-200" },
};

function ParseStatusChip({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG["uploaded"]!;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${cfg.cls}`}>
      {cfg.icon}
      {cfg.label}
    </span>
  );
}

// ── Left panel ────────────────────────────────────────────────────────────────

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all",       label: "All" },
  { key: "parsed",    label: "Parsed" },
  { key: "failed",    label: "Failed" },
  { key: "privileged",label: "Privileged" },
];

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
          className={`flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded transition-colors ${
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

function DocRow({
  doc,
  isSelected,
  isPrivileged,
  onClick,
}: {
  doc: DocumentItem;
  isSelected: boolean;
  isPrivileged: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-atlas-100 transition-colors border-l-2 ${
        isSelected
          ? "bg-atlas-100/80 border-l-atlas-500"
          : "hover:bg-atlas-50/50 border-l-transparent"
      }`}
    >
      <div className="flex items-start gap-2.5">
        {isPrivileged ? (
          <Shield className="w-4 h-4 text-purple-500 shrink-0 mt-0.5" />
        ) : (
          <FileText className="w-4 h-4 text-atlas-400 shrink-0 mt-0.5" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-medium text-atlas-800 truncate flex-1">
              {doc.filename}
            </span>
            <ParseStatusChip status={doc.parseStatus} />
          </div>
          <p className="text-[10px] text-atlas-500 mt-0.5">
            {doc.pageCount != null ? `${doc.pageCount}pp · ` : ""}
            {formatBytes(doc.sizeBytes)}
          </p>
          {isPrivileged && (
            <p className="text-[10px] text-purple-600 mt-0.5 font-medium">
              Under privilege hold
            </p>
          )}
        </div>
      </div>
    </button>
  );
}

// ── Center panel ──────────────────────────────────────────────────────────────

function DocumentDetail({
  doc,
  isPrivileged,
  mfaCode,
  reason,
  onMfaChange,
  onReasonChange,
  onMarkPrivileged,
  onReleaseHold,
  actionLoading,
  lastError,
}: {
  doc: DocumentItem | null;
  isPrivileged: boolean;
  mfaCode: string;
  reason: string;
  onMfaChange: (v: string) => void;
  onReasonChange: (v: string) => void;
  onMarkPrivileged: () => void;
  onReleaseHold: () => void;
  actionLoading: boolean;
  lastError: string | null;
}) {
  if (!doc) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-atlas-500">
        Select a document to review
      </div>
    );
  }

  const mfaReady = mfaCode.length === 6;

  return (
    <div className="p-5 max-w-2xl">
      {/* Header */}
      <div className="flex items-start gap-3 mb-5">
        <div
          className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
            isPrivileged
              ? "bg-purple-100 text-purple-600"
              : "bg-atlas-100 text-atlas-600"
          }`}
        >
          {isPrivileged ? (
            <Shield className="w-5 h-5" />
          ) : (
            <FileText className="w-5 h-5" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold text-atlas-900 break-all">
            {doc.filename}
          </h2>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <ParseStatusChip status={doc.parseStatus} />
            {isPrivileged && (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-50 text-purple-700 border border-purple-200">
                <Shield className="w-3 h-3" />
                Privilege hold
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-2 gap-4 mb-6 bg-atlas-50/50 rounded-lg border border-atlas-200 p-4">
        <div>
          <p className="text-[10px] font-medium text-atlas-500 uppercase tracking-wider">
            Pages
          </p>
          <p className="text-sm font-medium text-atlas-800 mt-0.5">
            {doc.pageCount ?? "—"}
          </p>
        </div>
        <div>
          <p className="text-[10px] font-medium text-atlas-500 uppercase tracking-wider">
            Size
          </p>
          <p className="text-sm font-medium text-atlas-800 mt-0.5">
            {formatBytes(doc.sizeBytes)}
          </p>
        </div>
        <div>
          <p className="text-[10px] font-medium text-atlas-500 uppercase tracking-wider">
            Uploaded
          </p>
          <p className="text-xs text-atlas-700 mt-0.5">
            {new Date(doc.createdAt).toLocaleString()}
          </p>
        </div>
        <div>
          <p className="text-[10px] font-medium text-atlas-500 uppercase tracking-wider">
            Document ID
          </p>
          <p className="text-[10px] font-mono text-atlas-600 mt-0.5">
            {doc.documentId.slice(0, 13)}…
          </p>
        </div>
      </div>

      {/* Privilege actions (admin only) */}
      <RoleGate
        role="admin"
        fallback={
          <div className="flex items-start gap-2 bg-atlas-50 border border-atlas-200 rounded-lg p-4">
            <Lock className="w-4 h-4 text-atlas-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-xs font-medium text-atlas-700">
                Admin access required
              </p>
              <p className="text-[10px] text-atlas-500 mt-0.5">
                Legal holds can only be applied or released by administrators.
              </p>
            </div>
          </div>
        }
      >
        <div className="space-y-4">
          <h3 className="text-[10px] font-semibold text-atlas-700 uppercase tracking-wider">
            Privilege Actions
          </h3>

          {/* Reason */}
          <div>
            <label className="block text-[10px] font-medium text-atlas-500 uppercase tracking-wider mb-1">
              Reason
            </label>
            <textarea
              value={reason}
              onChange={(e) => onReasonChange(e.target.value)}
              rows={2}
              className="w-full px-3 py-2 text-xs border border-atlas-200 rounded-md focus:outline-none focus:ring-1 focus:ring-atlas-400 resize-none"
              placeholder="Describe why this document is privileged…"
            />
          </div>

          {/* MFA code */}
          <div>
            <label className="block text-[10px] font-medium text-atlas-500 uppercase tracking-wider mb-1">
              MFA Code
              <span className="ml-1 text-atlas-400 normal-case font-normal">
                (required)
              </span>
            </label>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              value={mfaCode}
              onChange={(e) =>
                onMfaChange(e.target.value.replace(/\D/g, ""))
              }
              placeholder="000000"
              className="w-28 px-3 py-2 text-sm font-mono border border-atlas-200 rounded-md focus:outline-none focus:ring-1 focus:ring-atlas-400 tracking-widest"
            />
            {!mfaReady && (
              <p className="text-[10px] text-atlas-400 mt-1">
                Enter your 6-digit authenticator code to enable this action.
              </p>
            )}
          </div>

          {/* Action button */}
          {isPrivileged ? (
            <button
              onClick={onReleaseHold}
              disabled={actionLoading || !mfaReady}
              className="flex items-center gap-2 px-4 py-2 text-xs font-medium text-atlas-700 bg-atlas-50 border border-atlas-200 rounded-md hover:bg-atlas-100 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Unlock className="w-3.5 h-3.5" />
              Release Privilege Hold
            </button>
          ) : (
            <button
              onClick={onMarkPrivileged}
              disabled={actionLoading || !mfaReady || !reason.trim()}
              className="flex items-center gap-2 px-4 py-2 text-xs font-medium text-white bg-purple-600 rounded-md hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Shield className="w-3.5 h-3.5" />
              {actionLoading ? "Applying hold…" : "Apply Privilege Hold"}
            </button>
          )}

          {/* Error */}
          {lastError && (
            <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3">
              <XCircle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
              <p className="text-[10px] text-red-700">{lastError}</p>
            </div>
          )}

          {/* Info note */}
          <div className="flex items-start gap-2 bg-atlas-50 border border-atlas-200 rounded-lg p-3">
            <Shield className="w-3.5 h-3.5 text-atlas-400 shrink-0 mt-0.5" />
            <p className="text-[10px] text-atlas-500 leading-relaxed">
              A privilege hold prevents this document from appearing in standard
              report exports. The hold is recorded in the compliance ledger and
              can only be released by an administrator with a valid MFA code.
            </p>
          </div>
        </div>
      </RoleGate>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function PrivilegeReview() {
  const { slug } = useParams<{ slug: string }>();

  // Resolve canonicalEventId (UUID) needed to filter documents by event
  const { data: caseDetail, isLoading: caseLoading } = useCaseDetail(slug);
  const canonicalEventId = caseDetail?.canonicalEventId;

  const {
    data: docData,
    isLoading: docsLoading,
    error: docsError,
  } = useQuery({
    queryKey: ["privilege-docs", canonicalEventId],
    queryFn: () =>
      apiFetch<DocumentListResponse>(
        `/api/documents?event_id=${canonicalEventId}&limit=200`,
      ),
    enabled: !!canonicalEventId,
  });

  // Filter / selection state
  const [filter, setFilter] = useState<FilterKey>("all");
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

  // Legal-hold tracking — the backend does not expose has_legal_hold in
  // DocumentListItem, so we track what we've marked this session in local state.
  const [privilegedDocIds, setPrivilegedDocIds] = useState<Set<string>>(
    new Set(),
  );

  // Admin action state
  const [confirmTarget, setConfirmTarget] = useState<{
    doc: DocumentItem;
    action: "hold" | "release";
  } | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [reason, setReason] = useState(
    "Privilege review — document contains protected material",
  );
  const [lastError, setLastError] = useState<string | null>(null);

  const legalHoldMutation = useMutation({
    mutationFn: async ({
      docId,
      action,
      reason,
      mfaCode,
    }: {
      docId: string;
      action: "hold" | "release";
      reason: string;
      mfaCode: string;
    }) => {
      // apiFetch snake_cases camelCase body keys → entity_type, entity_id
      await apiFetch("/api/admin/legal-holds", {
        method: action === "hold" ? "POST" : "DELETE",
        body: JSON.stringify({
          entityType: "uploaded_document",
          entityId: docId,
          reason,
        }),
        headers: { "X-MFA-Code": mfaCode },
      });
    },
    onSuccess: (_, vars) => {
      setLastError(null);
      setMfaCode("");
      if (vars.action === "hold") {
        setPrivilegedDocIds((prev) => new Set([...prev, vars.docId]));
      } else {
        setPrivilegedDocIds((prev) => {
          const next = new Set(prev);
          next.delete(vars.docId);
          return next;
        });
      }
    },
    onError: (err: unknown) => {
      setLastError(extractApiErrorMessage(err));
    },
  });

  const documents = docData?.documents ?? [];

  const filtered = useMemo(() => {
    switch (filter) {
      case "parsed":
        return documents.filter((d) =>
          ["parsed", "ingested"].includes(d.parseStatus),
        );
      case "failed":
        return documents.filter((d) =>
          ["parse_failed", "ingest_failed"].includes(d.parseStatus),
        );
      case "privileged":
        return documents.filter((d) => privilegedDocIds.has(d.documentId));
      default:
        return documents;
    }
  }, [documents, filter, privilegedDocIds]);

  const selectedDoc =
    filtered.find((d) => d.documentId === selectedDocId) ?? null;

  const counts = useMemo<Record<FilterKey, number>>(
    () => ({
      all: documents.length,
      parsed: documents.filter((d) =>
        ["parsed", "ingested"].includes(d.parseStatus),
      ).length,
      failed: documents.filter((d) =>
        ["parse_failed", "ingest_failed"].includes(d.parseStatus),
      ).length,
      privileged: privilegedDocIds.size,
    }),
    [documents, privilegedDocIds],
  );

  // j/k keyboard navigation
  useEffect(() => {
    if (!filtered.length) return;
    const onKey = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      )
        return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedDocId((prev) => {
          const idx = filtered.findIndex((d) => d.documentId === prev);
          return filtered[Math.min(idx + 1, filtered.length - 1)]?.documentId ?? prev;
        });
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedDocId((prev) => {
          const idx = filtered.findIndex((d) => d.documentId === prev);
          return filtered[Math.max(idx - 1, 0)]?.documentId ?? prev;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filtered]);

  // Reset selection on filter change; clear error on doc change
  useEffect(() => { setSelectedDocId(null); }, [filter]);
  useEffect(() => { setLastError(null); }, [selectedDocId]);

  const isLoading = caseLoading || (!!canonicalEventId && docsLoading);

  if (isLoading) {
    return (
      <CaseWorkspaceLayout
        left={<TableSkeleton rows={6} />}
        center={<DetailSkeleton />}
      />
    );
  }

  if (docsError) {
    return (
      <CaseWorkspaceLayout
        left={
          <EmptyState
            icon={<XCircle className="w-6 h-6" />}
            title="Failed to load documents"
          />
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            —
          </div>
        }
      />
    );
  }

  const left = (
    <div>
      {/* Panel header */}
      <div className="px-4 py-3 border-b border-atlas-100">
        <h2 className="text-xs font-semibold text-atlas-700">
          Privilege Review
        </h2>
        <p className="text-[10px] text-atlas-500 mt-0.5">
          {docData?.total ?? 0} document{docData?.total === 1 ? "" : "s"}
        </p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 gap-2 px-3 py-3 border-b border-atlas-100 bg-atlas-50/50">
        <div className="bg-white rounded border border-atlas-200 px-2 py-1.5 text-center">
          <p className="text-base font-bold text-amber-600">{counts.parsed}</p>
          <p className="text-[10px] text-atlas-500">Parsed</p>
        </div>
        <div className="bg-white rounded border border-atlas-200 px-2 py-1.5 text-center">
          <p className="text-base font-bold text-purple-600">
            {counts.privileged}
          </p>
          <p className="text-[10px] text-atlas-500">Privileged</p>
        </div>
      </div>

      <FilterBar active={filter} counts={counts} onSelect={setFilter} />

      {filtered.length === 0 ? (
        <EmptyState
          icon={<Shield className="w-6 h-6" strokeWidth={1.25} />}
          title={
            filter === "privileged"
              ? "No privileged documents"
              : "No documents"
          }
          description={
            filter === "privileged"
              ? "Documents you mark as privileged will appear here."
              : "Upload documents on the Documents page first."
          }
        />
      ) : (
        <div>
          {filtered.map((doc) => (
            <DocRow
              key={doc.documentId}
              doc={doc}
              isSelected={doc.documentId === selectedDocId}
              isPrivileged={privilegedDocIds.has(doc.documentId)}
              onClick={() =>
                setSelectedDocId(
                  doc.documentId === selectedDocId ? null : doc.documentId,
                )
              }
            />
          ))}
        </div>
      )}
    </div>
  );

  return (
    <>
      <CaseWorkspaceLayout
        left={left}
        center={
          <DocumentDetail
            doc={selectedDoc}
            isPrivileged={
              selectedDoc !== null &&
              privilegedDocIds.has(selectedDoc.documentId)
            }
            mfaCode={mfaCode}
            reason={reason}
            onMfaChange={setMfaCode}
            onReasonChange={setReason}
            onMarkPrivileged={() => {
              if (!selectedDoc) return;
              setConfirmTarget({ doc: selectedDoc, action: "hold" });
            }}
            onReleaseHold={() => {
              if (!selectedDoc) return;
              setConfirmTarget({ doc: selectedDoc, action: "release" });
            }}
            actionLoading={legalHoldMutation.isPending}
            lastError={lastError}
          />
        }
      />

      {confirmTarget && (
        <ConfirmDialog
          title={
            confirmTarget.action === "hold"
              ? "Apply Privilege Hold"
              : "Release Privilege Hold"
          }
          message={
            confirmTarget.action === "hold"
              ? "This document will be placed under a legal hold and excluded from standard report exports. The action is permanently recorded in the compliance ledger."
              : "The privilege hold on this document will be released. It may appear in future report exports."
          }
          detail={`"${confirmTarget.doc.filename}"`}
          confirmLabel={
            confirmTarget.action === "hold" ? "Apply Hold" : "Release Hold"
          }
          cancelLabel="Cancel"
          danger={confirmTarget.action === "release"}
          loading={legalHoldMutation.isPending}
          icon={
            confirmTarget.action === "hold" ? (
              <Shield className="w-5 h-5" />
            ) : (
              <ShieldOff className="w-5 h-5" />
            )
          }
          onConfirm={() => {
            legalHoldMutation.mutate(
              {
                docId: confirmTarget.doc.documentId,
                action: confirmTarget.action,
                reason,
                mfaCode,
              },
              { onSettled: () => setConfirmTarget(null) },
            );
          }}
          onCancel={() => setConfirmTarget(null)}
        />
      )}
    </>
  );
}
