import { CLAIM_TYPES, type ClaimType } from '../types';

/**
 * Format a UTC-safe ISO date string (e.g. last_projected_at, system timestamps).
 * Uses the browser's timezone — ONLY call this for real UTC timestamps.
 * Do NOT use for accident occurrence dates (use formatOccurrenceDate instead).
 */
export function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

/**
 * Format an occurrence date string (YYYY-MM-DD) without any timezone conversion.
 * "2023-01-04" in UTC is midnight UTC, which in e.g. US/Pacific is Jan 3.
 * We parse the components manually to avoid that off-by-one day shift.
 */
export function formatDateOnly(value: string | null): string {
  if (!value) return '—';
  const parts = value.split('-');
  if (parts.length !== 3) return value;
  const [y, m, d] = parts;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const monthName = months[parseInt(m, 10) - 1];
  if (!monthName) return value;
  return `${monthName} ${parseInt(d, 10)}, ${y}`;
}

export function confColor(score: number): string {
  if (score >= 0.90) return '#1D9E75';
  if (score >= 0.70) return '#378ADD';
  if (score >= 0.50) return '#BA7517';
  return '#E24B4A';
}

/**
 * Canonical label thresholds — must match confidence/engine.py:
 *   ≥0.90 → Well sourced, ≥0.70 → Mostly sourced, ≥0.50 → Partially sourced, else Weakly sourced
 *
 * These labels say "sourced" intentionally — the score measures data/source
 * completeness, not factual truth-probability.
 */
export function confLabelClass(label: string): string {
  // Labels match confidence/engine.py — source completeness language
  switch (label) {
    case 'Well sourced':       return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'Mostly sourced':     return 'bg-blue-50 text-blue-700 border-blue-200';
    case 'Partially sourced':  return 'bg-amber-50 text-amber-700 border-amber-200';
    case 'Weakly sourced':     return 'bg-red-50 text-red-700 border-red-200';
    // Legacy fallbacks during transition
    case 'High':    return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    case 'Good':    return 'bg-blue-50 text-blue-700 border-blue-200';
    case 'Partial': return 'bg-amber-50 text-amber-700 border-amber-200';
    default:        return 'bg-red-50 text-red-700 border-red-200';
  }
}

// Keep old name for components that import it
export const confBg = confLabelClass;

export const SEV_COLOR: Record<string, string> = {
  FATAL: '#E24B4A', SERIOUS: '#EF9F27', MINOR: '#639922',
  NONE: '#888780', UNKNOWN: '#888780',
};

export const SEV_BG: Record<string, string> = {
  FATAL:   'bg-red-50 text-red-700 border-red-200',
  SERIOUS: 'bg-amber-50 text-amber-700 border-amber-200',
  MINOR:   'bg-green-50 text-green-700 border-green-200',
  NONE:    'bg-stone-50 text-stone-500 border-stone-200',
  UNKNOWN: 'bg-stone-50 text-stone-400 border-stone-200',
};

export const CLAIM_TYPE_BG: Record<ClaimType, string> = {
  confirmed:  'bg-emerald-50 text-emerald-700',
  inferred:   'bg-amber-50 text-amber-700',
  disputed:   'bg-red-50 text-red-700',
  rejected:   'bg-stone-200 text-stone-500 line-through',
  superseded: 'bg-stone-100 text-stone-400',
  pending:    'bg-stone-50 text-stone-500',
};

export function claimTypeLabel(type: ClaimType): string {
  switch (type) {
    case 'confirmed': return 'Confirmed';
    case 'inferred': return 'Inferred';
    case 'disputed': return 'Disputed';
    case 'rejected': return 'Rejected';
    case 'superseded': return 'Superseded';
    case 'pending': return 'Pending';
    default: {
      const exhaustive: never = type;
      return exhaustive;
    }
  }
}

// Runtime guard for defensive API rendering. The static ClaimType union should
// already be exhaustive, but raw JSON can still contain stale/invalid values
// before TypeScript sees them.
export function claimTypeBg(type: string): string {
  return (CLAIM_TYPES as readonly string[]).includes(type)
    ? CLAIM_TYPE_BG[type as ClaimType]
    : 'bg-stone-50 text-stone-400';
}

