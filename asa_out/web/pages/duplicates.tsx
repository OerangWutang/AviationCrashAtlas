import { useEffect, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import ReviewerAuthControl from '../components/ReviewerAuthControl';
import { useReviewerAuth } from '../hooks/useReviewerAuth';
import type { AccidentDetail, DuplicateCandidate } from '../types';
import { confirmDuplicateCandidate, fetchAccident, fetchDuplicateCandidatesWithAuth, rejectDuplicateCandidate } from '../lib/api';

function scoreLabel(score: number) {
  return `${Math.round(score * 100)}%`;
}

function EventSummary({ event }: { event: AccidentDetail | null }) {
  if (!event) return <div className="text-[12px] text-stone-400">Loading event…</div>;
  return (
    <div className="text-[12px] space-y-1">
      <div className="font-semibold text-stone-800">{event.canonical_id}</div>
      <div>{event.occurred_date ?? 'unknown date'} · {event.location_text ?? 'unknown location'}</div>
      <div>{event.aircraft_make ?? '—'} {event.aircraft_model ?? ''} · {event.aircraft_registration ?? 'no reg'}</div>
      <div>{event.operator_name ?? 'unknown operator'} · fatalities {event.fatalities_total ?? 'unknown'}</div>
    </div>
  );
}

function DuplicateRow({ item, apiKey, onDone }: { item: DuplicateCandidate; apiKey: string; onDone: () => void }) {
  const [sourceEvent, setSourceEvent] = useState<AccidentDetail | null>(null);
  const [candidateEvent, setCandidateEvent] = useState<AccidentDetail | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (item.source_event_id) fetchAccident(item.source_event_id).then(setSourceEvent).catch(() => setSourceEvent(null));
    fetchAccident(item.candidate_event_id).then(setCandidateEvent).catch(() => setCandidateEvent(null));
  }, [item.source_event_id, item.candidate_event_id]);

  async function decide(kind: 'confirm' | 'reject') {
    setBusy(true); setError(null);
    try {
      if (kind === 'confirm') await confirmDuplicateCandidate(item.id, apiKey, note || undefined);
      else await rejectDuplicateCandidate(item.id, apiKey, note || undefined);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <article className="bg-white border border-stone-200 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10px] px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200" style={{ fontFamily: 'var(--ff-mono)' }}>{item.match_type}</span>
        <span className="text-[10px] px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100" style={{ fontFamily: 'var(--ff-mono)' }}>{scoreLabel(item.match_score)} match</span>
        <span className="text-[10px] text-stone-400">created {new Date(item.created_at).toLocaleString()}</span>
      </div>
      <div className="grid md:grid-cols-2 gap-3">
        <div className="rounded-lg border border-stone-100 p-3">
          <div className="text-[10px] uppercase tracking-wider text-stone-400 mb-2">incoming/source-side event</div>
          <EventSummary event={sourceEvent} />
        </div>
        <div className="rounded-lg border border-stone-100 p-3">
          <div className="text-[10px] uppercase tracking-wider text-stone-400 mb-2">candidate existing event</div>
          <EventSummary event={candidateEvent} />
        </div>
      </div>
      <div className="mt-3 text-[11px] text-stone-500">
        <span className="font-medium">Why suggested:</span> {(item.match_reasons ?? []).join(', ') || 'No explanation recorded'}
      </div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reviewer note before confirm/reject" className="mt-3 w-full border border-stone-200 rounded p-2 text-[12px]" />
      {error && <div className="mt-2 text-[12px] text-red-600">{error}</div>}
      <div className="mt-3 flex gap-2">
        <button disabled={busy || !apiKey} onClick={() => decide('confirm')} className="px-3 py-1.5 rounded bg-emerald-600 text-white text-[12px] disabled:opacity-40">Confirm merge</button>
        <button disabled={busy || !apiKey} onClick={() => decide('reject')} className="px-3 py-1.5 rounded bg-stone-200 text-stone-700 text-[12px] disabled:opacity-40">Reject candidate</button>
      </div>
    </article>
  );
}

export default function DuplicatesPage() {
  const auth = useReviewerAuth();
  const [items, setItems] = useState<DuplicateCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try { setItems(await fetchDuplicateCandidatesWithAuth(auth.apiKey, 'pending', 100)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (auth.hydrated) load(); }, [auth.hydrated, auth.apiKey]);

  return <><Head><title>Duplicate Review — Aviation Safety Atlas</title></Head><main className="min-h-screen bg-stone-50">
    <header className="border-b border-stone-200 bg-white px-6 py-4 flex items-center gap-4"><Link href="/operator" className="text-[11px] text-stone-400">← operator</Link><div className="flex-1"><h1 className="text-[16px] font-semibold">Duplicate Review</h1><p className="text-[11px] text-stone-400">Side-by-side merge candidates with transparent match reasons.</p></div><ReviewerAuthControl apiKey={auth.apiKey} onApiKeyChange={auth.setApiKey} compact /></header>
    <section className="max-w-6xl mx-auto p-6 space-y-3">
      {loading && <div className="text-[12px] text-stone-500">Loading…</div>}
      {error && <div className="p-3 rounded bg-red-50 text-red-700 text-[12px]">{error}</div>}
      {!loading && !error && items.length === 0 && <div className="text-center py-16 text-stone-500">No pending duplicate candidates.</div>}
      {items.map((item) => <DuplicateRow key={item.id} item={item} apiKey={auth.apiKey} onDone={load} />)}
    </section>
  </main></>;
}
