import type { ReactNode } from "react";

/**
 * Three-column case working layout:
 * - Left (list rail): working set for the screen — sortable table
 * - Center (focus): selected item detail + action area
 * - Right (evidence): shared evidence/provenance panel
 *
 * The right panel collapses to a toggle on narrow viewports.
 */
export function CaseWorkspaceLayout({
  left,
  center,
  right,
  rightOpen = true,
}: {
  left: ReactNode;
  center: ReactNode;
  right?: ReactNode;
  rightOpen?: boolean;
}) {
  return (
    <div className="flex h-[calc(100vh-var(--topbar-height))]">
      {/* Left rail — list/table */}
      <div className="w-80 flex-shrink-0 border-r border-atlas-100 bg-white overflow-y-auto">
        {left}
      </div>

      {/* Center — detail/action */}
      <div className="flex-1 overflow-y-auto bg-white">
        {center}
      </div>

      {/* Right — evidence panel */}
      {right && rightOpen && (
        <div className="w-80 flex-shrink-0 border-l border-atlas-100 bg-atlas-50/50 overflow-y-auto">
          {right}
        </div>
      )}
    </div>
  );
}

/** Simple full-width page layout for screens that don't need 3 columns. */
export function FullWidthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="h-[calc(100vh-var(--topbar-height))] overflow-y-auto">
      {children}
    </div>
  );
}