export function aircraftLabel(make: string | null, model: string | null): string {
  if (make && model) return `${make} ${model}`;
  return make ?? model ?? 'Unknown aircraft';
}

export function claimValueDisplay(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export const PHASES = [
  'TAKEOFF', 'CLIMB', 'CRUISE', 'DESCENT',
  'APPROACH', 'LANDING', 'MANEUVERING', 'STANDING', 'TAXI',
];

export const FIELD_LABELS: Record<string, string> = {
  occurred_at: 'Date & time', occurred_at_precision: 'Date precision',
  location_text: 'Location', location_coordinates: 'Coordinates',
  country_code: 'Country', state_code: 'State',
  aircraft_make: 'Aircraft make', aircraft_model: 'Aircraft model',
  aircraft_registration: 'Registration', aircraft_amateur_built: 'Amateur built',
  engine_type: 'Engine type', num_engines: 'Engines',
  operator_name: 'Operator', phase_of_flight: 'Phase of flight',
  purpose_of_flight: 'Purpose', weather_condition: 'Weather',
  injury_severity: 'Injury severity', aircraft_damage: 'Aircraft damage',
  fatalities_total: 'Total fatalities',
  fatalities_crew: 'Crew fatalities', fatalities_passengers: 'Passenger fatalities',
  serious_injuries: 'Serious injuries',
  serious_injuries_crew: 'Crew serious injuries',
  serious_injuries_passengers: 'Passenger serious injuries',
  minor_injuries: 'Minor injuries',
  minor_injuries_crew: 'Crew minor injuries',
  minor_injuries_passengers: 'Passenger minor injuries',
  uninjured_crew: 'Uninjured crew', uninjured_passengers: 'Uninjured passengers',
  aboard_total: 'Total aboard',
  investigation_status: 'Investigation status', probable_cause: 'Probable cause',
  ntsb_report_number: 'Report number',
};

/**
 * Format an accident occurrence date/time while honestly representing
 * how precise the value is.
 *
 * Returns { date, qualifier } where:
 *   date      — the formatted date/time string
 *   qualifier — a short label explaining the precision, or null if exact
 *
 * Rules:
 *   precision "exact"  → show date + time, qualifier = "local time, tz unknown"
 *   precision "day"    → show date only, qualifier = "date only"
 *   precision "year"   → show year only, qualifier = "year only"
 *   precision null/""  → fall back gracefully, qualifier = "precision unknown"
 *
 * NEVER display a day-precision value as if it were an exact timestamp.
 * NEVER display a naive local time with a UTC label.
 */
export function formatOccurrenceDate(
  occurred_at: string | null,
  occurred_date: string | null,
  occurred_year: number | null,
  precision: string | null,
): { date: string; qualifier: string | null } {
  const prec = precision ?? '';

  if (prec === 'exact' && occurred_at) {
    // occurred_at is a local time string with unknown timezone.
    // We must NOT pass it to `new Date()` because:
    //   - "2023-01-04T22:30:00Z" → JS treats it as UTC, then converts to viewer tz
    //   - "2023-01-04T22:30:00"  → JS treats it as local viewer tz
    // Neither is the accident site timezone. Parse the date+time components
    // directly from the string so no timezone conversion happens at all.
    const raw = occurred_at.replace('Z', '');
    const [datePart, timePart] = raw.split('T');
    if (datePart && timePart) {
      const [y, m, d] = datePart.split('-');
      const hhmm = timePart.slice(0, 5);
      const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const monthName = monthNames[parseInt(m, 10) - 1] ?? m;
      return { date: `${monthName} ${parseInt(d, 10)}, ${y}, ${hhmm}`, qualifier: 'local time, tz unknown' };
    }
    // Fallback: just show the date part
    if (occurred_date) return { date: formatDateOnly(occurred_date), qualifier: 'date only' };
  }

  if ((prec === 'day' || prec === 'exact') && occurred_date) {
    const date = formatDateOnly(occurred_date);
    return { date, qualifier: 'date only' };
  }

  if (occurred_year) {
    return { date: String(occurred_year), qualifier: 'year only' };
  }

  if (occurred_date) {
    return { date: formatDateOnly(occurred_date), qualifier: 'precision unknown' };
  }

  return { date: '—', qualifier: null };
}
