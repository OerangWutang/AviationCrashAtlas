/**
 * AccidentIncidentTimeline
 *
 * Displays the reconstructed chronological timeline of events for an accident.
 * Distinct from AccidentTimelineSection (which shows how the *record* evolved via
 * ingestion revisions) — this component shows what happened *during the accident*.
 *
 * Data source: GET /api/v1/accidents/{id}/timeline
 *
 * Features
 * --------
 * - Vertical chronological spine
 * - Time label respecting time_precision (never shows exact timestamp for approximate data)
 * - Category badge and phase-of-flight label
 * - Confidence indicator bar
 * - Disputed badge
 * - Expandable detail panel with description and source claim list
 * - Filters: category, phase_of_flight, disputed only, low confidence only
 * - Empty state handled cleanly
 */

import { useState, useEffect, useMemo } from 'react';
import type { AccidentTimeline, TimelineEvent, TimePrecision } from '../types';
import { fetchAccidentTimeline } from '../lib/api';
import { SectionTitle } from './SectionHelpers';

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatEventTime(
  event: TimelineEvent,
): { label: string; uncertain: boolean } {
  const p = event.time_precision as TimePrecision;

  if (p === 'exact' && event.event_time_utc) {
    const d = new Date(event.event_time_utc);
    return {
      label: d.toUTCString().replace(' GMT', ' UTC'),
      uncertain: false,
    };
  }
  if ((p === 'approximate') && event.event_time_utc) {
    const d = new Date(event.event_time_utc);
    return {
      label: `~${d.toUTCString().replace(' GMT', ' UTC')}`,
      uncertain: true,
    };
  }
  if (p === 'relative' && event.relative_offset_seconds != null) {
    const abs = Math.abs(event.relative_offset_seconds);
    const sign = event.relative_offset_seconds < 0 ? 'before' : 'after';
    const mins = Math.floor(abs / 60);
    const secs = abs % 60;
    const parts = [];
    if (mins > 0) parts.push(`${mins}m`);
    if (secs > 0 || mins === 0) parts.push(`${secs}s`);
    return { label: `${parts.join(' ')} ${sign} impact`, uncertain: true };
  }
  if (p === 'sequence_only' && event.sequence_index != null) {
    return { label: `Step ${event.sequence_index + 1}`, uncertain: true };
  }
  return { label: 'Time unknown', uncertain: true };
}

function confidenceColor(score: number | null): string {
  if (score === null) return 'bg-stone-200';
  if (score >= 0.75) return 'bg-emerald-400';
  if (score >= 0.5)  return 'bg-amber-400';
  return 'bg-red-400';
}

function categoryBadgeClass(category: string | null): string {
  switch (category) {
    case 'pre_accident':  return 'bg-blue-100 text-blue-700';
    case 'in_flight':     return 'bg-indigo-100 text-indigo-700';
    case 'impact':        return 'bg-red-100 text-red-700';
    case 'post_accident': return 'bg-orange-100 text-orange-700';
    case 'investigation': return 'bg-stone-100 text-stone-600';
    default:              return 'bg-stone-100 text-stone-500';
  }
}

