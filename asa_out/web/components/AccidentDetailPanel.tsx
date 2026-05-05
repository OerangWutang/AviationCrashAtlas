import { useEffect, useState } from "react";
import type {
  AccidentDetail,
  AccidentProvenance,
  Claim,
  DocumentStatus,
  SourceDocument,
} from "../types";
import {
  formatDate,
  formatOccurrenceDate,
  aircraftLabel,
  SEV_BG,
} from "../lib/utils";
import ProvenancePanel from "./ProvenancePanel";
import ErrorBoundary from "./ErrorBoundary";
import { Chip } from "./SectionHelpers";
import { MetricCard, Panel, SectionHeader, StatusBadge } from "./UI";
import DisputedSection from "./AccidentDisputeSection";
import AccidentTimelineSection from "./AccidentTimelineSection";
import AccidentIncidentTimeline from "./AccidentIncidentTimeline";
import FlightPathMapPanel from "./FlightPathMapPanel";
import AccidentWeatherPanel from "./AccidentWeatherPanel";
import AccidentSystemFailuresPanel from "./AccidentSystemFailuresPanel";
import SimilarAccidentsPanel from "./SimilarAccidentsPanel";
import AccidentSourcesSection from "./AccidentSourcesSection";

type Tab = "overview" | "technical";

interface Props {
  accident: AccidentDetail;
  provenance: AccidentProvenance | null;
  loadingProvenance: boolean;
  /** Called after a conflict is resolved so the parent can re-fetch provenance. */
  onProvenanceRefresh?: () => void;
  /** Reviewer API key forwarded to the resolve endpoint. */
  apiKey?: string;
  /** Initial tab requested by a deep link, e.g. from the conflict review queue. */
  initialTab?: Tab;
}

// ─── Field-level status inference ─────────────────────────────────────────────
// "Confirmed" is reserved for fields where there IS a winning claim from a
// suitable source.  A final investigation status DOES NOT promote everything
// else to confirmed — the prompt is explicit on this point.  When provenance
// has not yet been loaded we emit a distinct "Source not loaded" state instead
// of optimistically labelling fields confirmed.
type FieldStatus =
  | "Confirmed"
  | "Preliminary"
  | "Approximate"
  | "Inferred"
  | "Disputed"
  | "Rejected"
  | "Missing"
  | "Superseded"
  | "Unverified"
  | "Source not loaded";

// Field groups — disputes or missing values in any member of a group should
// influence the group's display.  Used by the key-facts panel to avoid
// e.g. labelling "Aircraft = Cessna 172" Confirmed when only `aircraft_make`
// has a winning claim and `aircraft_model` is missing.
const FIELD_GROUPS = {
  date: [
    "occurred_at",
    "occurred_date",
    "occurred_year",
    "occurred_at_precision",
  ],
  location: [
    "location_text",
    "location_city",
    "location_state",
    "location_country",
    "latitude",
    "longitude",
    "location_coordinates",
  ],
  aircraft: ["aircraft_make", "aircraft_model", "aircraft_registration"],
  cause: ["probable_cause", "cause_summary", "contributing_factors"],
  report: ["report_status", "investigation_status"],
  injuries: [
    "fatalities_total",
    "fatalities_crew",
    "fatalities_passengers",
    "serious_injuries",
    "serious_injuries_crew",
    "serious_injuries_passengers",
    "minor_injuries",
    "minor_injuries_crew",
    "minor_injuries_passengers",
    "uninjured_crew",
    "uninjured_passengers",
  ],
} as const;

/**
 * Per-field status resolution. Order matters:
 *   1. open conflict → Disputed (highest priority)
 *   2. missing/empty value → Missing
 *   3. provenance not loaded → Source not loaded
 *   4. winning claim found → Confirmed/Inferred/etc. based on claim_type
 *   5. no winning claim, no conflict, value present → Unverified
 *
 * We deliberately never use a final-report shortcut to "Confirmed".
 */
