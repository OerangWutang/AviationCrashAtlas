/**
 * Documents route — upload PDFs, view parse status, trigger ingestion.
 *
 * Route: /app/documents
 * This screen is the source-intake surface. It's separate from the case-specific
 * routes because a document may be uploaded without knowing its event yet.
 */
import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { FullWidthLayout } from "../components/CaseWorkspaceLayout";
import { EmptyState, TableSkeleton, ErrorPanel } from "../components/Feedback";
import { RoleGate } from "../components/RoleGate";
import {
  Upload,
  FileText,
  CheckCircle2,
  AlertTriangle,
  Clock,
  RefreshCw,
  Eye,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface UploadedDocument {
  documentId: string;
  filename: string;
  sizeBytes: number;
  parseStatus: "uploaded" | "parsing" | "parsed" | "parse_failed" | "ingesting" | "ingested" | "ingest_failed";
  pageCount: number | null;
  parseNote: string | null;
  sourceId: string | null;
  eventId: string | null;
  createdAt: string;
  updatedAt: string;
}

interface UploadResponse {
  documentId: string;
  filename: string;
  sizeBytes: number;
  contentSha256: string;
  pageCount: number | null;
  parseStatus: string;
  parseNote: string | null;
  sourceId: string | null;
  eventId: string | null;
  createdAt: string;
}

// ── API hooks ─────────────────────────────────────────────────────────────────

const docKeys = {
  list: () => ["documents", "list"] as const,
};

function useDocumentList() {
  return useQuery({
    queryKey: docKeys.list(),
    queryFn: () =>
      apiFetch<{ documents: UploadedDocument[]; total: number }>("/api/documents"),
  });
}

function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/documents", {
        method: "POST",
        credentials: "include",
        body: formData,
        // Don't set Content-Type — browser sets it with boundary
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: "Upload failed" }));
        throw Object.assign(new Error(err.error || "Upload failed"), { status: response.status });
      }
      return response.json() as Promise<UploadResponse>;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: docKeys.list() });
    },
  });
}

// ── Status chip ───────────────────────────────────────────────────────────────

const statusConfig: Record<string, { label: string; classes: string; icon: React.ReactNode }> = {
  uploaded: { label: "Uploaded", classes: "chip bg-atlas-50 text-atlas-700 border border-atlas-200", icon: <Clock className="w-3 h-3" /> },
  parsing: { label: "Parsing…", classes: "chip bg-atlas-50 text-atlas-700 border border-atlas-200", icon: <RefreshCw className="w-3 h-3 animate-spin" /> },
  parsed: { label: "Parsed", classes: "chip chip-resolved", icon: <CheckCircle2 className="w-3 h-3" /> },
  parse_failed: { label: "Parse failed", classes: "chip bg-destructive-50 text-destructive-700 border border-destructive-200", icon: <AlertTriangle className="w-3 h-3" /> },
  ingesting: { label: "Ingesting…", classes: "chip bg-atlas-50 text-atlas-700 border border-atlas-200", icon: <RefreshCw className="w-3 h-3 animate-spin" /> },
  ingested: { label: "Ingested", classes: "chip chip-resolved", icon: <CheckCircle2 className="w-3 h-3" /> },
  ingest_failed: { label: "Ingest failed", classes: "chip bg-destructive-50 text-destructive-700 border border-destructive-200", icon: <AlertTriangle className="w-3 h-3" /> },
};

function DocStatusChip({ status }: { status: string }) {
  const config = statusConfig[status] ?? statusConfig["uploaded"]!;
  return (
    <span className={`${config.classes} inline-flex items-center gap-1`}>
      {config.icon}
      {config.label}
    </span>
  );
}

// ── Upload dropzone ───────────────────────────────────────────────────────────

function UploadZone({ onUpload, isPending }: { onUpload: (file: File) => void; isPending: boolean }) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      alert("Only PDF files are supported.");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      alert("File too large. Maximum is 50 MB.");
      return;
    }
    onUpload(file);
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files[0];
        if (file) handleFile(file);
      }}
      onClick={() => inputRef.current?.click()}
      className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
        dragOver
          ? "border-atlas-500 bg-atlas-50"
          : "border-atlas-200 hover:border-atlas-400 hover:bg-atlas-50/50"
      } ${isPending ? "pointer-events-none opacity-60" : ""}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
          e.target.value = "";
        }}
      />
      <Upload className="w-8 h-8 text-atlas-300 mx-auto mb-3" strokeWidth={1.5} />
      {isPending ? (
        <p className="text-sm text-atlas-600">Uploading…</p>
      ) : (
        <>
          <p className="text-sm font-medium text-atlas-700">Drop a PDF here or click to browse</p>
          <p className="text-xs text-atlas-500 mt-1">PDF documents only · Max 50 MB</p>
        </>
      )}
    </div>
  );
}

// ── Main Documents page ───────────────────────────────────────────────────────

