/**
 * AccidentSystemFailuresPanel
 *
 * Displays mechanical / system failure records for an aviation accident.
 *
 * Design rules (must be preserved in all future UI changes):
 * - NEVER present a suspected or disputed failure as confirmed.
 * - NEVER present any failure as a causal factor unless is_causal_factor is True
 *   and a source claim explicitly supports it.
 * - Ruled-out failures are shown with a distinct visual treatment so users
 *   understand they were investigated and cleared.
 * - The display_note from the API is always shown — it encodes the epistemic status.
 * - Disputed failures always show a prominent red "Disputed" badge.
 *
 * Data source: GET /api/v1/accidents/{id}/system-failures
 */
import { useState, useEffect, useMemo } from 'react';
import type {
  FailureCategory,
  FailureStatus,
  FailureSeverity,
  SystemFailure,
  SystemFailures,
} from '../types';
import { fetchSystemFailures } from '../lib/api';
import { SectionTitle } from './SectionHelpers';

// ── Display helpers ────────────────────────────────────────────────────────────

function statusBadgeClass(status: FailureStatus): string {
  switch (status) {
    case 'confirmed':  return 'bg-red-100 text-red-700 border-red-200';
    case 'reported':   return 'bg-orange-100 text-orange-700 border-orange-200';
    case 'suspected':  return 'bg-amber-100 text-amber-700 border-amber-200';
    case 'disputed':   return 'bg-purple-100 text-purple-700 border-purple-200';
    case 'ruled_out':  return 'bg-stone-100 text-stone-500 border-stone-200 line-through';
    default:           return 'bg-stone-100 text-stone-400 border-stone-200';
  }
}

function statusLabel(status: FailureStatus): string {
  switch (status) {
    case 'confirmed':  return 'Confirmed';
    case 'reported':   return 'Reported';
    case 'suspected':  return 'Suspected';
    case 'disputed':   return 'Disputed';
    case 'ruled_out':  return 'Ruled Out';
    default:           return 'Unknown';
  }
}

function severityBadgeClass(severity: FailureSeverity | null): string {
  switch (severity) {
    case 'catastrophic': return 'bg-red-200 text-red-800';
    case 'hazardous':    return 'bg-orange-100 text-orange-800';
    case 'major':        return 'bg-amber-100 text-amber-700';
    case 'minor':        return 'bg-stone-100 text-stone-500';
    default:             return '';
  }
}

function categoryIcon(cat: FailureCategory): string {
  const map: Record<string, string> = {
    engine: '🔧', fuel: '⛽', hydraulic: '💧', electrical: '⚡',
    avionics: '📡', flight_controls: '✈️', landing_gear: '🛬',
    brakes: '🔴', tires: '⚫', structure: '🏗️', pressurization: '🫧',
    navigation: '🧭', autopilot: '🤖', rotor_system: '🔄',
    propeller: '💫', maintenance: '🔨', other: '❓', unknown: '❓',
  };
  return map[cat] ?? '❓';
}

function confidenceColor(score: number | null): string {
  if (score === null) return 'bg-stone-200';
  if (score >= 0.75) return 'bg-emerald-400';
  if (score >= 0.5)  return 'bg-amber-400';
  return 'bg-red-400';
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ConfidenceBar({ score }: { score: number | null }) {
  if (score === null) return null;
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1 rounded-full bg-stone-100 overflow-hidden">
        <div
          className={`h-full rounded-full ${confidenceColor(score)}`}
          style={{ width: `${Math.round(score * 100)}%` }}
        />
      </div>
      <span className="text-[9px] text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>
        {Math.round(score * 100)}%
      </span>
    </div>
  );
}

function DetectionRow({ label, value }: { label: string; value: boolean | null }) {
  if (value === null) return null;
  return (
    <div className="flex gap-1 text-[10px]">
      <span className="text-stone-400">{label}:</span>
      <span className={value ? 'text-stone-700' : 'text-stone-400'}>{value ? 'Yes' : 'No'}</span>
    </div>
  );
}

