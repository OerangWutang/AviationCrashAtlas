import { AlertTriangle, X } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Confirmation dialog for destructive or legally significant actions.
 *
 * Renders an overlay with a title, message, confirm/cancel buttons, and an
 * optional danger variant that highlights the destructive nature of the action.
 *
 * Usage:
 *   const [showConfirm, setShowConfirm] = useState(false);
 *   // ...
 *   {showConfirm && (
 *     <ConfirmDialog
 *       title="Delete evidence document"
 *       message="This will permanently remove the document and its extracted text. This action cannot be undone."
 *       danger
 *       onConfirm={handleDelete}
 *       onCancel={() => setShowConfirm(false)}
 *     />
 *   )}
 */
export function ConfirmDialog({
  title,
  message,
  detail,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  loading = false,
  icon,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  loading?: boolean;
  icon?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/30" onClick={loading ? undefined : onCancel} />
      {/* Dialog */}
      <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
        {/* Close button */}
        <button
          onClick={loading ? undefined : onCancel}
          className="absolute top-3 right-3 text-atlas-400 hover:text-atlas-600"
          aria-label="Close"
          disabled={loading}
        >
          <X className="w-4 h-4" />
        </button>

        {/* Icon */}
        <div className={`w-10 h-10 rounded-full flex items-center justify-center mb-4 ${
          danger ? "bg-red-100 text-red-600" : "bg-atlas-100 text-atlas-600"
        }`}>
          {icon ?? <AlertTriangle className="w-5 h-5" />}
        </div>

        {/* Title */}
        <h3 className="text-sm font-semibold text-atlas-900 mb-2">{title}</h3>

        {/* Message */}
        <p className="text-xs text-atlas-600 leading-relaxed mb-1">{message}</p>

        {/* Optional detail */}
        {detail && (
          <p className="text-xs text-atlas-400 leading-relaxed mb-4">{detail}</p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={loading ? undefined : onCancel}
            disabled={loading}
            className="px-4 py-2 text-xs font-medium text-atlas-700 bg-atlas-50 rounded-md hover:bg-atlas-100 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            onClick={loading ? undefined : onConfirm}
            disabled={loading}
            className={`px-4 py-2 text-xs font-medium rounded-md disabled:opacity-50 ${
              danger
                ? "bg-red-600 text-white hover:bg-red-700"
                : "bg-atlas-800 text-white hover:bg-atlas-900"
            }`}
          >
            {loading ? "Processing…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}