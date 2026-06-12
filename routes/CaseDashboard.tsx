import { useParams, Link } from "react-router-dom";
import { useCaseAudit, useCaseDetail, useCaseTimeline } from "../features/cases/api";
import { useConflictListBySlug } from "../features/conflicts/api";
import { ConfidenceChip } from "../components/StatusChip";
import { DetailSkeleton, ErrorPanel } from "../components/Feedback";
import { FullWidthLayout } from "../components/CaseWorkspaceLayout";
import {
  FileText,
  GitPullRequestArrow,
  Clock,
  CheckCircle2,
  AlertTriangle,
  BarChart3,
  ShieldCheck,
  ListChecks,
} from "lucide-react";

function MetricCard({
  label,
  value,
  icon,
  accent,
  to,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  accent?: "disputed" | "resolved" | "default";
  to?: string;
}) {
  const accentColors = {
    disputed: "border-l-disputed-400",
    resolved: "border-l-resolved-400",
    default: "border-l-atlas-300",
  };

  const content = (
    <div
      className={`bg-white border border-atlas-200 rounded p-4 border-l-4 ${accentColors[accent ?? "default"]} ${to ? "hover:bg-atlas-50/50 transition-colors cursor-pointer" : ""}`}
    >
      <div className="flex items-center gap-2 mb-2">
        <span className="text-atlas-400">{icon}</span>
        <span className="text-2xs font-medium text-atlas-500 uppercase tracking-wider">
          {label}
        </span>
      </div>
      <p className="text-2xl font-semibold text-atlas-900 tabular-nums">
        {value}
      </p>
    </div>
  );

  if (to) return <Link to={to}>{content}</Link>;
  return content;
}

