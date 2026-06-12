import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useCaseDetail } from "../features/cases/api";
import { FullWidthLayout } from "../components/CaseWorkspaceLayout";
import { EmptyState, TableSkeleton, ErrorPanel, DetailSkeleton } from "../components/Feedback";
import { RoleGate } from "../components/RoleGate";
import { apiFetch } from "../lib/api";
import {
  FileBarChart,
  Download,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Eye,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReportSummary {
  reportId: string;
  eventId: string;
  version: number;
  projectionVersion: number;
  contentSha256: string;
  generatedAt: string;
  generatedBy: string | null;
  downloadUrl: string;
}

interface ReportPreview {
  eventId: string;
  projectionVersion: number;
  html: string;
  warnings: string[];
}

interface GenerateReportResponse {
  reportId: string;
  eventId: string;
  version: number;
  projectionVersion: number;
  contentSha256: string;
  generatedAt: string;
  downloadUrl: string;
}

// ── API hooks ─────────────────────────────────────────────────────────────────

const reportKeys = {
  list: (eventId: string) => ["reports", "list", eventId] as const,
  preview: (eventId: string) => ["reports", "preview", eventId] as const,
};

function useReportList(eventId: string | undefined) {
  return useQuery({
    queryKey: reportKeys.list(eventId!),
    queryFn: () =>
      apiFetch<{ reports: ReportSummary[]; total: number }>(
        `/api/reports?event_id=${eventId}`,
      ),
    enabled: !!eventId,
  });
}

function useReportPreview(eventId: string | undefined) {
  return useQuery({
    queryKey: reportKeys.preview(eventId!),
    queryFn: () => apiFetch<ReportPreview>(`/api/reports/${eventId}/preview`),
    enabled: !!eventId,
    staleTime: 30_000,
  });
}

function useGenerateReport(eventId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiFetch<GenerateReportResponse>(`/api/reports/${eventId}/generate`, {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reportKeys.list(eventId) });
      void queryClient.invalidateQueries({ queryKey: reportKeys.preview(eventId) });
    },
  });
}

// ── Report preview panel ──────────────────────────────────────────────────────