export default function Documents() {
  const { data, isLoading, error, refetch } = useDocumentList();
  const uploadMutation = useUploadDocument();
  const [lastUpload, setLastUpload] = useState<UploadResponse | null>(null);

  const documents = data?.documents ?? [];

  const handleUpload = (file: File) => {
    setLastUpload(null);
    uploadMutation.mutate(file, {
      onSuccess: (result) => setLastUpload(result),
    });
  };

  return (
    <FullWidthLayout>
      <div className="max-w-4xl mx-auto p-6">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-atlas-900">Documents</h1>
          <p className="text-xs text-atlas-500 mt-0.5">
            Upload PDF dockets, hearing transcripts, and source documents.
            Text is extracted conservatively and submitted as evidence claims.
          </p>
        </div>

        {/* Upload zone */}
        <RoleGate
          permission="canUploadDocuments"
          fallback={
            <div className="border border-atlas-200 rounded-lg p-6 text-center text-xs text-atlas-500 mb-6">
              Reviewer or admin role required to upload documents.
            </div>
          }
        >
          <div className="mb-6">
            <UploadZone onUpload={handleUpload} isPending={uploadMutation.isPending} />

            {/* Upload result */}
            {uploadMutation.isSuccess && lastUpload && (
              <div className={`mt-3 flex items-start gap-3 px-4 py-3 rounded border text-xs ${
                lastUpload.parseStatus === "parse_failed"
                  ? "bg-destructive-50 border-destructive-200 text-destructive-700"
                  : "bg-resolved-50 border-resolved-200 text-resolved-700"
              }`}>
                {lastUpload.parseStatus === "parse_failed"
                  ? <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  : <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
                }
                <div>
                  <p className="font-medium">{lastUpload.filename}</p>
                  <p className="mt-0.5">
                    {lastUpload.parseStatus === "parse_failed"
                      ? `Parse failed: ${lastUpload.parseNote ?? "Unknown error"}`
                      : `Parsed successfully · ${lastUpload.pageCount ?? "?"} pages`
                    }
                  </p>
                  {lastUpload.parseStatus !== "parse_failed" && (
                    <p className="text-2xs mt-1 opacity-75">
                      SHA-256: {lastUpload.contentSha256.slice(0, 16)}…
                    </p>
                  )}
                </div>
              </div>
            )}

            {uploadMutation.isError && (
              <div className="mt-3">
                <ErrorPanel
                  message="Upload failed. Check the file is a valid PDF under 50 MB."
                  onRetry={() => uploadMutation.reset()}
                />
              </div>
            )}
          </div>
        </RoleGate>

        {/* Document list */}
        <div>
          <h2 className="text-xs font-semibold text-atlas-700 mb-3 uppercase tracking-wider">
            Uploaded documents ({data?.total ?? 0})
          </h2>

          {error && (
            <ErrorPanel message="Failed to load documents." onRetry={() => refetch()} />
          )}
          {isLoading && <TableSkeleton rows={4} />}

          {!isLoading && documents.length === 0 && (
            <EmptyState
              icon={<FileText className="w-8 h-8" strokeWidth={1.25} />}
              title="No documents uploaded yet"
              description="Upload PDF dockets and source documents to extract evidence claims."
            />
          )}

          {documents.length > 0 && (
            <div className="border border-atlas-200 rounded overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-atlas-50/50 text-left text-2xs text-atlas-500 font-medium border-b border-atlas-200">
                    <th className="px-4 py-2.5">Filename</th>
                    <th className="px-4 py-2.5">Pages</th>
                    <th className="px-4 py-2.5">Size</th>
                    <th className="px-4 py-2.5">Status</th>
                    <th className="px-4 py-2.5">Uploaded</th>
                  </tr>
                </thead>
                <tbody>
                  {documents.map((doc) => (
                    <tr key={doc.documentId} className="table-row-dense">
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <FileText className="w-3.5 h-3.5 text-atlas-400 flex-shrink-0" />
                          <span className="text-xs font-medium text-atlas-800 truncate max-w-[200px]">
                            {doc.filename}
                          </span>
                        </div>
                        {doc.parseNote && doc.parseStatus.includes("failed") && (
                          <p className="text-2xs text-destructive-600 mt-0.5 pl-5 truncate max-w-[200px]">
                            {doc.parseNote}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-atlas-600">
                        {doc.pageCount ?? "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-atlas-600 whitespace-nowrap">
                        {doc.sizeBytes > 1024 * 1024
                          ? `${(doc.sizeBytes / (1024 * 1024)).toFixed(1)} MB`
                          : `${Math.round(doc.sizeBytes / 1024)} KB`}
                      </td>
                      <td className="px-4 py-2.5">
                        <DocStatusChip status={doc.parseStatus} />
                      </td>
                      <td className="px-4 py-2.5 text-xs text-atlas-500 whitespace-nowrap">
                        {new Date(doc.createdAt).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </FullWidthLayout>
  );
}
