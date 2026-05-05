/**
 * SimilarAccidentsPanel
 *
 * Shows accidents with similar technical context to the current accident.
 * Uses weighted feature matching — NOT ML or correlation = causation.
 *
 * Design rules:
 * - Never label similarity as shared cause.
 * - Always show the similarity_note disclaimer.
 * - Show low_confidence_warning when present.
 * - Fatality alone is insufficient for high similarity.
 * - Shared factors and differing factors are always shown.
 *
 * Data source: GET /api/v1/accidents/{id}/similar
 */
import { useState, useEffect } from 'react';
import type { SimilarAccident, SimilarAccidentsResult } from '../types';
import { fetchSimilarAccidents } from '../lib/api';
import { SectionTitle } from './SectionHelpers';

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = score >= 0.6 ? 'bg-indigo-400' : score >= 0.35 ? 'bg-blue-300' : 'bg-stone-200';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1 rounded-full bg-stone-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[9px] text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>
        {pct}%
      </span>
    </div>
  );
}

function SimilarAccidentCard({ acc }: { acc: SimilarAccident }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border border-stone-100 rounded p-2.5 mb-2 bg-white">
      <div className="flex items-start justify-between gap-2 mb-1">
        <a
          href={`/?id=${acc.accident_id}`}
          className="text-[12px] font-medium text-indigo-700 hover:underline truncate"
        >
          {acc.accident_id}
        </a>
        <ScoreBar score={acc.similarity_score} />
      </div>
      {acc.low_confidence_warning && (
        <div className="text-[9px] text-amber-600 bg-amber-50 rounded px-1.5 py-0.5 mb-1">
          ⚠ Similarity based on low-confidence or disputed data
        </div>
      )}
      <div className="text-[10px] text-stone-600 mb-0.5">
        <span className="text-stone-400">Shared: </span>
        {acc.shared_factors.length > 0 ? acc.shared_factors.join(' · ') : '—'}
      </div>
      <button
        onClick={() => setExpanded(x => !x)}
        className="text-[10px] text-stone-400 hover:text-stone-600 underline-offset-2 hover:underline"
      >
        {expanded ? 'Less' : 'Differences & details'}
      </button>
      {expanded && (
        <div className="mt-1.5 space-y-1 text-[10px]">
          {acc.differing_factors.length > 0 && (
            <div>
              <span className="text-stone-400">Differs: </span>
              <span className="text-stone-600">{acc.differing_factors.join(' · ')}</span>
            </div>
          )}
          <p className="text-stone-400 italic text-[9px] border-t border-stone-50 pt-1">
            {acc.similarity_note}
          </p>
        </div>
      )}
    </div>
  );
}

export default function SimilarAccidentsPanel({ accidentEventId }: { accidentEventId: string }) {
  const [result, setResult] = useState<SimilarAccidentsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSimilarAccidents(accidentEventId)
      .then(d => { if (!cancelled) { setResult(d); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [accidentEventId]);

  if (loading) return (
    <div className="mb-6">
      <SectionTitle>Similar Accidents</SectionTitle>
      <div className="text-[11px] text-stone-400 animate-pulse">Finding similar accidents…</div>
    </div>
  );

  if (error) return (
    <div className="mb-6">
      <SectionTitle>Similar Accidents</SectionTitle>
      <div className="text-[11px] text-red-500">Could not load: {error}</div>
    </div>
  );

  if (!result || result.similar_count === 0) return (
    <div className="mb-6">
      <SectionTitle>Similar Accidents</SectionTitle>
      <p className="text-[11px] text-stone-400 italic">
        No similar accidents found above the minimum similarity threshold.
      </p>
    </div>
  );

  return (
    <div className="mb-6">
      <SectionTitle>
        Similar Accidents
        <span className="ml-2 text-[10px] font-normal text-stone-400" style={{ fontFamily: 'var(--ff-mono)' }}>
          {result.similar_count} found
        </span>
      </SectionTitle>
      <p className="text-[10px] text-stone-400 italic mb-3">{result.similarity_note}</p>
      {result.similar_accidents.map(acc => (
        <SimilarAccidentCard key={acc.accident_id} acc={acc} />
      ))}
    </div>
  );
}