export default function CaseDashboard() {
  const { slug } = useParams<{ slug: string }>();
  const { data: detail, isLoading, error, refetch } = useCaseDetail(slug);
  const { data: conflicts } = useConflictListBySlug(slug ?? undefined);
  const { data: audit } = useCaseAudit(slug);
  const { data: timeline } = useCaseTimeline(slug);

  if (isLoading) {
    return (
      <FullWidthLayout>
        <div className="max-w-5xl mx-auto p-6">
          <DetailSkeleton />
        </div>
      </FullWidthLayout>
    );
  }

  if (error) {
    return (
      <FullWidthLayout>
        <div className="max-w-5xl mx-auto p-6">
          <ErrorPanel
            message="Failed to load case data."
            onRetry={() => refetch()}
          />
        </div>
      </FullWidthLayout>
    );
  }

  if (!detail) return null;

  const openConflicts =
    conflicts?.filter((c) => c.status === "OPEN").length ?? 0;
  const resolvedConflicts =
    conflicts?.filter((c) => c.status === "RESOLVED").length ?? 0;
  const totalFields = detail.fields ? Object.keys(detail.fields).length : 0;
  const disputedFields = detail.unresolvedConflictFields.length;
  const auditDisputedFields =
    audit?.fields.filter((field) => field.isDisputed).length ?? disputedFields;
  const auditOverrideFields =
    audit?.fields.filter((field) => field.isManuallyOverridden).length ?? 0;
  const timelineEventCount = timeline?.events.length ?? 0;
  const nextActions = [
    openConflicts > 0
      ? {
          label: "Resolve open conflicts",
          description: `${openConflicts} conflict${openConflicts === 1 ? "" : "s"} still affect the canonical record.`,
          to: slug ? `/app/cases/${slug}/conflicts` : "#",
        }
      : null,
    auditDisputedFields > 0
      ? {
          label: "Review disputed audit fields",
          description: `${auditDisputedFields} field${auditDisputedFields === 1 ? "" : "s"} need source-level explanation review.`,
          to: slug ? `/app/cases/${slug}/audit` : "#",
        }
      : null,
    timelineEventCount === 0
      ? {
          label: "Verify timeline extraction",
          description: "No timeline events are currently published for this case.",
          to: slug ? `/app/cases/${slug}/timeline` : "#",
        }
      : null,
  ].filter((action): action is { label: string; description: string; to: string } => action !== null);

  return (
    <FullWidthLayout>
      <div className="max-w-5xl mx-auto p-6">
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-atlas-900">
              {detail.editorial.title}
            </h1>
            <ConfidenceChip confidence={detail.confidence} />
          </div>
          {detail.editorial.shortSummary && (
            <p className="text-sm text-atlas-600 mt-1 max-w-2xl">
              {detail.editorial.shortSummary}
            </p>
          )}
          <div className="flex items-center gap-4 mt-2 text-xs text-atlas-500">
            {"event_date" in detail.fields && detail.fields.event_date != null && (
              <span>{String(detail.fields.event_date)}</span>
            )}
            {"location" in detail.fields && detail.fields.location != null && (
              <span>{String(detail.fields.location)}</span>
            )}
            {"operator" in detail.fields && detail.fields.operator != null && (
              <span>{String(detail.fields.operator)}</span>
            )}
            <span>v{detail.projectionVersion}</span>
          </div>
        </div>

        {/* Metrics */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <MetricCard
            label="Open conflicts"
            value={openConflicts}
            icon={<AlertTriangle className="w-4 h-4" />}
            accent={openConflicts > 0 ? "disputed" : "default"}
            to={slug ? `/app/cases/${slug}/conflicts` : undefined}
          />
          <MetricCard
            label="Resolved"
            value={resolvedConflicts}
            icon={<CheckCircle2 className="w-4 h-4" />}
            accent="resolved"
          />
          <MetricCard
            label="Audit risk"
            value={auditDisputedFields + auditOverrideFields}
            icon={<ShieldCheck className="w-4 h-4" />}
            accent={auditDisputedFields > 0 ? "disputed" : "default"}
            to={slug ? `/app/cases/${slug}/audit` : undefined}
          />
          <MetricCard
            label="Timeline events"
            value={timelineEventCount}
            icon={<Clock className="w-4 h-4" />}
            to={slug ? `/app/cases/${slug}/timeline` : undefined}
          />
        </div>

        {/* Quick links */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <Link
            to={slug ? `/app/cases/${slug}/conflicts` : "#"}
            className="flex items-center gap-3 p-4 bg-white border border-atlas-200 rounded hover:bg-atlas-50/50 transition-colors"
          >
            <GitPullRequestArrow className="w-5 h-5 text-atlas-400" />
            <div>
              <p className="text-sm font-medium text-atlas-800">
                Conflict queue
              </p>
              <p className="text-xs text-atlas-500">
                {openConflicts > 0
                  ? `${openConflicts} conflict${openConflicts > 1 ? "s" : ""} awaiting resolution`
                  : "No open conflicts — review resolved items"}
              </p>
            </div>
          </Link>

          <Link
            to={slug ? `/app/cases/${slug}/audit` : "#"}
            className="flex items-center gap-3 p-4 bg-white border border-atlas-200 rounded hover:bg-atlas-50/50 transition-colors"
          >
            <ShieldCheck className="w-5 h-5 text-atlas-400" />
            <div>
              <p className="text-sm font-medium text-atlas-800">
                Evidence audit
              </p>
              <p className="text-xs text-atlas-500">
                {auditDisputedFields > 0
                  ? `${auditDisputedFields} disputed field${auditDisputedFields > 1 ? "s" : ""} need explanation review`
                  : "Filter review fields and inspect source explanations"}
              </p>
            </div>
          </Link>

          <Link
            to={slug ? `/app/cases/${slug}/timeline` : "#"}
            className="flex items-center gap-3 p-4 bg-white border border-atlas-200 rounded hover:bg-atlas-50/50 transition-colors"
          >
            <Clock className="w-5 h-5 text-atlas-400" />
            <div>
              <p className="text-sm font-medium text-atlas-800">Timeline</p>
              <p className="text-xs text-atlas-500">
                {timelineEventCount > 0
                  ? `${timelineEventCount} event${timelineEventCount === 1 ? "" : "s"} in the reconstructed sequence`
                  : "Reconstructed sequence from source evidence"}
              </p>
            </div>
          </Link>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_20rem] gap-4">
          <section className="bg-white border border-atlas-200 rounded p-4">
            <div className="flex items-center gap-2 mb-3">
              <ListChecks className="w-4 h-4 text-atlas-400" />
              <h2 className="text-sm font-semibold text-atlas-900">Next actions</h2>
            </div>
            {nextActions.length > 0 ? (
              <div className="space-y-2">
                {nextActions.map((action) => (
                  <Link
                    key={action.label}
                    to={action.to}
                    className="block rounded border border-atlas-200 p-3 hover:bg-atlas-50/60 transition-colors"
                  >
                    <p className="text-xs font-medium text-atlas-800">{action.label}</p>
                    <p className="text-xs text-atlas-500 mt-0.5">{action.description}</p>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="text-xs text-atlas-500">
                No blocking review items are visible. Continue with report and provenance review.
              </p>
            )}
          </section>

          <section className="bg-white border border-atlas-200 rounded p-4">
            <div className="flex items-center gap-2 mb-3">
              <BarChart3 className="w-4 h-4 text-atlas-400" />
              <h2 className="text-sm font-semibold text-atlas-900">Record health</h2>
            </div>
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-atlas-500">Completeness</span>
                  <span className="font-medium text-atlas-800">
                    {Math.round(detail.completenessScore * 100)}%
                  </span>
                </div>
                <div className="h-2 rounded bg-atlas-100 overflow-hidden">
                  <div
                    className="h-full bg-atlas-600"
                    style={{ width: `${Math.round(detail.completenessScore * 100)}%` }}
                  />
                </div>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-atlas-500">Total fields</span>
                <Link
                  to={slug ? `/app/cases/${slug}/claims` : "#"}
                  className="font-medium text-atlas-700 hover:text-atlas-900"
                >
                  {totalFields}
                </Link>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-atlas-500">Manual overrides</span>
                <span className="font-medium text-atlas-800">{auditOverrideFields}</span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </FullWidthLayout>
  );
}