function SystemFailureCard({ failure }: { failure: SystemFailure }) {
  const [expanded, setExpanded] = useState(false);

  const isRuledOut = failure.status === 'ruled_out';
  const hasDetail = failure.description || failure.inspection_finding
    || failure.supporting_claims.length > 0 || failure.dispute_summary;

  return (
    <div
      className={`rounded border p-3 mb-2 ${
        failure.is_disputed
          ? 'border-purple-200 bg-purple-50/20'
          : isRuledOut
          ? 'border-stone-100 bg-stone-50/50 opacity-70'
          : 'border-stone-100 bg-white'
      }`}
    >
      {/* Header */}
      <div className="flex flex-wrap items-start gap-2 mb-1.5">
        <span className="text-base" aria-hidden>{categoryIcon(failure.failure_category)}</span>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`text-[13px] font-semibold leading-tight ${isRuledOut ? 'line-through text-stone-400' : 'text-stone-800'}`}>
              {failure.failure_category.replace(/_/g, ' ')}
              {failure.subsystem && <span className="font-normal text-stone-500"> · {failure.subsystem}</span>}
            </span>
            {failure.is_causal_factor && (
              <span className="text-[9px] px-1.5 py-px rounded bg-red-100 text-red-700 font-semibold uppercase tracking-wide border border-red-200">
                Causal
              </span>
            )}
          </div>
          {failure.component_name && (
            <div className="text-[10px] text-stone-500 mt-0.5">{failure.component_name}</div>
          )}
        </div>
      </div>

      {/* Badges row */}
      <div className="flex flex-wrap gap-1 mb-1.5">
        <span className={`text-[10px] px-2 py-px rounded border font-semibold ${statusBadgeClass(failure.status)}`}>
          {statusLabel(failure.status)}
        </span>
        {failure.severity && (
          <span className={`text-[9px] px-1.5 py-px rounded uppercase tracking-wide ${severityBadgeClass(failure.severity as FailureSeverity)}`}>
            {failure.severity}
          </span>
        )}
        {failure.failure_mode && (
          <span className="text-[9px] px-1.5 py-px rounded bg-stone-100 text-stone-500 uppercase tracking-wide">
            {failure.failure_mode.replace(/_/g, ' ')}
          </span>
        )}
        {failure.is_disputed && (
          <span className="text-[9px] px-1.5 py-px rounded bg-purple-100 text-purple-700 font-semibold uppercase tracking-wide">
            Disputed
          </span>
        )}
        {failure.maintenance_related && (
          <span className="text-[9px] px-1.5 py-px rounded bg-amber-100 text-amber-700 uppercase tracking-wide">
            Maintenance
          </span>
        )}
        {failure.source_count > 0 && (
          <span className="text-[9px] px-1.5 py-px rounded bg-stone-100 text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>
            {failure.source_count} src
          </span>
        )}
      </div>

      {/* Confidence */}
      <ConfidenceBar score={failure.confidence_score} />

      {/* Display note — always show, encodes epistemic status */}
      <p className="text-[10px] text-stone-400 italic mt-1 leading-relaxed">
        {failure.display_note}
      </p>

      {/* Expand toggle */}
      {hasDetail && (
        <button
          onClick={() => setExpanded(x => !x)}
          className="text-[10px] text-stone-400 hover:text-stone-600 mt-1 underline-offset-2 hover:underline"
        >
          {expanded ? 'Collapse' : 'Show details'}
        </button>
      )}

      {/* Expanded panel */}
      {expanded && (
        <div className="mt-2 space-y-2 pl-2 border-l border-stone-100">
          {failure.dispute_summary && (
            <div className="text-[11px] text-purple-700 bg-purple-50 rounded px-2 py-1">
              <span className="font-semibold">Dispute: </span>{failure.dispute_summary}
            </div>
          )}

          {/* Detection timing */}
          <div className="space-y-0.5">
            <DetectionRow label="Occurred in flight" value={failure.occurred_in_flight} />
            <DetectionRow label="Detected before accident" value={failure.detected_before_accident} />
            <DetectionRow label="Detected during flight" value={failure.detected_during_flight} />
            <DetectionRow label="Detected post-accident" value={failure.detected_post_accident} />
          </div>

          {failure.description && (
            <p className="text-[11px] text-stone-600 leading-relaxed">{failure.description}</p>
          )}

          {failure.inspection_finding && (
            <div>
              <div className="text-[9px] text-stone-400 uppercase tracking-wide mb-0.5">Inspection finding</div>
              <p className="text-[11px] text-stone-600 leading-relaxed">{failure.inspection_finding}</p>
            </div>
          )}

          {/* Component detail */}
          {(failure.manufacturer || failure.model_number || failure.part_number) && (
            <div className="text-[10px] text-stone-500 space-y-0.5">
              {failure.manufacturer && <div>Manufacturer: {failure.manufacturer}</div>}
              {failure.model_number && <div>Model: {failure.model_number}</div>}
              {failure.part_number && <div>P/N: {failure.part_number}</div>}
              {failure.serial_number && <div>S/N: {failure.serial_number}</div>}
            </div>
          )}

          {/* Claims */}
          {failure.supporting_claims.length > 0 && (
            <div>
              <div className="text-[9px] text-stone-400 uppercase tracking-wide mb-0.5">
                Supporting claims ({failure.supporting_claims.length})
              </div>
              <ul className="space-y-0.5">
                {failure.supporting_claims.map(c => (
                  <li key={c.claim_id} className="text-[10px] text-stone-600 flex gap-1.5">
                    <span className="text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>{c.claim_type}</span>
                    <span>{c.field_name.replace(/_/g, ' ')}</span>
                    <span className="text-stone-400">· {c.link_reason.replace(/_/g, ' ')}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Filters ────────────────────────────────────────────────────────────────────

interface Filters {
  category: string;
  status: string;
  severity: string;
  disputedOnly: boolean;
  maintenanceOnly: boolean;
  confirmedOnly: boolean;
  hideRuledOut: boolean;
}

const CATEGORIES: FailureCategory[] = [
  'engine', 'fuel', 'hydraulic', 'electrical', 'avionics',
  'flight_controls', 'landing_gear', 'brakes', 'tires', 'structure',
  'pressurization', 'navigation', 'autopilot', 'rotor_system',
  'propeller', 'maintenance', 'other', 'unknown',
];

const STATUSES: FailureStatus[] = [
  'suspected', 'reported', 'confirmed', 'disputed', 'ruled_out', 'unknown',
];

function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 mb-3 text-[11px]">
      <select
        value={filters.category}
        onChange={e => onChange({ ...filters, category: e.target.value })}
        className="border border-stone-200 rounded px-1.5 py-0.5 text-stone-600 bg-white text-[11px]"
      >
        <option value="">All categories</option>
        {CATEGORIES.map(c => (
          <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>
        ))}
      </select>
      <select
        value={filters.status}
        onChange={e => onChange({ ...filters, status: e.target.value })}
        className="border border-stone-200 rounded px-1.5 py-0.5 text-stone-600 bg-white text-[11px]"
      >
        <option value="">All statuses</option>
        {STATUSES.map(s => (
          <option key={s} value={s}>{statusLabel(s as FailureStatus)}</option>
        ))}
      </select>
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input type="checkbox" checked={filters.disputedOnly}
          onChange={e => onChange({ ...filters, disputedOnly: e.target.checked })}
          className="rounded border-stone-300" />
        Disputed only
      </label>
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input type="checkbox" checked={filters.maintenanceOnly}
          onChange={e => onChange({ ...filters, maintenanceOnly: e.target.checked })}
          className="rounded border-stone-300" />
        Maintenance-related
      </label>
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input type="checkbox" checked={filters.hideRuledOut}
          onChange={e => onChange({ ...filters, hideRuledOut: e.target.checked })}
          className="rounded border-stone-300" />
        Hide ruled-out
      </label>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function AccidentSystemFailuresPanel({
  accidentEventId,
}: {
  accidentEventId: string;
}) {
  const [data, setData] = useState<SystemFailures | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({
    category: '', status: '', severity: '',
    disputedOnly: false, maintenanceOnly: false,
    confirmedOnly: false, hideRuledOut: false,
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSystemFailures(accidentEventId)
      .then(d => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [accidentEventId]);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data.failures.filter(f => {
      if (filters.category && f.failure_category !== filters.category) return false;
      if (filters.status && f.status !== filters.status) return false;
      if (filters.severity && f.severity !== filters.severity) return false;
      if (filters.disputedOnly && !f.is_disputed) return false;
      if (filters.maintenanceOnly && !f.maintenance_related) return false;
      if (filters.confirmedOnly && f.status !== 'confirmed') return false;
      if (filters.hideRuledOut && f.status === 'ruled_out') return false;
      return true;
    });
  }, [data, filters]);

  if (loading) {
    return (
      <div className="mb-6">
        <SectionTitle>Mechanical / System Failures</SectionTitle>
        <div className="text-[11px] text-stone-400 animate-pulse">Loading failure records…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mb-6">
        <SectionTitle>Mechanical / System Failures</SectionTitle>
        <div className="text-[11px] text-red-500">Could not load failure records: {error}</div>
      </div>
    );
  }

  if (!data || data.failure_count === 0) {
    return (
      <div className="mb-6">
        <SectionTitle>Mechanical / System Failures</SectionTitle>
        <p className="text-[11px] text-stone-400 italic">
          No mechanical or system failure records have been entered for this accident.
        </p>
      </div>
    );
  }

  return (
    <div className="mb-6">
      <SectionTitle>
        Mechanical / System Failures
        <span className="ml-2 text-[10px] font-normal text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>
          {data.failure_count} record{data.failure_count !== 1 ? 's' : ''}
        </span>
      </SectionTitle>

      {data.failure_count > 1 && (
        <FilterBar filters={filters} onChange={setFilters} />
      )}

      {filtered.length === 0 ? (
        <p className="text-[11px] text-stone-400 italic">No records match the current filters.</p>
      ) : (
        filtered.map(f => <SystemFailureCard key={f.id} failure={f} />)
      )}
    </div>
  );
}
