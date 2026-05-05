import { useEffect, useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import ReviewerAuthControl from '../components/ReviewerAuthControl';
import { useReviewerAuth } from '../hooks/useReviewerAuth';
import type { DataQualityIssue } from '../types';
import { fetchDataQualityIssuesWithAuth, resolveDataQualityIssue } from '../lib/api';

export default function DataQualityPage() {
  const auth = useReviewerAuth();
  const [items, setItems] = useState<DataQualityIssue[]>([]);
  const [status, setStatus] = useState('open');
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try { setItems(await fetchDataQualityIssuesWithAuth(auth.apiKey, status)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  useEffect(() => { if (auth.hydrated) load(); }, [auth.hydrated, auth.apiKey, status]);

  async function resolveIssue(id: string) {
    try { await resolveDataQualityIssue(id, auth.apiKey, notes[id] || undefined); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  return <><Head><title>Data Quality — Aviation Safety Atlas</title></Head><main className="min-h-screen bg-stone-50">
    <header className="border-b border-stone-200 bg-white px-6 py-4 flex items-center gap-4"><Link href="/operator" className="text-[11px] text-stone-400">← operator</Link><div className="flex-1"><h1 className="text-[16px] font-semibold">Data-quality issues</h1><p className="text-[11px] text-stone-400">First-class review queue for inconsistent source values and split totals.</p></div><ReviewerAuthControl apiKey={auth.apiKey} onApiKeyChange={auth.setApiKey} compact /></header>
    <section className="max-w-5xl mx-auto p-6">
      <div className="mb-4 flex gap-2"><button onClick={() => setStatus('open')} className={`px-3 py-1 rounded text-[12px] ${status === 'open' ? 'bg-stone-800 text-white' : 'bg-white border'}`}>Open</button><button onClick={() => setStatus('resolved')} className={`px-3 py-1 rounded text-[12px] ${status === 'resolved' ? 'bg-stone-800 text-white' : 'bg-white border'}`}>Resolved</button></div>
      {error && <div className="p-3 rounded bg-red-50 text-red-700 text-[12px] mb-3">{error}</div>}
      <div className="space-y-3">{items.map((item) => <article key={item.id} className="bg-white border border-stone-200 rounded-xl p-4">
        <div className="flex items-center gap-2"><span className="text-[10px] px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-100">{item.severity}</span><span className="font-medium text-[13px]">{item.issue_code}</span><span className="text-[11px] text-stone-400">{item.field_name}</span></div>
        <div className="text-[11px] text-stone-500 mt-2" style={{ fontFamily: 'var(--ff-mono)' }}>event {item.event_id} · source {item.source_id ?? 'unknown'}</div>
        <pre className="mt-2 bg-stone-50 border border-stone-100 rounded p-2 text-[11px] overflow-auto">{JSON.stringify(item.details ?? {}, null, 2)}</pre>
        {item.status === 'open' && <><textarea value={notes[item.id] ?? ''} onChange={(e) => setNotes({ ...notes, [item.id]: e.target.value })} placeholder="Resolution note / waiver reason" className="mt-3 w-full border border-stone-200 rounded p-2 text-[12px]" /><button disabled={!auth.apiKey} onClick={() => resolveIssue(item.id)} className="mt-2 px-3 py-1.5 rounded bg-emerald-600 text-white text-[12px] disabled:opacity-40">Resolve / waive</button></>}
      </article>)}</div>
      {items.length === 0 && <div className="text-center py-16 text-stone-500">No {status} issues.</div>}
    </section>
  </main></>;
}
