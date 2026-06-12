import { NavLink } from "react-router-dom";
import { useCaseList } from "../features/cases/api";
import { ConfidenceChip, StatusChip } from "../components/StatusChip";
import { EmptyState, TableSkeleton, ErrorPanel } from "../components/Feedback";
import { FullWidthLayout } from "../components/CaseWorkspaceLayout";
import { Briefcase, AlertTriangle } from "lucide-react";

export default function CaseList() {
  const { data, isLoading, error, refetch } = useCaseList();

  return (
    <FullWidthLayout>
      <div className="max-w-5xl mx-auto p-6">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-atlas-900">Cases</h1>
          <p className="text-xs text-atlas-500 mt-0.5">
            Select a case to view its reconciled record, conflicts, and evidence
            chain.
          </p>
        </div>

        {error && (
          <div className="mb-4">
            <ErrorPanel
              message="Failed to load cases."
              onRetry={() => refetch()}
            />
          </div>
        )}

        {isLoading && <TableSkeleton rows={4} />}

        {data && data.items.length === 0 && (
          <EmptyState
            icon={<Briefcase className="w-10 h-10" strokeWidth={1.25} />}
            title="No cases available"
            description="Cases appear here once events are published. Contact your administrator to seed data."
          />
        )}

        {data && data.items.length > 0 && (
          <div className="bg-white rounded border border-atlas-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-atlas-500 font-medium border-b border-atlas-200 bg-atlas-50/50">
                  <th className="px-4 py-2.5">Case</th>
                  <th className="px-4 py-2.5">Date</th>
                  <th className="px-4 py-2.5">Location</th>
                  <th className="px-4 py-2.5">Operator</th>
                  <th className="px-4 py-2.5">Confidence</th>
                  <th className="px-4 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.slug} className="table-row-dense">
                    <td className="px-4 py-2.5">
                      <NavLink
                        to={`/app/cases/${c.slug}`}
                        className="font-medium text-atlas-800 hover:text-atlas-600 transition-colors"
                      >
                        {c.title}
                      </NavLink>
                      {c.aircraftType && (
                        <p className="text-2xs text-atlas-500 mt-0.5">
                          {c.aircraftType}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-atlas-600 whitespace-nowrap">
                      {c.eventDate ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-atlas-600">
                      {c.location ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-atlas-600">
                      {c.operator ?? "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <ConfidenceChip confidence={c.confidence} />
                    </td>
                    <td className="px-4 py-2.5">
                      {c.hasUnresolvedConflicts ? (
                        <span className="inline-flex items-center gap-1 chip chip-disputed">
                          <AlertTriangle className="w-3 h-3" />
                          Has disputes
                        </span>
                      ) : (
                        <StatusChip status="RESOLVED" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </FullWidthLayout>
  );
}
