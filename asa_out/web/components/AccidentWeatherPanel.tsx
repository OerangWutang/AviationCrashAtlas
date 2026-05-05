/**
 * AccidentWeatherPanel
 *
 * Displays weather context observations for an aviation accident.
 *
 * Key design decisions:
 * - Weather is ALWAYS labelled as contextual, never causal, unless a source
 *   explicitly says otherwise. The causation_note from the API is surfaced.
 * - Raw METAR text is preserved and shown verbatim when available.
 * - Parsed canonical fields are displayed with clear labels and units.
 * - Flight rules badge (VFR/MVFR/IFR/LIFR) is colour-coded for at-a-glance reading.
 * - Disputed observations are visibly marked with a red badge.
 * - Confidence bar reflects source reliability + time/distance proximity.
 * - Observations can be filtered by report type, disputed state, low confidence.
 * - Empty state is handled gracefully.
 *
 * Data source: GET /api/v1/accidents/{id}/weather
 */
import { useState, useEffect, useMemo } from 'react';
import type {
  FlightRules,
  IcingRisk,
  TurbulenceRisk,
  WeatherContext,
  WeatherObservation,
  WeatherReportType,
} from '../types';
import { fetchAccidentWeather } from '../lib/api';
import { SectionTitle } from './SectionHelpers';

// ── Display helpers ────────────────────────────────────────────────────────────

function flightRulesLabel(fr: FlightRules | null): string {
  switch (fr) {
    case 'vfr':  return 'VFR';
    case 'mvfr': return 'MVFR';
    case 'ifr':  return 'IFR';
    case 'lifr': return 'LIFR';
    default:     return 'Unknown';
  }
}

function flightRulesBadgeClass(fr: FlightRules | null): string {
  switch (fr) {
    case 'vfr':  return 'bg-emerald-100 text-emerald-700 border-emerald-200';
    case 'mvfr': return 'bg-blue-100 text-blue-700 border-blue-200';
    case 'ifr':  return 'bg-red-100 text-red-700 border-red-200';
    case 'lifr': return 'bg-purple-100 text-purple-700 border-purple-200';
    default:     return 'bg-stone-100 text-stone-500 border-stone-200';
  }
}

function riskBadge(label: string, level: string | null): JSX.Element | null {
  if (!level || level === 'none' || level === 'unknown') return null;
  const cls =
    level === 'severe' ? 'bg-red-100 text-red-700' :
    level === 'likely' ? 'bg-orange-100 text-orange-700' :
    'bg-amber-100 text-amber-700';
  return (
    <span key={label} className={`text-[9px] px-1.5 py-px rounded font-medium uppercase tracking-wide ${cls}`}>
      {label} {level}
    </span>
  );
}

function reportTypeBadge(rt: WeatherReportType): JSX.Element {
  const colors: Record<string, string> = {
    metar:          'bg-indigo-100 text-indigo-700',
    taf:            'bg-blue-100 text-blue-700',
    pirep:          'bg-violet-100 text-violet-700',
    radar:          'bg-cyan-100 text-cyan-700',
    satellite:      'bg-teal-100 text-teal-700',
    report_summary: 'bg-stone-100 text-stone-600',
    manual:         'bg-amber-100 text-amber-700',
  };
  return (
    <span className={`text-[9px] px-1.5 py-px rounded uppercase tracking-wide font-semibold ${colors[rt] ?? 'bg-stone-100 text-stone-500'}`}>
      {rt.replace('_', ' ')}
    </span>
  );
}

function formatDelta(delta: number | null): string {
  if (delta === null) return 'time unknown';
  const abs = Math.abs(delta);
  const sign = delta < 0 ? 'before' : 'after';
  if (abs < 60) return `${Math.round(abs)} min ${sign} accident`;
  const hrs = Math.floor(abs / 60);
  const mins = Math.round(abs % 60);
  return `${hrs}h ${mins}m ${sign} accident`;
}

function formatVisibility(vis_m: number | null): string {
  if (vis_m === null) return '—';
  const sm = vis_m / 1609.344;
  if (sm >= 10) return '10+ SM';
  return `${sm.toFixed(1)} SM (${(vis_m / 1000).toFixed(1)} km)`;
}