function fieldStatus(
  fieldName: string,
  value: string | null | undefined,
  provenance: AccidentProvenance | null,
): FieldStatus {
  const isMissing =
    value == null ||
    value === "" ||
    value === "—" ||
    value === "null" ||
    value === "undefined";
  const hasOpenConflict = provenance?.conflicts.some(
    (c) => c.status === "open" && c.field_name === fieldName,
  );
  // Disputed should win over Missing — a field that has conflicting source
  // values but is currently withheld is "Disputed", not just "Missing".
  if (hasOpenConflict) return "Disputed";
  if (isMissing) return "Missing";
  if (!provenance) return "Source not loaded";
  const winning = provenance.claims.find(
    (c) => c.is_winning && c.field_name === fieldName,
  );
  if (winning) {
    if (winning.claim_type === "inferred") return "Inferred";
    if (winning.claim_type === "superseded") return "Superseded";
    if (winning.claim_type === "pending") return "Preliminary";
    if (winning.claim_type === "disputed") return "Disputed";
    if (winning.claim_type === "rejected") return "Rejected";
    // claim_type === 'confirmed'
    return "Confirmed";
  }
  // Value displayed but no winning claim supports it.  Possible reasons:
  // older snapshot, parser-only inference, or pre-v20 record without
  // projection explanations.  Either way, not Confirmed.
  return "Unverified";
}

/**
 * Group-aware status. Returns Disputed if any field in the group is
 * disputed, Missing if all are missing, otherwise the worst-but-non-empty
 * status across the group.
 */
function groupStatus(
  fields: readonly string[],
  values: readonly (string | null | undefined)[],
  provenance: AccidentProvenance | null,
): FieldStatus {
  const statuses = fields.map((f, i) => fieldStatus(f, values[i], provenance));
  if (statuses.includes("Disputed")) return "Disputed";
  if (statuses.every((s) => s === "Missing")) return "Missing";
  // Walk severity order and pick the most concerning non-Missing status.
  const order: FieldStatus[] = [
    "Disputed",
    "Rejected",
    "Source not loaded",
    "Unverified",
    "Preliminary",
    "Inferred",
    "Superseded",
    "Approximate",
    "Confirmed",
  ];
  for (const s of order) {
    if (statuses.includes(s)) return s;
  }
  return "Missing";
}

function locationStatus(
  accident: AccidentDetail,
  provenance: AccidentProvenance | null,
): FieldStatus {
  const base = groupStatus(
    FIELD_GROUPS.location,
    [
      accident.location_text,
      accident.location_text,
      accident.location_text,
      accident.location_text,
      accident.location_lat != null ? String(accident.location_lat) : null,
      accident.location_lon != null ? String(accident.location_lon) : null,
      accident.location_lat != null && accident.location_lon != null
        ? "present"
        : null,
    ],
    provenance,
  );
  // NTSB CSV reports nearest-city location, not exact crash coordinates.
  // When the underlying data is fine BUT we know it is city-granularity
  // only, surface that explicitly rather than calling it Confirmed.
  if (
    base === "Confirmed" &&
    (accident.location_lat == null || accident.location_lon == null)
  ) {
    return "Approximate";
  }
  return base;
}

// ─── Status pill ──────────────────────────────────────────────────────────────
const STATUS_STYLES: Record<FieldStatus, string> = {
  Confirmed: "bg-emerald-50 text-emerald-700 border-emerald-200",
  Preliminary: "bg-amber-50 text-amber-700 border-amber-200",
  Approximate: "bg-sky-50 text-sky-700 border-sky-200",
  Inferred: "bg-violet-50 text-violet-700 border-violet-200",
  Disputed: "bg-red-50 text-red-700 border-red-200",
  Rejected: "bg-stone-200 text-stone-600 border-stone-300 line-through",
  Missing: "bg-stone-50 text-stone-400 border-stone-200",
  Superseded: "bg-stone-100 text-stone-400 border-stone-200",
  Unverified: "bg-stone-100 text-stone-500 border-stone-200",
  "Source not loaded": "bg-stone-100 text-stone-400 border-stone-200",
};

