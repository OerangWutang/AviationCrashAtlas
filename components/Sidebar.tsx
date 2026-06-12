import { NavLink, useParams } from "react-router-dom";
import {
  ChevronDown,
  LayoutDashboard,
  FileText,
  GitPullRequestArrow,
  Clock,
  Link2,
  FileBarChart,
  Activity,
  AlertTriangle,
  Loader2,
  Upload,
} from "lucide-react";
import { useState } from "react";
import { useCaseList } from "../features/cases/api";
import { useConflictListBySlug } from "../features/conflicts/api";
import type { PublicEventSummary } from "../types/api";

// ── Case switcher ─────────────────────────────────────────────────────────────

function CaseSwitcher({
  currentSlug,
  cases,
  loading,
}: {
  currentSlug: string | undefined;
  cases: PublicEventSummary[];
  loading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const current = cases.find((c) => c.slug === currentSlug);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left text-sm rounded hover:bg-atlas-800/50 transition-colors focus-ring"
      >
        <div className="flex-1 min-w-0">
          {loading ? (
            <div className="flex items-center gap-2">
              <Loader2 className="w-3 h-3 text-atlas-500 animate-spin" />
              <span className="text-2xs text-atlas-500">Loading cases…</span>
            </div>
          ) : (
            <>
              <p className="font-medium text-white truncate text-xs">
                {current?.title ?? "Select a case"}
              </p>
              {current?.eventDate && (
                <p className="text-atlas-400 text-2xs truncate">
                  {current.eventDate}
                  {current.location ? ` · ${current.location}` : ""}
                </p>
              )}
            </>
          )}
        </div>
        <ChevronDown
          className={`w-3.5 h-3.5 text-atlas-400 flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 right-0 top-full mt-1 z-20 bg-atlas-800 border border-atlas-700 rounded shadow-xl max-h-64 overflow-y-auto">
            {cases.map((c) => (
              <NavLink
                key={c.slug}
                to={`/app/cases/${c.slug}`}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  `block px-3 py-2 text-xs transition-colors ${
                    isActive
                      ? "bg-atlas-700 text-white"
                      : "text-atlas-300 hover:bg-atlas-800/80 hover:text-white"
                  }`
                }
              >
                <p className="font-medium truncate">{c.title}</p>
                <p className="text-2xs text-atlas-400 truncate">
                  {c.eventDate}
                  {c.hasUnresolvedConflicts && (
                    <span className="ml-1 text-disputed-400">· has disputes</span>
                  )}
                </p>
              </NavLink>
            ))}
            {cases.length === 0 && !loading && (
              <p className="px-3 py-4 text-xs text-atlas-500 text-center">
                No cases available
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Nav item ──────────────────────────────────────────────────────────────────

interface NavItemProps {
  to: string;
  icon: React.ReactNode;
  label: string;
  end?: boolean;
  badge?: number | null;
}

function NavItem({ to, icon, label, end, badge }: NavItemProps) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center gap-2.5 px-3 py-1.5 text-xs rounded transition-colors ${
          isActive
            ? "bg-atlas-700/80 text-white font-medium"
            : "text-atlas-300 hover:bg-atlas-800/50 hover:text-white"
        }`
      }
    >
      {icon}
      <span className="flex-1">{label}</span>
      {badge != null && badge > 0 && (
        <span className="bg-disputed-500 text-white text-2xs font-medium px-1.5 py-0.5 rounded-full min-w-[1.25rem] text-center">
          {badge}
        </span>
      )}
    </NavLink>
  );
}

const iconSize = "w-3.5 h-3.5";

// ── Sidebar ───────────────────────────────────────────────────────────────────

export function Sidebar() {
  const { slug } = useParams<{ slug: string }>();
  const { data: caseList, isLoading: casesLoading } = useCaseList();
  // Fetch conflicts for the current case to show real open count in the badge
  const { data: conflicts } = useConflictListBySlug(slug);

  const cases = caseList?.items ?? [];
  const caseBase = slug ? `/app/cases/${slug}` : "/app/cases";
  const openConflictCount = conflicts
    ? conflicts.filter((c) => c.status === "OPEN").length
    : null;

  return (
    <aside className="fixed left-0 top-0 bottom-0 w-[var(--sidebar-width)] bg-atlas-900 flex flex-col z-30 border-r border-atlas-800">
      {/* Logo */}
      <div className="px-4 py-3 border-b border-atlas-800">
        <NavLink to="/app" className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-white/10 flex items-center justify-center">
            <span className="text-xs font-bold text-white tracking-tight">A</span>
          </div>
          <span className="text-sm font-semibold text-white tracking-tight">
            Atlas
          </span>
          <span className="text-2xs text-atlas-500 font-medium">Argus</span>
        </NavLink>
      </div>

      {/* Case switcher */}
      <div className="px-2 py-2 border-b border-atlas-800">
        <p className="px-3 pb-1.5 text-2xs font-medium text-atlas-500 uppercase tracking-wider">
          Active case
        </p>
        <CaseSwitcher
          currentSlug={slug}
          cases={cases}
          loading={casesLoading}
        />
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
        {slug ? (
          <>
            <div className="pb-1">
              <p className="px-3 pb-1 text-2xs font-medium text-atlas-500 uppercase tracking-wider">
                Case
              </p>
            </div>
            <NavItem
              to={caseBase}
              end
              icon={<LayoutDashboard className={iconSize} />}
              label="Dashboard"
            />
            <NavItem
              to={`${caseBase}/claims`}
              icon={<FileText className={iconSize} />}
              label="Claims"
            />
            <NavItem
              to={`${caseBase}/conflicts`}
              icon={<GitPullRequestArrow className={iconSize} />}
              label="Conflicts"
              badge={openConflictCount}
            />
            <NavItem
              to={`${caseBase}/timeline`}
              icon={<Clock className={iconSize} />}
              label="Timeline"
            />
            <NavItem
              to={`${caseBase}/provenance`}
              icon={<Link2 className={iconSize} />}
              label="Provenance"
            />
            <NavItem
              to={`${caseBase}/reports`}
              icon={<FileBarChart className={iconSize} />}
              label="Reports"
            />

            <div className="pt-3 pb-1">
              <p className="px-3 pb-1 text-2xs font-medium text-atlas-500 uppercase tracking-wider">
                System
              </p>
            </div>
            <NavItem
              to="/app/documents"
              icon={<Upload className={iconSize} />}
              label="Documents"
            />
            <NavItem
              to={`${caseBase}/audit`}
              icon={<Activity className={iconSize} />}
              label="Audit log"
            />
          </>
        ) : (
          <div className="px-3 py-6 text-xs text-atlas-500 text-center">
            <AlertTriangle className="w-5 h-5 mx-auto mb-2 text-atlas-600" />
            Select a case to begin
          </div>
        )}
      </nav>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-atlas-800">
        <p className="text-2xs text-atlas-600 leading-relaxed">
          Atlas is an evidence-organization tool, not a provider of legal advice.
        </p>
      </div>
    </aside>
  );
}
