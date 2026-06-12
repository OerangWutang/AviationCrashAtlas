import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

/** Honest, plain empty state. No fluff. */
export function EmptyState({
  icon,
  title,
  description,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-8 text-center">
      <div className="text-atlas-300 mb-3">
        {icon ?? <Inbox className="w-10 h-10" strokeWidth={1.25} />}
      </div>
      <p className="text-sm font-medium text-atlas-700">{title}</p>
      {description && (
        <p className="text-xs text-atlas-500 mt-1 max-w-xs">{description}</p>
      )}
    </div>
  );
}

/** Skeleton rows for dense table loading state. */
export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="animate-pulse space-y-0">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 px-4 py-3 border-b border-atlas-100"
        >
          <div className="h-3 bg-atlas-100 rounded w-32" />
          <div className="h-3 bg-atlas-100 rounded w-16" />
          <div className="h-3 bg-atlas-100 rounded w-24" />
          <div className="flex-1" />
          <div className="h-3 bg-atlas-100 rounded w-20" />
        </div>
      ))}
    </div>
  );
}

/** Skeleton for detail panels. */
export function DetailSkeleton() {
  return (
    <div className="animate-pulse p-5 space-y-4">
      <div className="h-5 bg-atlas-100 rounded w-48" />
      <div className="h-3 bg-atlas-100 rounded w-64" />
      <div className="space-y-2 mt-6">
        <div className="h-3 bg-atlas-100 rounded w-full" />
        <div className="h-3 bg-atlas-100 rounded w-3/4" />
        <div className="h-3 bg-atlas-100 rounded w-5/6" />
      </div>
    </div>
  );
}

/** Inline error with retry. */
export function ErrorPanel({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3 bg-destructive-50 border border-destructive-200 rounded text-sm">
      <AlertTriangle className="w-4 h-4 text-destructive-500 mt-0.5 flex-shrink-0" />
      <div className="flex-1">
        <p className="text-destructive-700">{message}</p>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-1 text-xs text-destructive-600 hover:text-destructive-700 font-medium focus-ring rounded px-2 py-1"
        >
          <RefreshCw className="w-3 h-3" />
          Retry
        </button>
      )}
    </div>
  );
}

/** The 409 conflict-modified banner — a designed state, not a toast. */
export function ConflictModifiedBanner({
  message,
  modifierReason,
  onDismiss,
}: {
  message: string;
  modifierReason?: string | null;
  onDismiss?: () => void;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3 bg-disputed-50 border border-disputed-200 rounded text-sm">
      <AlertTriangle className="w-4 h-4 text-disputed-600 mt-0.5 flex-shrink-0" />
      <div className="flex-1 space-y-1">
        <p className="text-disputed-800 font-medium">
          This conflict changed since you opened it
        </p>
        <p className="text-disputed-700 text-xs">{message}</p>
        {modifierReason && (
          <p className="text-disputed-600 text-xs">
            Reason: {modifierReason}
          </p>
        )}
        <p className="text-disputed-600 text-xs">
          Review the updated data below and retry your action.
        </p>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-disputed-500 hover:text-disputed-700 text-xs font-medium focus-ring rounded px-2 py-1"
        >
          Dismiss
        </button>
      )}
    </div>
  );
}