function severityDot(severity: string | null): string {
  switch (severity) {
    case 'critical':      return 'bg-red-600';
    case 'high':          return 'bg-orange-500';
    case 'medium':        return 'bg-amber-400';
    case 'low':           return 'bg-blue-400';
    case 'informational': return 'bg-stone-300';
    default:              return 'bg-stone-300';
  }
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function TimelineSpineLine({ isLast }: { isLast: boolean }) {
  return (
    <div className="flex flex-col items-center w-5 flex-shrink-0">
      <div className="w-2 h-2 rounded-full border-2 border-stone-400 bg-white mt-1 flex-shrink-0 z-10" />
      {!isLast && <div className="w-px flex-1 bg-stone-200 mt-1" />}
    </div>
  );
}

function ConfidenceBar({ score }: { score: number | null }) {
  if (score === null) return null;
  const pct = Math.round(score * 100);
  return (
    <div className="flex items-center gap-1.5 mt-1">
      <div className="w-16 h-1 rounded-full bg-stone-100 overflow-hidden">
        <div
          className={`h-full rounded-full ${confidenceColor(score)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span
        className="text-[9px] text-stone-400"
        style={{ fontFamily: 'var(--ff-mono)' }}
      >
        {pct}%
      </span>
    </div>
  );
}

function TimelineEventCard({
  event,
  isLast,
}: {
  event: TimelineEvent;
  isLast: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const time = formatEventTime(event);

  return (
    <div className="flex gap-3">
      <TimelineSpineLine isLast={isLast} />

      <div className="pb-4 flex-1 min-w-0">
        {/* Time label */}
        <div
          className={`text-[9px] mb-0.5 ${time.uncertain ? 'text-stone-400 italic' : 'text-stone-500'}`}
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          {time.label}
        </div>

        {/* Title row */}
        <div className="flex flex-wrap items-center gap-1.5">
          {/* Severity dot */}
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${severityDot(event.severity)}`} />

          <span className="text-[13px] font-medium text-stone-800 leading-snug">
            {event.title}
          </span>

          {/* Disputed badge */}
          {event.is_disputed && (
            <span className="text-[9px] px-1 py-px rounded bg-red-100 text-red-600 font-semibold uppercase tracking-wide">
              Disputed
            </span>
          )}
        </div>

        {/* Category + phase badges */}
        <div className="flex flex-wrap gap-1 mt-1">
          {event.category && (
            <span
              className={`text-[9px] px-1.5 py-px rounded font-medium uppercase tracking-wide ${categoryBadgeClass(event.category)}`}
            >
              {event.category.replace(/_/g, ' ')}
            </span>
          )}
          {event.phase_of_flight && (
            <span className="text-[9px] px-1.5 py-px rounded bg-stone-100 text-stone-500 uppercase tracking-wide">
              {event.phase_of_flight}
            </span>
          )}
        </div>

        {/* Confidence bar */}
        <ConfidenceBar score={event.confidence_score} />

        {/* Expand toggle */}
        {(event.description || event.supporting_claims.length > 0 || event.dispute_summary) && (
          <button
            onClick={() => setExpanded((x) => !x)}
            className="text-[10px] text-stone-400 hover:text-stone-600 mt-1 underline-offset-2 hover:underline"
          >
            {expanded ? 'Collapse' : 'Show details'}
          </button>
        )}

        {/* Expanded panel */}
        {expanded && (
          <div className="mt-2 text-[11px] text-stone-600 space-y-2 pl-2 border-l border-stone-100">
            {event.dispute_summary && (
              <div className="text-red-600 bg-red-50 rounded px-2 py-1">
                <span className="font-semibold">Dispute: </span>
                {event.dispute_summary}
              </div>
            )}
            {event.description && (
              <p className="leading-relaxed">{event.description}</p>
            )}
            {event.supporting_claims.length > 0 && (
              <div>
                <div className="text-[9px] text-stone-400 uppercase tracking-wide mb-1">
                  Supporting claims ({event.supporting_claims.length})
                </div>
                <ul className="space-y-0.5">
                  {event.supporting_claims.map((c) => (
                    <li key={c.claim_id} className="flex gap-1.5 items-center">
                      <span
                        className="text-[9px] text-stone-400"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        {c.claim_type}
                      </span>
                      <span className="text-[10px] text-stone-600">
                        {c.field_name.replace(/_/g, ' ')}
                      </span>
                      <span className="text-[9px] text-stone-400">
                        · {c.link_reason.replace(/_/g, ' ')}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Filters ────────────────────────────────────────────────────────────────────

interface Filters {
  category: string;
  phase: string;
  disputedOnly: boolean;
  lowConfidenceOnly: boolean;
}

function FilterBar({
  events,
  filters,
  onChange,
}: {
  events: TimelineEvent[];
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  const categories = useMemo(() => {
    const s = new Set<string>();
    events.forEach((e) => { if (e.category) s.add(e.category); });
    return [...s].sort();
  }, [events]);

  const phases = useMemo(() => {
    const s = new Set<string>();
    events.forEach((e) => { if (e.phase_of_flight) s.add(e.phase_of_flight); });
    return [...s].sort();
  }, [events]);

  return (
    <div className="flex flex-wrap gap-2 mb-4 text-[11px]">
      {/* Category */}
      {categories.length > 0 && (
        <select
          value={filters.category}
          onChange={(e) => onChange({ ...filters, category: e.target.value })}
          className="border border-stone-200 rounded px-1.5 py-0.5 text-stone-600 bg-white text-[11px]"
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>
          ))}
        </select>
      )}
      {/* Phase */}
      {phases.length > 0 && (
        <select
          value={filters.phase}
          onChange={(e) => onChange({ ...filters, phase: e.target.value })}
          className="border border-stone-200 rounded px-1.5 py-0.5 text-stone-600 bg-white text-[11px]"
        >
          <option value="">All phases</option>
          {phases.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      )}
      {/* Flags */}
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input
          type="checkbox"
          checked={filters.disputedOnly}
          onChange={(e) => onChange({ ...filters, disputedOnly: e.target.checked })}
          className="rounded border-stone-300"
        />
        Disputed only
      </label>
      <label className="flex items-center gap-1 cursor-pointer text-stone-500">
        <input
          type="checkbox"
          checked={filters.lowConfidenceOnly}
          onChange={(e) => onChange({ ...filters, lowConfidenceOnly: e.target.checked })}
          className="rounded border-stone-300"
        />
        Low confidence only
      </label>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function AccidentIncidentTimeline({
  accidentEventId,
}: {
  accidentEventId: string;
}) {
  const [timeline, setTimeline] = useState<AccidentTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({
    category: '',
    phase: '',
    disputedOnly: false,
    lowConfidenceOnly: false,
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchAccidentTimeline(accidentEventId)
      .then((data) => { if (!cancelled) { setTimeline(data); setLoading(false); } })
      .catch((err) => { if (!cancelled) { setError(String(err)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [accidentEventId]);

  const filtered = useMemo(() => {
    if (!timeline) return [];
    return timeline.events.filter((e) => {
      if (filters.category && e.category !== filters.category) return false;
      if (filters.phase && e.phase_of_flight !== filters.phase) return false;
      if (filters.disputedOnly && !e.is_disputed) return false;
      if (filters.lowConfidenceOnly && (e.confidence_score ?? 1) >= 0.5) return false;
      return true;
    });
  }, [timeline, filters]);

  if (loading) {
    return (
      <div className="mb-6">
        <SectionTitle>Accident Timeline</SectionTitle>
        <div className="text-[11px] text-stone-400 animate-pulse">Loading timeline…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mb-6">
        <SectionTitle>Accident Timeline</SectionTitle>
        <div className="text-[11px] text-red-500">Could not load timeline: {error}</div>
      </div>
    );
  }

  if (!timeline || timeline.event_count === 0) {
    return (
      <div className="mb-6">
        <SectionTitle>Accident Timeline</SectionTitle>
        <p className="text-[11px] text-stone-400 italic">
          No timeline events have been recorded for this accident yet.
        </p>
      </div>
    );
  }

  return (
    <div className="mb-6">
      <SectionTitle>
        Accident Timeline
        <span
          className="ml-2 text-[10px] font-normal text-stone-400"
          style={{ fontFamily: 'var(--ff-mono)' }}
        >
          {timeline.event_count} event{timeline.event_count !== 1 ? 's' : ''}
        </span>
      </SectionTitle>

      {/* Filters */}
      {timeline.event_count > 1 && (
        <FilterBar events={timeline.events} filters={filters} onChange={setFilters} />
      )}

      {/* Empty filter result */}
      {filtered.length === 0 && (
        <p className="text-[11px] text-stone-400 italic">
          No events match the current filters.
        </p>
      )}

      {/* Timeline spine */}
      <div className="space-y-0">
        {filtered.map((event, i) => (
          <TimelineEventCard
            key={event.id}
            event={event}
            isLast={i === filtered.length - 1}
          />
        ))}
      </div>
    </div>
  );
}