function StatusPill({ status }: { status: FieldStatus }) {
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-[9px] font-medium ${STATUS_STYLES[status]}`}
      style={{ fontFamily: "var(--ff-mono)" }}
    >
      {status}
    </span>
  );
}

// ─── Document status helpers ──────────────────────────────────────────────────
//
// The backend may or may not provide AccidentDetail.document_status (it's
// nullable for forward-compat with older API responses).  When absent we
// derive the same label from the SourceDocument array we already have so
// the UI never silently degrades to "verified" when nothing has been
// verified.
function deriveDocumentStatus(docs: SourceDocument[]): DocumentStatus {
  if (docs.length === 0) return "none_linked";
  let verifiedCount = 0;
  let unavailableCount = 0;
  for (const d of docs) {
    if (d.url_verified && d.is_available) verifiedCount++;
    if (d.is_available === false) unavailableCount++;
  }
  if (verifiedCount === docs.length) return "verified";
  if (unavailableCount === docs.length) return "unavailable";
  if (verifiedCount > 0 || unavailableCount > 0) return "mixed";
  return "linked_unverified";
}

const DOCUMENT_STATUS_LABEL: Record<DocumentStatus, string> = {
  none_linked: "None linked",
  linked_unverified: "Linked, unverified",
  verified: "Verified",
  unavailable: "Unavailable",
  mixed: "Mixed",
};

const DOCUMENT_STATUS_HIGHLIGHT: Record<
  DocumentStatus,
  "green" | "amber" | "red" | "neutral"
> = {
  none_linked: "neutral",
  linked_unverified: "amber",
  verified: "green",
  unavailable: "red",
  mixed: "amber",
};

// ─── Human summary ────────────────────────────────────────────────────────────
function humanSummary(accident: AccidentDetail): string {
  const aircraft = aircraftLabel(
    accident.aircraft_make,
    accident.aircraft_model,
  );
  const { date } = formatOccurrenceDate(
    accident.occurred_at,
    accident.occurred_date,
    accident.occurred_year,
    accident.occurred_at_precision,
  );
  const loc = accident.location_text ?? "an unspecified location";
  const phase = accident.phase_of_flight
    ? ` during ${accident.phase_of_flight.toLowerCase()}`
    : "";
  const damage = accident.aircraft_damage
    ? `. The aircraft sustained ${accident.aircraft_damage.toLowerCase()} damage`
    : "";
  const sevParts: string[] = [];
  if ((accident.fatalities_total ?? 0) > 0)
    sevParts.push(
      `${accident.fatalities_total} fatal${accident.fatalities_total === 1 ? "ity" : "ities"}`,
    );
  if ((accident.serious_injuries ?? 0) > 0)
    sevParts.push(
      `${accident.serious_injuries} serious injur${accident.serious_injuries === 1 ? "y" : "ies"}`,
    );
  if ((accident.minor_injuries ?? 0) > 0)
    sevParts.push(
      `${accident.minor_injuries} minor injur${accident.minor_injuries === 1 ? "y" : "ies"}`,
    );
  if (accident.injury_severity === "NONE")
    sevParts.push("no injuries reported");
  const sevStr = sevParts.length > 0 ? `. ${sevParts.join(", ")}` : "";
  return `On ${date}, a ${aircraft} was involved in an accident${phase} near ${loc}${damage}${sevStr}.`;
}

// ─── Evidence status bar ──────────────────────────────────────────────────────
function EvidenceStatusBar({
  accident,
  provenance,
}: {
  accident: AccidentDetail;
  provenance: AccidentProvenance | null;
}) {
  const isFinal =
    accident.investigation_status === "final" ||
    accident.investigation_status === "closed";
  const openConflicts =
    provenance?.conflicts.filter((c) => c.status === "open") ?? [];
  const disputedFields = [
    ...new Set(openConflicts.map((c) => c.field_name.replace(/_/g, " "))),
  ];

  // Document status: prefer backend-computed, fall back to deriving from
  // the documents we have. Never hardcode "verified".
  const docStatus: DocumentStatus =
    accident.document_status ??
    (provenance
      ? deriveDocumentStatus(provenance.source_documents)
      : "none_linked");

  return (
    <div className="grid gap-3 rounded-2xl border border-stone-200 bg-white p-4 text-[11px] shadow-sm shadow-stone-200/40 sm:grid-cols-2 xl:grid-cols-5">
      <StatusChip
        label="Report status"
        value={isFinal ? "Final report available" : "Preliminary — may change"}
        highlight={!isFinal ? "amber" : "green"}
      />
      <StatusChip
        label="Disputed fields"
        value={openConflicts.length > 0 ? String(openConflicts.length) : "None"}
        highlight={openConflicts.length > 0 ? "red" : "green"}
      />
      <StatusChip
        label="Sources"
        value={`${accident.claim_source_count} contributing`}
        highlight="neutral"
      />
      <StatusChip
        label="Documents"
        value={DOCUMENT_STATUS_LABEL[docStatus]}
        highlight={DOCUMENT_STATUS_HIGHLIGHT[docStatus]}
      />
      {/* Renamed from "Last updated" — last_projected_at reflects local
          projection rebuilds, not source data changes. */}
      <StatusChip
        label="Record rebuilt"
        value={formatDate(accident.last_projected_at)}
        highlight="neutral"
      />
      {disputedFields.length > 0 && (
        <div
          className="w-full text-[10px] text-stone-400 mt-0.5"
          style={{ fontFamily: "var(--ff-mono)" }}
        >
          Disputed:{" "}
          <span className="text-stone-600">{disputedFields.join(", ")}</span>
        </div>
      )}
    </div>
  );
}

function StatusChip({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight: "green" | "amber" | "red" | "neutral";
}) {
  const valColor =
    highlight === "green"
      ? "text-emerald-700"
      : highlight === "amber"
        ? "text-amber-700"
        : highlight === "red"
          ? "text-red-700"
          : "text-stone-700";
  return (
    <div className="flex flex-col gap-0.5 rounded-xl bg-stone-50 px-3 py-2">
      <span
        className="text-[9px] text-stone-400 uppercase tracking-wide"
        style={{ fontFamily: "var(--ff-mono)" }}
      >
        {label}
      </span>
      <span className={`text-[12px] font-medium ${valColor}`}>{value}</span>
    </div>
  );
}

// ─── Key facts grid ───────────────────────────────────────────────────────────
function FactRow({
  label,
  value,
  status,
  why,
}: {
  label: string;
  value: string | null | undefined;
  status: FieldStatus;
  why?: string;
}) {
  return (
    <div className="flex min-h-[104px] flex-col gap-2 rounded-xl border border-stone-200 bg-white p-3 shadow-sm shadow-stone-200/40">
      <div className="flex items-center justify-between gap-1">
        <div
          className="text-[10px] text-stone-400 uppercase tracking-wide"
          style={{ fontFamily: "var(--ff-mono)" }}
        >
          {label}
        </div>
        <StatusPill status={status} />
      </div>
      <div className="text-[14px] font-semibold leading-snug text-stone-900">
        {value == null || value === "" ? "—" : value}
      </div>
      {why && status !== "Confirmed" && (
        <div
          className="text-[10px] text-stone-400 leading-tight"
          style={{ fontFamily: "var(--ff-mono)" }}
        >
          {why}
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function AccidentDetailPanel({
  accident,
  provenance,
  loadingProvenance,
  onProvenanceRefresh,
  apiKey,
  initialTab = "overview",
}: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [showSources, setShowSources] = useState(false);

  useEffect(() => {
    setTab(initialTab);
  }, [initialTab, accident.id]);

  const aircraft = aircraftLabel(
    accident.aircraft_make,
    accident.aircraft_model,
  );
  const sevBg = SEV_BG[accident.injury_severity ?? "UNKNOWN"] ?? SEV_BG.UNKNOWN;
  const isFinal =
    accident.investigation_status === "final" ||
    accident.investigation_status === "closed";

  const { date, qualifier } = formatOccurrenceDate(
    accident.occurred_at,
    accident.occurred_date,
    accident.occurred_year,
    accident.occurred_at_precision,
  );
  const dateDisplay = qualifier ? `${date} (${qualifier})` : date;
  const openConflicts =
    provenance?.conflicts.filter((c) => c.status === "open") ?? [];

  const fs = (field: string, val: string | null | undefined) =>
    fieldStatus(field, val, provenance);

  const dateStatus = groupStatus(
    FIELD_GROUPS.date,
    [
      accident.occurred_at,
      accident.occurred_date,
      accident.occurred_year != null ? String(accident.occurred_year) : null,
      accident.occurred_at_precision,
    ],
    provenance,
  );
  const aircraftStatus = groupStatus(
    FIELD_GROUPS.aircraft,
    [
      accident.aircraft_make,
      accident.aircraft_model,
      accident.aircraft_registration,
    ],
    provenance,
  );
  const investigationStatus: FieldStatus =
    accident.investigation_status == null
      ? "Missing"
      : isFinal
        ? fs("investigation_status", accident.investigation_status)
        : "Preliminary";

  return (
    <div className="flex h-full flex-col bg-stone-50/60">
      {/* Header */}
      <div className="border-b border-stone-200 bg-gradient-to-br from-white via-white to-blue-50/40 p-4 sm:p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="text-[10px] uppercase tracking-[0.18em] text-stone-400 font-mono">
                {accident.canonical_id}
              </span>
              <StatusBadge tone={isFinal ? "green" : "amber"}>
                {isFinal ? "Final investigation" : "Preliminary record"}
              </StatusBadge>
              {openConflicts.length > 0 && (
                <StatusBadge tone="purple">
                  {openConflicts.length} open dispute
                  {openConflicts.length !== 1 ? "s" : ""}
                </StatusBadge>
              )}
            </div>
            <h2
              className="text-[28px] leading-tight text-stone-950 sm:text-[34px]"
              style={{ fontFamily: "var(--ff-serif)" }}
            >
              {aircraft}
            </h2>
            <div className="mt-1 text-[15px] text-stone-600">
              {accident.location_text ?? "Location not available"}
            </div>
            <p className="mt-4 max-w-3xl text-[14px] leading-relaxed text-stone-700">
              {humanSummary(accident)}
            </p>
            <div className="mt-4 flex flex-wrap gap-2 items-center">
              <Chip>{dateDisplay}</Chip>
              {accident.phase_of_flight && (
                <Chip>{accident.phase_of_flight}</Chip>
              )}
              {accident.injury_severity && (
                <span
                  className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${sevBg}`}
                >
                  {accident.injury_severity === "FATAL"
                    ? `${accident.fatalities_total ?? 0} fatal`
                    : accident.injury_severity}
                </span>
              )}
              {accident.aircraft_damage && (
                <Chip>{accident.aircraft_damage} damage</Chip>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:w-[520px] xl:grid-cols-2">
            <MetricCard
              label="Fatalities"
              value={(accident.fatalities_total ?? 0).toLocaleString()}
              sub="total reported"
              tone={(accident.fatalities_total ?? 0) > 0 ? "red" : "green"}
            />
            <MetricCard
              label="Aboard"
              value={
                accident.aboard_total != null
                  ? accident.aboard_total.toLocaleString()
                  : "—"
              }
              sub="crew + passengers"
            />
            <MetricCard
              label="Completeness"
              value={`${Math.round(accident.confidence.score * 100)}%`}
              sub={accident.confidence.label}
              tone={
                accident.confidence.score >= 0.9
                  ? "green"
                  : accident.confidence.score >= 0.7
                    ? "blue"
                    : accident.confidence.score >= 0.5
                      ? "amber"
                      : "red"
              }
            />
            <MetricCard
              label="Sources"
              value={`${accident.winning_source_count}/${accident.claim_source_count}`}
              sub="projected / contributing"
            />
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-stone-200 bg-white px-4 sm:px-6">
        {(["overview", "technical"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`min-h-11 px-3 text-[12px] border-b-2 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
              tab === t
                ? "border-[#185FA5] text-[#185FA5] font-medium"
                : "border-transparent text-stone-400 hover:text-stone-600"
            }`}
            style={{ fontFamily: "var(--ff-mono)", marginBottom: "-1px" }}
          >
            {t === "overview" ? "Overview" : "Technical provenance"}
            {t === "technical" && provenance && (
              <span className="ml-1.5 text-[9px] bg-stone-200 text-stone-500 rounded px-1">
                {provenance.claims.filter((c) => c.is_winning).length} claims
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {tab === "overview" && (
          <>
            <div className="mb-6">
              <EvidenceStatusBar accident={accident} provenance={provenance} />
            </div>
            {accident.probable_cause && (
              <Panel className="mb-6">
                <SectionHeader
                  eyebrow="Narrative"
                  title={
                    <>
                      Probable cause
                      {!isFinal && (
                        <span className="text-[12px] text-amber-600">
                          {" "}
                          · preliminary
                        </span>
                      )}
                    </>
                  }
                  description="Projected narrative; field-level provenance remains explicit below."
                />
                <div className="rounded-xl border-l-4 border-[#185FA5] bg-blue-50/50 px-4 py-3 text-[13px] leading-relaxed text-stone-700">
                  {accident.probable_cause}
                </div>
                {accident.contributing_factors &&
                  accident.contributing_factors.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {accident.contributing_factors.map((f, i) => (
                        <StatusBadge key={i} tone="neutral">
                          {f}
                        </StatusBadge>
                      ))}
                    </div>
                  )}
              </Panel>
            )}
            <Panel className="mb-6">
              <SectionHeader
                eyebrow="Projected values"
                title="Key facts"
                description="Each field keeps its own provenance status; final reports do not automatically confirm unsupported fields."
              />
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <FactRow
                  label="Date"
                  value={dateDisplay}
                  status={dateStatus}
                  why={qualifier ? `Precision: ${qualifier}` : undefined}
                />
                <FactRow
                  label="Location"
                  value={accident.location_text}
                  status={locationStatus(accident, provenance)}
                  why="NTSB provides nearest city, not exact crash site."
                />
                <FactRow
                  label="Aircraft"
                  value={aircraft}
                  status={aircraftStatus}
                />
                <FactRow
                  label="Registration"
                  value={accident.aircraft_registration}
                  status={fs(
                    "aircraft_registration",
                    accident.aircraft_registration,
                  )}
                />
                <FactRow
                  label="Operator"
                  value={accident.operator_name}
                  status={fs("operator_name", accident.operator_name)}
                />
                <FactRow
                  label="Phase of flight"
                  value={accident.phase_of_flight}
                  status={fs("phase_of_flight", accident.phase_of_flight)}
                />
                <FactRow
                  label="Weather"
                  value={accident.weather_condition}
                  status={fs("weather_condition", accident.weather_condition)}
                />
                <FactRow
                  label="Purpose"
                  value={accident.purpose_of_flight}
                  status={fs("purpose_of_flight", accident.purpose_of_flight)}
                />
                <FactRow
                  label="Aboard"
                  value={
                    accident.aboard_total != null
                      ? String(accident.aboard_total)
                      : null
                  }
                  status={fs(
                    "aboard_total",
                    accident.aboard_total != null
                      ? String(accident.aboard_total)
                      : null,
                  )}
                />
                <FactRow
                  label="Fatalities"
                  value={
                    accident.fatalities_total != null
                      ? String(accident.fatalities_total)
                      : null
                  }
                  status={fs(
                    "fatalities_total",
                    accident.fatalities_total != null
                      ? String(accident.fatalities_total)
                      : null,
                  )}
                />
                <FactRow
                  label="Fatalities — crew"
                  value={
                    accident.fatalities_crew != null
                      ? String(accident.fatalities_crew)
                      : null
                  }
                  status={fs(
                    "fatalities_crew",
                    accident.fatalities_crew != null
                      ? String(accident.fatalities_crew)
                      : null,
                  )}
                />
                <FactRow
                  label="Fatalities — passengers"
                  value={
                    accident.fatalities_passengers != null
                      ? String(accident.fatalities_passengers)
                      : null
                  }
                  status={fs(
                    "fatalities_passengers",
                    accident.fatalities_passengers != null
                      ? String(accident.fatalities_passengers)
                      : null,
                  )}
                />
                <FactRow
                  label="Serious injuries"
                  value={
                    accident.serious_injuries != null
                      ? String(accident.serious_injuries)
                      : null
                  }
                  status={fs(
                    "serious_injuries",
                    accident.serious_injuries != null
                      ? String(accident.serious_injuries)
                      : null,
                  )}
                />
                <FactRow
                  label="Serious injuries — crew"
                  value={
                    accident.serious_injuries_crew != null
                      ? String(accident.serious_injuries_crew)
                      : null
                  }
                  status={fs(
                    "serious_injuries_crew",
                    accident.serious_injuries_crew != null
                      ? String(accident.serious_injuries_crew)
                      : null,
                  )}
                />
                <FactRow
                  label="Serious injuries — passengers"
                  value={
                    accident.serious_injuries_passengers != null
                      ? String(accident.serious_injuries_passengers)
                      : null
                  }
                  status={fs(
                    "serious_injuries_passengers",
                    accident.serious_injuries_passengers != null
                      ? String(accident.serious_injuries_passengers)
                      : null,
                  )}
                />
                <FactRow
                  label="Minor injuries"
                  value={
                    accident.minor_injuries != null
                      ? String(accident.minor_injuries)
                      : null
                  }
                  status={fs(
                    "minor_injuries",
                    accident.minor_injuries != null
                      ? String(accident.minor_injuries)
                      : null,
                  )}
                />
                <FactRow
                  label="Minor injuries — crew"
                  value={
                    accident.minor_injuries_crew != null
                      ? String(accident.minor_injuries_crew)
                      : null
                  }
                  status={fs(
                    "minor_injuries_crew",
                    accident.minor_injuries_crew != null
                      ? String(accident.minor_injuries_crew)
                      : null,
                  )}
                />
                <FactRow
                  label="Minor injuries — passengers"
                  value={
                    accident.minor_injuries_passengers != null
                      ? String(accident.minor_injuries_passengers)
                      : null
                  }
                  status={fs(
                    "minor_injuries_passengers",
                    accident.minor_injuries_passengers != null
                      ? String(accident.minor_injuries_passengers)
                      : null,
                  )}
                />
                <FactRow
                  label="Uninjured — crew"
                  value={
                    accident.uninjured_crew != null
                      ? String(accident.uninjured_crew)
                      : null
                  }
                  status={fs(
                    "uninjured_crew",
                    accident.uninjured_crew != null
                      ? String(accident.uninjured_crew)
                      : null,
                  )}
                />
                <FactRow
                  label="Uninjured — passengers"
                  value={
                    accident.uninjured_passengers != null
                      ? String(accident.uninjured_passengers)
                      : null
                  }
                  status={fs(
                    "uninjured_passengers",
                    accident.uninjured_passengers != null
                      ? String(accident.uninjured_passengers)
                      : null,
                  )}
                />
                <FactRow
                  label="Aircraft damage"
                  value={accident.aircraft_damage}
                  status={fs("aircraft_damage", accident.aircraft_damage)}
                />
                <FactRow
                  label="Investigation"
                  value={accident.investigation_status?.toUpperCase()}
                  status={investigationStatus}
                />
              </div>
            </Panel>
            {provenance && openConflicts.length > 0 && (
              <DisputedSection provenance={provenance} />
            )}
            <FlightPathMapPanel accidentEventId={accident.id} />
            <AccidentIncidentTimeline accidentEventId={accident.id} />
            <AccidentWeatherPanel accidentEventId={accident.id} />
            <AccidentSystemFailuresPanel accidentEventId={accident.id} />
            <SimilarAccidentsPanel accidentEventId={accident.id} />
            {provenance && (
              <AccidentTimelineSection
                provenance={provenance}
                accident={accident}
              />
            )}
            {provenance && (
              <Panel className="mb-6">
                <button
                  onClick={() => setShowSources((v) => !v)}
                  className="mb-3 flex min-h-10 items-center gap-2 rounded-lg px-2 text-[12px] text-stone-600 transition hover:bg-stone-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 font-mono"
                  aria-expanded={showSources}
                >
                  <span aria-hidden="true">{showSources ? "▾" : "▸"}</span>
                  Sources ({provenance.sources.length})
                </button>
                {showSources && (
                  <AccidentSourcesSection provenance={provenance} />
                )}
              </Panel>
            )}
            {loadingProvenance && !provenance && (
              <div className="space-y-2 mb-4">
                <div className="skeleton h-10 rounded-lg" />
                <div className="skeleton h-10 rounded-lg" />
              </div>
            )}
            <div
              className="text-[10px] text-stone-300 mt-2"
              style={{ fontFamily: "var(--ff-mono)" }}
            >
              Record rebuilt: {formatDate(accident.last_projected_at)} ·{" "}
              {accident.claim_source_count} contributing source
              {accident.claim_source_count !== 1 ? "s" : ""}
              {accident.winning_source_count !==
                accident.claim_source_count && (
                <> · {accident.winning_source_count} used in projected values</>
              )}
            </div>
          </>
        )}

        {tab === "technical" &&
          (loadingProvenance ? (
            <div className="space-y-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="skeleton h-16 rounded-lg" />
              ))}
            </div>
          ) : provenance ? (
            <ErrorBoundary label="Provenance">
              <ProvenancePanel
                provenance={provenance}
                onResolved={onProvenanceRefresh}
                apiKey={apiKey}
              />
            </ErrorBoundary>
          ) : (
            <div className="text-center text-stone-400 py-12 text-[13px]">
              Could not load provenance data
            </div>
          ))}
      </div>
    </div>
  );
}