function ReportPreviewPanel({ eventId }: { eventId: string }) {
  const { data, isLoading, error } = useReportPreview(eventId);

  if (isLoading) return <DetailSkeleton />;
  if (error) return <ErrorPanel message="Failed to load report preview." />;
  if (!data) return null;

  return (
    <div className="space-y-4">
      {data.warnings.length > 0 && (
        <div className="flex items-start gap-2 px-4 py-3 bg-disputed-50 border border-disputed-200 rounded text-xs text-disputed-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <ul className="space-y-1">
            {data.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
      <div className="flex items-center gap-2 text-2xs text-atlas-500">
        <span>Preview of projection v{data.projectionVersion}</span>
        <span>·</span>
        <span>Not versioned until generated</span>
      </div>
      {/* Render the HTML preview in a sandboxed iframe */}
      <div className="border border-atlas-200 rounded overflow-hidden bg-white">
        <iframe
          srcDoc={data.html}
          className="w-full"
          style={{ height: "600px", border: "none" }}
          sandbox="allow-same-origin"
          title="Report preview"
        />
      </div>
    </div>
  );
}

// ── Report history ────────────────────────────────────────────────────────────

function ReportHistory({
  reports,
  onDownload,
}: {
  reports: ReportSummary[];
  onDownload: (report: ReportSummary, format: "html" | "pdf") => void;
}) {
  if (reports.length === 0) {
    return (
      <p className="text-xs text-atlas-500 py-4">
        No reports generated yet. Generate a report to create a versioned, immutable export.
      </p>
    );
  }

  return (
    <div className="border border-atlas-200 rounded overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-atlas-50/50 text-left text-2xs text-atlas-500 font-medium border-b border-atlas-200">
            <th className="px-4 py-2.5">Version</th>
            <th className="px-4 py-2.5">Projection</th>
            <th className="px-4 py-2.5">Generated</th>
            <th className="px-4 py-2.5">By</th>
            <th className="px-4 py-2.5">Hash</th>
            <th className="px-4 py-2.5"></th>
          </tr>
        </thead>
        <tbody>
          {reports.map((r) => (
            <tr key={r.reportId} className="table-row-dense">
              <td className="px-4 py-2.5">
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-resolved-500 flex-shrink-0" />
                  <span className="font-medium text-atlas-800">v{r.version}</span>
                </span>
              </td>
              <td className="px-4 py-2.5 text-xs text-atlas-600">
                Projection v{r.projectionVersion}
              </td>
              <td className="px-4 py-2.5 text-xs text-atlas-600 whitespace-nowrap">
                {new Date(r.generatedAt).toLocaleString()}
              </td>
              <td className="px-4 py-2.5 text-xs text-atlas-600">
                {r.generatedBy ?? "—"}
              </td>
              <td className="px-4 py-2.5">
                <span className="font-mono text-2xs text-atlas-500">
                  {r.contentSha256.slice(0, 12)}…
                </span>
              </td>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => onDownload(r, "html")}
                    className="flex items-center gap-1 text-2xs text-atlas-600 hover:text-atlas-900 focus-ring rounded px-2 py-1 hover:bg-atlas-50 transition-colors"
                  >
                    <Download className="w-3.5 h-3.5" />
                    HTML
                  </button>
                  <button
                    onClick={() => onDownload(r, "pdf")}
                    className="flex items-center gap-1 text-2xs text-atlas-600 hover:text-atlas-900 focus-ring rounded px-2 py-1 hover:bg-atlas-50 transition-colors"
                  >
                    <Download className="w-3.5 h-3.5" />
                    PDF
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main Reports page ─────────────────────────────────────────────────────────

export default function Reports() {
  const { slug } = useParams<{ slug: string }>();
  const { data: detail } = useCaseDetail(slug);
  const eventId = detail?.canonicalEventId;

  const { data: reportList, isLoading: listLoading } = useReportList(eventId);
  const generateMutation = useGenerateReport(eventId ?? "");
  const [activeTab, setActiveTab] = useState<"preview" | "history">("preview");

  const handleDownload = (report: ReportSummary, format: "html" | "pdf" = "html") => {
    window.open(`/api/reports/${report.reportId}/download?format=${format}`, "_blank");
  };

  const reports = reportList?.reports ?? [];

  return (
    <FullWidthLayout>
      <div className="max-w-4xl mx-auto p-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1 className="text-lg font-semibold text-atlas-900">Reports</h1>
            <p className="text-xs text-atlas-500 mt-0.5">
              Generate court-ready exports with full provenance, resolution history, and audit hashes.
              Each report is versioned and immutable.
            </p>
          </div>
          <RoleGate
            permission="canGenerateReports"
            disabled
            disabledReason="Requires reviewer role to generate reports."
          >
            <button
              onClick={() => generateMutation.mutate()}
              disabled={!eventId || generateMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 bg-atlas-900 text-white text-xs font-medium rounded hover:bg-atlas-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-ring flex-shrink-0"
            >
              {generateMutation.isPending ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  Generating…
                </>
              ) : (
                <>
                  <FileBarChart className="w-3.5 h-3.5" />
                  Generate report
                </>
              )}
            </button>
          </RoleGate>
        </div>

        {/* Case summary */}
        {detail && (
          <div className="bg-white border border-atlas-200 rounded p-4 mb-6 flex items-center gap-3">
            <FileBarChart className="w-5 h-5 text-atlas-400 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-atlas-800 truncate">
                {detail.editorial.title}
              </p>
              <p className="text-xs text-atlas-500 mt-0.5">
                Projection v{detail.projectionVersion} ·{" "}
                {detail.unresolvedConflictFields.length > 0
                  ? `${detail.unresolvedConflictFields.length} disputed field${detail.unresolvedConflictFields.length > 1 ? "s" : ""}`
                  : "No disputes"}
                {" · "}
                {reports.length > 0
                  ? `${reports.length} version${reports.length > 1 ? "s" : ""} generated`
                  : "No versions generated"}
              </p>
            </div>
          </div>
        )}

        {/* Generate success */}
        {generateMutation.isSuccess && (
          <div className="flex items-center gap-2 px-4 py-3 bg-resolved-50 border border-resolved-200 rounded text-xs text-resolved-700 mb-4">
            <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
            Report v{generateMutation.data?.version} generated.
            Hash: <span className="font-mono">{generateMutation.data?.contentSha256.slice(0, 16)}…</span>
            <div className="ml-auto flex items-center gap-3">
              {(["html", "pdf"] as const).map((fmt) => (
                <button
                  key={fmt}
                  onClick={() => generateMutation.data && handleDownload({
                    reportId: generateMutation.data.reportId,
                    eventId: generateMutation.data.eventId,
                    version: generateMutation.data.version,
                    projectionVersion: generateMutation.data.projectionVersion,
                    contentSha256: generateMutation.data.contentSha256,
                    generatedAt: generateMutation.data.generatedAt,
                    generatedBy: null,
                    downloadUrl: generateMutation.data.downloadUrl,
                  }, fmt)}
                  className="flex items-center gap-1 underline hover:no-underline uppercase"
                >
                  <Download className="w-3 h-3" /> {fmt}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-0.5 border-b border-atlas-200 mb-6">
          {([
            { key: "preview", label: "Preview", icon: <Eye className="w-3.5 h-3.5" /> },
            { key: "history", label: `History (${reports.length})`, icon: <Clock className="w-3.5 h-3.5" /> },
          ] as const).map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors -mb-px ${
                activeTab === tab.key
                  ? "border-atlas-700 text-atlas-900"
                  : "border-transparent text-atlas-500 hover:text-atlas-700"
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "preview" && eventId && (
          <ReportPreviewPanel eventId={eventId} />
        )}

        {activeTab === "history" && (
          listLoading ? (
            <TableSkeleton rows={3} />
          ) : (
            <ReportHistory reports={reports} onDownload={handleDownload} />
          )
        )}
      </div>
    </FullWidthLayout>
  );
}
