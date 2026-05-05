import { useState, useEffect, useRef } from 'react';
import type {
  AccidentSummary, AccidentDetail, AccidentProvenance, SearchFilters,
} from '../types';
import {
  fetchAccidents, fetchAccident, fetchProvenance,
  MOCK_ACCIDENTS, MOCK_DETAIL, MOCK_PROVENANCE,
} from '../lib/api';

// Fix: mock mode is EXPLICIT only. Removed || process.env.NODE_ENV === 'development'
// which silently forced mock even when NEXT_PUBLIC_USE_MOCK=false.
const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === 'true';

export function useAccidentSearch(filters: SearchFilters, page: number) {
  const [results, setResults] = useState<AccidentSummary[]>(USE_MOCK ? MOCK_ACCIDENTS : []);
  const [total, setTotal] = useState(USE_MOCK ? MOCK_ACCIDENTS.length : 0);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(!USE_MOCK);
  const [error, setError] = useState<string | null>(null);
  const cursorByPageRef = useRef<(string | null)[]>([null]);
  const filterKeyRef = useRef('');

  useEffect(() => {
    const filterKey = JSON.stringify({
      q: filters.q,
      severity: filters.severity,
      phase: filters.phase,
      year_from: filters.year_from,
      min_source_completeness: filters.min_source_completeness,
      fatality_status: filters.fatality_status,
      registration: filters.registration,
      aircraft_type: filters.aircraft_type,
      operator: filters.operator,
      source_id: filters.source_id,
      disputed_only: filters.disputed_only,
      final_report_only: filters.final_report_only,
      sort: filters.sort,
    });
    if (filterKeyRef.current !== filterKey) {
      filterKeyRef.current = filterKey;
      cursorByPageRef.current = [null];
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const run = async () => {
      if (USE_MOCK) {
        await new Promise((r) => setTimeout(r, 100));
        if (cancelled) return;
        let data = [...MOCK_ACCIDENTS];
        if (filters.q) {
          const q = filters.q.toLowerCase();
          data = data.filter((a) =>
            [a.aircraft_make, a.aircraft_model, a.location_text, a.operator_name]
              .join(' ').toLowerCase().includes(q)
          );
        }
        if (filters.severity) data = data.filter((a) => a.injury_severity === filters.severity);
        if (filters.fatality_status === 'some') data = data.filter((a) => (a.fatalities_total ?? 0) > 0);
        if (filters.fatality_status === 'none') data = data.filter((a) => a.fatalities_total === 0);
        if (filters.fatality_status === 'unknown') data = data.filter((a) => a.fatalities_total == null);
        if (filters.phase) data = data.filter((a) => a.phase_of_flight === filters.phase);
        if (filters.year_from) data = data.filter((a) => (a.occurred_year ?? 0) >= parseInt(filters.year_from));
        if (filters.min_source_completeness) data = data.filter((a) => a.confidence.score >= parseFloat(filters.min_source_completeness));
        const sortFns: Record<string, (a: AccidentSummary, b: AccidentSummary) => number> = {
          date_desc: (a, b) => (b.occurred_date ?? '').localeCompare(a.occurred_date ?? ''),
          date_asc:  (a, b) => (a.occurred_date ?? '').localeCompare(b.occurred_date ?? ''),
          source_completeness_desc: (a, b) => b.confidence.score - a.confidence.score,
          confidence_desc: (a, b) => b.confidence.score - a.confidence.score,  // legacy alias
          fatalities_desc: (a, b) => (b.fatalities_total ?? 0) - (a.fatalities_total ?? 0),
        };
        data.sort(sortFns[filters.sort] ?? sortFns.date_desc);
        const PAGE_SIZE = 8;
        setTotal(data.length);
        setResults(data.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE));
        setHasNext((page + 1) * PAGE_SIZE < data.length);
        setLoading(false);
        return;
      }
      try {
        const cursorSort = filters.sort === 'date_desc' || filters.sort === 'date_asc';
        const cursor = cursorSort ? cursorByPageRef.current[page] : null;
        const res = await fetchAccidents({
          ...filters,
          cursor: cursor ?? undefined,
          page: cursor ? undefined : page,
          page_size: 8,
        });
        if (!cancelled) {
          if (cursorSort) {
            const nextCursors = [...cursorByPageRef.current];
            if (res.next_cursor) {
              nextCursors[page + 1] = res.next_cursor;
            } else {
              nextCursors.length = page + 1;
            }
            cursorByPageRef.current = nextCursors;
          }
          setResults(res.items);
          setTotal(res.total);
          setHasNext(res.has_next);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) { setError(`API error: ${String(err)}`); setLoading(false); }
      }
    };
    run();
    return () => { cancelled = true; };
  }, [
    filters.q,
    filters.severity,
    filters.phase,
    filters.year_from,
    filters.min_source_completeness,
    filters.fatality_status,
    filters.registration,
    filters.aircraft_type,
    filters.operator,
    filters.source_id,
    filters.disputed_only,
    filters.final_report_only,
    filters.sort,
    page,
  ]);

  return { results, total, hasNext, loading, error };
}

export function useAccidentDetail(id: string | null) {
  const [detail, setDetail] = useState<AccidentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) { setDetail(null); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const run = async () => {
      if (USE_MOCK) {
        await new Promise((r) => setTimeout(r, 80));
        if (cancelled) return;
        if (MOCK_DETAIL[id]) {
          setDetail(MOCK_DETAIL[id]);
        } else {
          // Fallback for mock IDs not in MOCK_DETAIL — build from summary if available.
          // Do NOT spread undefined into an AccidentDetail; that hides a real bug.
          const summary = MOCK_ACCIDENTS.find((a) => a.id === id);
          if (!summary) {
            setError(`Mock accident ${id} not found`);
            setDetail(null);
          } else {
            setDetail({
              ...summary,
              probable_cause: 'Investigation ongoing.',
              contributing_factors: null,
              ntsb_report_number: null,
              weather_condition: 'VMC',
              purpose_of_flight: 'Personal',
              aircraft_registration: null,
              aircraft_amateur_built: null,
              serious_injuries: null,
              minor_injuries: null,
              state_code: null,
              last_projected_at: new Date().toISOString(),
              document_status: 'none_linked',
            });
          }
        }
        setLoading(false);
        return;
      }
      try {
        const res = await fetchAccident(id);
        if (!cancelled) { setDetail(res); setLoading(false); }
      } catch (err) {
        if (!cancelled) { setError(String(err)); setLoading(false); }
      }
    };
    run();
    return () => { cancelled = true; };
  }, [id]);

  return { detail, loading, error };
}

export function useProvenance(id: string | null) {
  const [provenance, setProvenance] = useState<AccidentProvenance | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!id) { setProvenance(null); return; }
    let cancelled = false;
    setLoading(true);
    const run = async () => {
      if (USE_MOCK) {
        await new Promise((r) => setTimeout(r, 120));
        if (!cancelled) { setProvenance({ ...MOCK_PROVENANCE, event_id: id }); setLoading(false); }
        return;
      }
      try {
        const res = await fetchProvenance(id);
        if (!cancelled) { setProvenance(res); setLoading(false); }
      } catch { if (!cancelled) setLoading(false); }
    };
    run();
    return () => { cancelled = true; };
  }, [id, refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);
  return { provenance, loading, refresh };
}
