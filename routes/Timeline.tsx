import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useCaseTimeline, useCaseDetail } from "../features/cases/api";
import { CaseWorkspaceLayout } from "../components/CaseWorkspaceLayout";
import { EmptyState, ErrorPanel, TableSkeleton, DetailSkeleton } from "../components/Feedback";
import { Clock, AlertTriangle } from "lucide-react";
import type { TimelineEvent } from "../types/api";

const eventTypeLabels: Record<string, string> = {
  SCHEDULED_DEPARTURE: "Scheduled departure",
  ACTUAL_DEPARTURE: "Actual departure",
  TAKEOFF: "Takeoff",
  LAST_CONTACT: "Last contact",
  EMERGENCY_DECLARED: "Emergency declared",
  IMPACT: "Impact",
  LANDING: "Landing",
  RESCUE_STARTED: "Rescue started",
  INVESTIGATION_OPENED: "Investigation opened",
  REPORT_PUBLISHED: "Report published",
};

const precisionLabels: Record<string, string> = {
  EXACT: "Exact",
  MINUTE: "± minute",
  HOUR: "± hour",
  DAY: "± day",
  APPROXIMATE: "Approximate",
  RELATIVE: "Relative",
  UNKNOWN: "Unknown precision",
};

function TimelineList({
  events,
  selectedIdx,
  onSelect,
  disputedFields,
}: {
  events: TimelineEvent[];
  selectedIdx: number | null;
  onSelect: (idx: number) => void;
  disputedFields: string[];
}) {
  return (
    <div>
      <div className="px-4 py-3 border-b border-atlas-200 bg-atlas-50/50">
        <h2 className="text-xs font-semibold text-atlas-700">
          Timeline ({events.length} events)
        </h2>
      </div>

      {events.map((evt, idx) => {
        // Check if underlying fields are disputed
        const hasUncertainty =
          evt.timestampPrecision !== "EXACT" ||
          disputedFields.some((f) =>
            evt.eventType.toLowerCase().includes(f.toLowerCase()),
          );

        return (
          <button
            key={idx}
            onClick={() => onSelect(idx)}
            className={`w-full text-left px-4 py-3 border-b border-atlas-100 transition-colors ${
              selectedIdx === idx
                ? "bg-atlas-100/80"
                : "hover:bg-atlas-50/50"
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              {/* Timeline dot */}
              <div
                className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  hasUncertainty ? "bg-disputed-400" : "bg-atlas-400"
                }`}
              />
              <span className="text-xs font-medium text-atlas-800">
                {eventTypeLabels[evt.eventType] ?? evt.eventType}
              </span>
              {hasUncertainty && (
                <AlertTriangle className="w-3 h-3 text-disputed-500" />
              )}
            </div>

            <div className="pl-4 text-2xs text-atlas-500 space-y-0.5">
              {evt.occurredAt && (
                <p>
                  {new Date(evt.occurredAt).toLocaleString()}
                  <span className="text-atlas-400 ml-1">
                    ({precisionLabels[evt.timestampPrecision] ?? evt.timestampPrecision})
                  </span>
                </p>
              )}
              {evt.description && (
                <p className="text-atlas-600 truncate">{evt.description}</p>
              )}
              {evt.sequenceIndex !== null && (
                <p className="text-atlas-400">
                  Sequence #{evt.sequenceIndex}
                </p>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function TimelineDetail({ event }: { event: TimelineEvent }) {
  return (
    <div className="p-5">
      <h2 className="text-sm font-semibold text-atlas-900 mb-2">
        {eventTypeLabels[event.eventType] ?? event.eventType}
      </h2>

      <div className="space-y-4">
        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
            Timestamp
          </p>
          <p className="text-sm text-atlas-800">
            {event.occurredAt
              ? new Date(event.occurredAt).toLocaleString()
              : "Not recorded"}
          </p>
          <p className="text-xs text-atlas-500 mt-0.5">
            Precision:{" "}
            {precisionLabels[event.timestampPrecision] ??
              event.timestampPrecision}
          </p>
        </div>

        {event.description && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
              Description
            </p>
            <p className="text-sm text-atlas-700 leading-relaxed">
              {event.description}
            </p>
          </div>
        )}

        {event.sequenceIndex !== null && (
          <div>
            <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
              Sequence position
            </p>
            <p className="text-sm text-atlas-800">#{event.sequenceIndex}</p>
          </div>
        )}

        <div>
          <p className="text-2xs font-medium text-atlas-500 uppercase tracking-wider mb-1">
            Event type
          </p>
          <p className="text-xs font-mono text-atlas-600">
            {event.eventType}
          </p>
        </div>
      </div>
    </div>
  );
}

export default function Timeline() {
  const { slug } = useParams<{ slug: string }>();
  const { data: detail } = useCaseDetail(slug);
  const { data: timeline, isLoading, isError, refetch } = useCaseTimeline(slug);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  const events = timeline?.events ?? [];
  const disputedFields = detail?.unresolvedConflictFields ?? [];
  const selected =
    selectedIdx !== null && selectedIdx >= 0 && selectedIdx < events.length
      ? events[selectedIdx]
      : null;

  useEffect(() => {
    if (events.length === 0) {
      setSelectedIdx(null);
    } else if (selectedIdx === null || selectedIdx >= events.length) {
      setSelectedIdx(0);
    }
  }, [events.length, selectedIdx]);

  if (isLoading) {
    return (
      <CaseWorkspaceLayout
        left={<TableSkeleton rows={8} />}
        center={<DetailSkeleton />}
      />
    );
  }

  if (isError) {
    return (
      <CaseWorkspaceLayout
        left={
          <div className="p-4">
            <ErrorPanel
              message="Failed to load timeline events for this case."
              onRetry={() => void refetch()}
            />
          </div>
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            Timeline unavailable
          </div>
        }
      />
    );
  }

  if (events.length === 0) {
    return (
      <CaseWorkspaceLayout
        left={
          <EmptyState
            icon={<Clock className="w-8 h-8" strokeWidth={1.25} />}
            title="No timeline events for this case"
            description="Timeline events are reconstructed from ingested evidence via Chronos."
          />
        }
        center={
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            No timeline data available
          </div>
        }
      />
    );
  }

  return (
    <CaseWorkspaceLayout
      left={
        <TimelineList
          events={events}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
          disputedFields={disputedFields}
        />
      }
      center={
        selected ? (
          <TimelineDetail event={selected} />
        ) : (
          <div className="flex items-center justify-center h-full text-xs text-atlas-500">
            Select a timeline event to view details
          </div>
        )
      }
    />
  );
}