function formatWind(
  dir: number | null,
  speed: number | null,
  gust: number | null,
): string {
  if (speed === null) return '—';
  const dirStr = dir !== null ? `${dir}°` : 'VRB';
  const gustStr = gust !== null ? ` G${gust}kt` : '';
  return `${dirStr} @ ${speed}kt${gustStr}`;
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

function WeatherObservationCard({ obs }: { obs: WeatherObservation }) {
  const [expanded, setExpanded] = useState(false);

  const hasDetail = obs.raw_report_text || obs.description || obs.supporting_claims.length > 0
    || obs.parsed_data || obs.dispute_summary;

  return (
    <div className={`rounded border ${obs.is_disputed ? 'border-red-200 bg-red-50/30' : 'border-stone-100 bg-white'} p-3 mb-2`}>
      {/* Header row */}
      <div className="flex flex-wrap items-start gap-2 mb-1.5">
        {/* Station */}
        <div>
          <span className="text-[13px] font-semibold text-stone-800 leading-tight">
            {obs.station_identifier ?? 'Unknown station'}
          </span>
          {obs.station_name && (
            <span className="text-[10px] text-stone-400 ml-1">· {obs.station_name}</span>
          )}
        </div>

        {/* Badges */}
        <div className="flex flex-wrap gap-1 items-center">
          {reportTypeBadge(obs.report_type)}
          {obs.flight_rules && (
            <span className={`text-[10px] px-2 py-px rounded border font-bold ${flightRulesBadgeClass(obs.flight_rules)}`}>
              {flightRulesLabel(obs.flight_rules)}
            </span>
          )}
          {obs.is_disputed && (
            <span className="text-[9px] px-1.5 py-px rounded bg-red-100 text-red-600 font-semibold uppercase tracking-wide">
              Disputed
            </span>
          )}
          {obs.thunderstorm_present && (
            <span className="text-[9px] px-1.5 py-px rounded bg-yellow-100 text-yellow-700 font-semibold">
              ⚡ TS
            </span>
          )}
          {riskBadge('Icing', obs.icing_risk)}
          {riskBadge('Turb', obs.turbulence_risk)}
        </div>
      </div>

      {/* Station meta */}
      <div className="flex flex-wrap gap-3 text-[10px] text-stone-500 mb-2">
        {obs.accident_time_delta_minutes !== null && (
          <span>{formatDelta(obs.accident_time_delta_minutes)}</span>
        )}
        {obs.distance_to_accident_km !== null && (
          <span>{obs.distance_to_accident_km.toFixed(1)} km from site</span>
        )}
        {obs.observation_time_utc && (
          <span style={{ fontFamily: 'var(--ff-mono)' }}>
            {new Date(obs.observation_time_utc).toUTCString().replace(' GMT', ' UTC')}
          </span>
        )}
      </div>

      {/* Parsed weather grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] mb-2">
        {obs.wind_speed_kt !== null && (
          <Row label="Wind" value={formatWind(obs.wind_direction_degrees, obs.wind_speed_kt, obs.wind_gust_kt)} />
        )}
        {obs.visibility_m !== null && (
          <Row label="Visibility" value={formatVisibility(obs.visibility_m)} />
        )}
        {obs.ceiling_ft !== null && (
          <Row label="Ceiling" value={`${obs.ceiling_ft.toLocaleString()} ft`} />
        )}
        {obs.temperature_c !== null && obs.dew_point_c !== null && (
          <Row label="Temp / DP" value={`${obs.temperature_c}°C / ${obs.dew_point_c}°C`} />
        )}
        {obs.altimeter_hpa !== null && (
          <Row label="Altimeter" value={`${obs.altimeter_hpa} hPa`} />
        )}
        {obs.precipitation_type && (
          <Row label="Precip" value={obs.precipitation_type.replace('_', ' ')} />
        )}
      </div>

      {/* Confidence */}
      <ConfidenceBar score={obs.confidence_score} />

      {/* Expand toggle */}
      {hasDetail && (
        <button
          onClick={() => setExpanded(x => !x)}
          className="text-[10px] text-stone-400 hover:text-stone-600 mt-1.5 underline-offset-2 hover:underline"
        >
          {expanded ? 'Collapse' : 'Show raw report & sources'}
        </button>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-2 space-y-2">
          {obs.dispute_summary && (
            <div className="text-[11px] text-red-600 bg-red-50 rounded px-2 py-1">
              <span className="font-semibold">Dispute: </span>{obs.dispute_summary}
            </div>
          )}

          {obs.raw_report_text && (
            <div>
              <div className="text-[9px] text-stone-400 uppercase tracking-wide mb-0.5">Raw report</div>
              <pre
                className="text-[10px] text-stone-700 bg-stone-50 rounded px-2 py-1.5 overflow-x-auto whitespace-pre-wrap break-words"
                style={{ fontFamily: 'var(--ff-mono)' }}
              >
                {obs.raw_report_text}
              </pre>
            </div>
          )}

          {obs.supporting_claims.length > 0 && (
            <div>
              <div className="text-[9px] text-stone-400 uppercase tracking-wide mb-0.5">
                Supporting claims ({obs.supporting_claims.length})
              </div>
              <ul className="space-y-0.5">
                {obs.supporting_claims.map(c => (
                  <li key={c.claim_id} className="text-[10px] text-stone-600 flex gap-1.5">
                    <span className="text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>{c.claim_type}</span>
                    <span>{c.field_name.replace(/_/g, ' ')}</span>
                    <span className="text-stone-400">· {c.link_reason.replace(/_/g, ' ')}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Causation note — always shown when expanded */}
          <p className="text-[9px] text-stone-400 italic border-t border-stone-100 pt-1">
            {obs.causation_note}
          </p>
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-stone-400">{label}</span>
      <span className="text-stone-700">{value}</span>
    </>
  );
}

// ── Filters ────────────────────────────────────────────────────────────────────

interface Filters {
  reportType: string;
  disputedOnly: boolean;
  lowConfidenceOnly: boolean;
}

function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  const types: WeatherReportType[] = ['metar', 'taf', 'pirep', 'radar', 'satellite', 'report_summary', 'manual'];
  return (
    <div className="flex flex-wrap gap-2 mb-3 text-[11px]">
      <select
        value={filters.reportType}
        onChange={e => onChange({ ...filters, reportType: e.target.value })}
        className="border border-stone-200 rounded px-1.5 py-0.5 text-stone-600 bg-white text-[11px]"
      >
        <option value="">All types</option>
        {types.map(t => (
          <option key={t} value={t}>{t.replace('_', ' ').toUpperCase()}</option>
        ))}
      </select>
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input
          type="checkbox"
          checked={filters.disputedOnly}
          onChange={e => onChange({ ...filters, disputedOnly: e.target.checked })}
          className="rounded border-stone-300"
        />
        Disputed only
      </label>
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input
          type="checkbox"
          checked={filters.lowConfidenceOnly}
          onChange={e => onChange({ ...filters, lowConfidenceOnly: e.target.checked })}
          className="rounded border-stone-300"
        />
        Low confidence only
      </label>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function AccidentWeatherPanel({
  accidentEventId,
}: {
  accidentEventId: string;
}) {
  const [weather, setWeather] = useState<WeatherContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({
    reportType: '',
    disputedOnly: false,
    lowConfidenceOnly: false,
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchAccidentWeather(accidentEventId)
      .then(data => { if (!cancelled) { setWeather(data); setLoading(false); } })
      .catch(err => { if (!cancelled) { setError(String(err)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [accidentEventId]);

  const filtered = useMemo(() => {
    if (!weather) return [];
    return weather.observations.filter(o => {
      if (filters.reportType && o.report_type !== filters.reportType) return false;
      if (filters.disputedOnly && !o.is_disputed) return false;
      if (filters.lowConfidenceOnly && (o.confidence_score ?? 1) >= 0.5) return false;
      return true;
    });
  }, [weather, filters]);

  if (loading) {
    return (
      <div className="mb-6">
        <SectionTitle>Weather Context</SectionTitle>
        <div className="text-[11px] text-stone-400 animate-pulse">Loading weather data…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mb-6">
        <SectionTitle>Weather Context</SectionTitle>
        <div className="text-[11px] text-red-500">Could not load weather data: {error}</div>
      </div>
    );
  }

  if (!weather || weather.observation_count === 0) {
    return (
      <div className="mb-6">
        <SectionTitle>Weather Context</SectionTitle>
        <p className="text-[11px] text-stone-400 italic">
          No weather observations have been recorded for this accident yet.
        </p>
      </div>
    );
  }

  return (
    <div className="mb-6">
      <SectionTitle>
        Weather Context
        <span
          className="ml-2 text-[10px] font-normal text-stone-400"
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          {weather.observation_count} observation{weather.observation_count !== 1 ? 's' : ''}
        </span>
      </SectionTitle>

      {/* Non-causal disclaimer */}
      <p className="text-[10px] text-stone-400 italic mb-3">
        Weather data is contextual evidence. It does not imply causation unless
        explicitly supported by a source claim.
      </p>

      {weather.observation_count > 1 && (
        <FilterBar filters={filters} onChange={setFilters} />
      )}

      {filtered.length === 0 ? (
        <p className="text-[11px] text-stone-400 italic">
          No observations match the current filters.
        </p>
      ) : (
        filtered.map(obs => (
          <WeatherObservationCard key={obs.id} obs={obs} />
        ))
      )}
    </div>
  );
}
