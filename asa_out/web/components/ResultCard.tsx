import type { ReactNode } from "react";
import type { AccidentSummary } from "../types";
import { formatOccurrenceDate, aircraftLabel, SEV_BG } from "../lib/utils";
import ConfidenceBadge from "./ConfidenceBadge";
import SeverityDot from "./SeverityDot";
import { StatusBadge, cx } from "./UI";

interface Props {
  accident: AccidentSummary;
  selected: boolean;
  onClick: () => void;
}

export default function ResultCard({ accident, selected, onClick }: Props) {
  const aircraft = aircraftLabel(
    accident.aircraft_make,
    accident.aircraft_model,
  );
  const { date, qualifier } = formatOccurrenceDate(
    accident.occurred_at,
    accident.occurred_date,
    accident.occurred_year,
    accident.occurred_at_precision,
  );
  const isFinal =
    accident.investigation_status === "final" ||
    accident.investigation_status === "closed";
  const severity = accident.injury_severity ?? "UNKNOWN";
  const completeness = `${Math.round(accident.confidence.score * 100)}%`;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cx(
        "group relative mb-2 w-full rounded-xl border p-3 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2",
        selected
          ? "border-blue-300 bg-blue-50/80 shadow-sm shadow-blue-100"
          : "border-stone-200 bg-white hover:border-stone-300 hover:bg-stone-50/60 hover:shadow-sm hover:shadow-stone-200/60",
      )}
    >
      <span
        className={cx(
          "absolute inset-y-3 left-0 w-1 rounded-r-full transition",
          selected ? "bg-[#185FA5]" : "bg-transparent group-hover:bg-stone-200",
        )}
        aria-hidden="true"
      />
      <div className="flex items-start justify-between gap-3 pl-1">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[10px] uppercase tracking-[0.14em] text-stone-400 font-mono">
              {accident.canonical_id}
            </span>
            {accident.has_conflicts && (
              <StatusBadge tone="purple" className="px-1.5 py-0.5">
                Disputed
              </StatusBadge>
            )}
            {isFinal && (
              <StatusBadge tone="green" className="px-1.5 py-0.5">
                Final
              </StatusBadge>
            )}
          </div>
          <div className="truncate text-[14px] font-semibold leading-tight text-stone-900">
            {aircraft}
          </div>
          <div className="mt-1 line-clamp-2 text-[12px] leading-snug text-stone-500">
            {accident.location_text ?? "Location not available"}
          </div>
        </div>
        <ConfidenceBadge confidence={accident.confidence} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 pl-1 text-[11px] text-stone-500 font-mono">
        <Meta
          label="Date"
          value={qualifier ? `${date} · ${qualifier}` : date}
        />
        <Meta
          label="Severity"
          value={
            <span className="inline-flex items-center gap-1.5">
              <SeverityDot severity={accident.injury_severity} />
              <span
                className={cx(
                  "rounded-full border px-1.5 py-0.5 text-[10px]",
                  SEV_BG[severity] ?? SEV_BG.UNKNOWN,
                )}
              >
                {accident.injury_severity === "FATAL"
                  ? `${accident.fatalities_total ?? 0} fatal`
                  : severity}
              </span>
            </span>
          }
        />
        <Meta label="Phase" value={accident.phase_of_flight ?? "—"} />
        <Meta
          label="Sources"
          value={`${completeness} · ${accident.winning_source_count}/${accident.claim_source_count}`}
        />
      </div>
    </button>
  );
}

function Meta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg bg-stone-50 px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-[0.14em] text-stone-400">
        {label}
      </div>
      <div className="mt-0.5 truncate text-[11px] text-stone-700">{value}</div>
    </div>
  );
}
