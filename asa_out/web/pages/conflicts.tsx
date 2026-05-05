import { useEffect, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import type { ConflictQueueItem } from '../types';
import ReviewerAuthControl from '../components/ReviewerAuthControl';
import { useReviewerAuth } from '../hooks/useReviewerAuth';
import { FIELD_LABELS, formatDateOnly, SEV_BG } from '../lib/utils';
import {
  fetchConflictQueue,
  fetchConflictStats,
  type ConflictStats,
} from '../lib/api';

function fieldLabel(name: string) {
  return FIELD_LABELS[name as keyof typeof FIELD_LABELS] ?? name.replace(/_/g, ' ');
}

function SevBadge({ sev }: { sev: string | null }) {
  if (!sev) return null;
  const cls = SEV_BG[sev] ?? 'bg-stone-50 text-stone-400 border-stone-200';
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded border ${cls}`}
      style={{ fontFamily: 'var(--ff-mono)' }}>
      {sev}
    </span>
  );
}

export default function ConflictsPage() {
  const [items, setItems] = useState<ConflictQueueItem[]>([]);
  const [stats, setStats] = useState<ConflictStats | null>(null);
  const [fieldFilter, setFieldFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reviewerAuth = useReviewerAuth();

  function load(field?: string) {
    setLoading(true);
    setError(null);
    Promise.all([fetchConflictQueue(field || undefined, 100, reviewerAuth.apiKey), fetchConflictStats(reviewerAuth.apiKey)])
      .then(([q, s]) => { setItems(q); setStats(s); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => { if (reviewerAuth.hydrated) load(); }, [reviewerAuth.hydrated, reviewerAuth.apiKey]);

  return (
    <>
      <Head>
        <title>Conflict Queue — Aviation Safety Atlas</title>
      </Head>

      <div className="min-h-screen bg-stone-50">
        {/* Header */}
        <div className="border-b border-stone-200 bg-white px-6 py-4 flex items-center gap-4">
          <Link href="/" className="text-[11px] text-stone-400 hover:text-stone-600 transition-colors"
            style={{ fontFamily: 'var(--ff-mono)' }}>
            ← back to search
          </Link>
          <div className="flex-1">
            <h1 className="text-[16px] font-semibold text-stone-800">Conflict Review Queue</h1>
            <p className="text-[11px] text-stone-400 mt-0.5">
              Open claim conflicts across all events — resolve here to unblock projection
            </p>
          </div>
          <ReviewerAuthControl
            apiKey={reviewerAuth.apiKey}
            onApiKeyChange={reviewerAuth.setApiKey}
            compact
          />
          {stats && (
            <div className="flex items-center gap-4">
              <Stat label="open" value={stats.by_status.open ?? 0} color="text-red-600" />
              <Stat label="resolved" value={stats.by_status.resolved ?? 0} color="text-emerald-600" />
              <Stat label="obsolete" value={stats.by_status.obsolete ?? 0} color="text-stone-400" />
            </div>
          )}
        </div>

        <div className="max-w-6xl mx-auto px-6 py-6 flex gap-6">

          {/* Left: filters + top fields */}
          <div className="w-52 flex-shrink-0 space-y-5">
            <div>
              <div className="text-[10px] text-stone-400 uppercase tracking-widest mb-2"
                style={{ fontFamily: 'var(--ff-mono)' }}>
                Filter by field
              </div>
              <button
                onClick={() => { setFieldFilter(''); load(); }}
                className={`w-full text-left px-3 py-1.5 rounded text-[11px] mb-1 transition-colors ${!fieldFilter ? 'bg-stone-800 text-white' : 'hover:bg-stone-100 text-stone-600'}`}
              >
                All fields
              </button>
              {stats?.top_disputed_fields.map(({ field, open_count }) => (
                <button
                  key={field}
                  onClick={() => { setFieldFilter(field); load(field); }}
                  className={`w-full text-left px-3 py-1.5 rounded text-[11px] flex items-center justify-between mb-0.5 transition-colors ${fieldFilter === field ? 'bg-stone-800 text-white' : 'hover:bg-stone-100 text-stone-600'}`}
                >
                  <span>{fieldLabel(field)}</span>
                  <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${fieldFilter === field ? 'bg-stone-600' : 'bg-red-100 text-red-600'}`}
                    style={{ fontFamily: 'var(--ff-mono)' }}>
                    {open_count}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Right: conflict list */}
          <div className="flex-1 min-w-0">
            {loading && (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="h-24 rounded-xl bg-stone-200 animate-pulse" />
                ))}
              </div>
            )}

            {error && (
              <div className="p-4 rounded-xl bg-red-50 border border-red-200 text-[12px] text-red-700">
                Failed to load conflicts: {error}
                <button onClick={() => load(fieldFilter || undefined)} className="ml-3 underline">
                  Retry
                </button>
              </div>
            )}

            {!loading && !error && items.length === 0 && (
              <div className="text-center py-16">
                <div className="text-[32px] mb-3">✓</div>
                <div className="text-[14px] font-medium text-stone-600">No open conflicts</div>
                <div className="text-[12px] text-stone-400 mt-1">
                  {fieldFilter ? `No conflicts for "${fieldLabel(fieldFilter)}"` : 'All conflicts have been resolved.'}
                </div>
              </div>
            )}

            {!loading && !error && items.length > 0 && (
              <div className="space-y-3">
                <div className="text-[11px] text-stone-400 mb-1">
                  {items.length} open conflict{items.length !== 1 ? 's' : ''}
                  {fieldFilter ? ` for "${fieldLabel(fieldFilter)}"` : ''}
                </div>
                {items.map((item) => (
                  <ConflictRow key={item.conflict_id} item={item} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="text-center">
      <div className={`text-[20px] font-bold ${color}`}>{value}</div>
      <div className="text-[9px] text-stone-400 uppercase tracking-wider"
        style={{ fontFamily: 'var(--ff-mono)' }}>{label}</div>
    </div>
  );
}

function ConflictRow({ item }: { item: ConflictQueueItem }) {
  return (
    <Link
      href={{ pathname: '/', query: { selected: item.event_id, tab: 'technical' } }}
      className="block p-4 bg-white border-2 border-red-100 rounded-xl hover:border-red-300 transition-all group">
      <div className="flex items-start gap-4">
        {/* Left: field + event info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] px-2 py-0.5 rounded bg-red-50 text-red-600 border border-red-100"
              style={{ fontFamily: 'var(--ff-mono)' }}>
              {fieldLabel(item.field_name)}
            </span>
            <SevBadge sev={item.injury_severity} />
          </div>

          {/* Claim comparison */}
          <div className="flex items-center gap-3 text-[12px]">
            <ClaimValue source={item.claim_a_source} value={item.claim_a_value} />
            <span className="text-stone-300 text-[14px]">vs</span>
            <ClaimValue source={item.claim_b_source} value={item.claim_b_value} />
          </div>
        </div>

        {/* Right: event context */}
        <div className="flex-shrink-0 text-right">
          <div className="text-[11px] text-stone-500 font-medium">
            {item.location_text ?? 'Unknown location'}
          </div>
          <div className="text-[10px] text-stone-400 mt-0.5" style={{ fontFamily: 'var(--ff-mono)' }}>
            {item.occurred_date ? formatDateOnly(item.occurred_date.toString()) : '—'}
          </div>
          <div className="text-[9px] text-stone-300 mt-2 group-hover:text-stone-500 transition-colors">
            Review →
          </div>
        </div>
      </div>
    </Link>
  );
}

function ClaimValue({ source, value }: { source: string | null; value: string | null }) {
  return (
    <div className="flex items-baseline gap-1.5">
      {source && (
        <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600"
          style={{ fontFamily: 'var(--ff-mono)' }}>
          {source}
        </span>
      )}
      <span className="font-medium text-stone-800">{value ?? '—'}</span>
    </div>
  );
}
