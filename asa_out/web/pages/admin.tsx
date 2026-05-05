import { useEffect, useState, type ReactNode } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import ReviewerAuthControl from '../components/ReviewerAuthControl';
import { useReviewerAuth } from '../hooks/useReviewerAuth';
import type { ApiKeyRecord, ArchiveManifest, AuditLogItem, CreatedApiKey, IngestionRun, SourceDocument, SourceStatus } from '../types';
import { createApiKey, fetchApiKeys, fetchArchiveManifests, fetchAuditLog, fetchIngestionRuns, fetchSourceDocuments, fetchSourceStatus, reviewSourceDocument, revokeApiKey, verifyArchiveManifest } from '../lib/api';

type Tab = 'sources' | 'ingestion' | 'archives' | 'audit' | 'keys' | 'documents';

function Badge({ children }: { children: ReactNode }) {
  return <span className="px-2 py-0.5 rounded border border-stone-200 bg-stone-50 text-[10px]" style={{ fontFamily: 'var(--ff-mono)' }}>{children}</span>;
}

export default function AdminPage() {
  const auth = useReviewerAuth();
  const [tab, setTab] = useState<Tab>('sources');
  return <><Head><title>Admin Console — Aviation Safety Atlas</title></Head><main className="min-h-screen bg-stone-50">
    <header className="border-b border-stone-200 bg-white px-6 py-4 flex items-center gap-4"><Link href="/operator" className="text-[11px] text-stone-400">← operator</Link><div className="flex-1"><h1 className="text-[16px] font-semibold">Admin Console</h1><p className="text-[11px] text-stone-400">Operational inspection: sources, ingestion, archives, audit trail, API keys, and documents.</p></div><ReviewerAuthControl apiKey={auth.apiKey} onApiKeyChange={auth.setApiKey} compact /></header>
    <div className="max-w-7xl mx-auto p-6">
      <nav className="flex flex-wrap gap-2 mb-4">{(['sources','ingestion','archives','audit','keys','documents'] as Tab[]).map((t) => <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 rounded text-[12px] ${tab === t ? 'bg-stone-800 text-white' : 'bg-white border border-stone-200 text-stone-600'}`}>{t}</button>)}</nav>
      {tab === 'sources' && <SourceStatusPanel apiKey={auth.apiKey} />}
      {tab === 'ingestion' && <IngestionPanel apiKey={auth.apiKey} />}
      {tab === 'archives' && <ArchivePanel apiKey={auth.apiKey} />}
      {tab === 'audit' && <AuditPanel apiKey={auth.apiKey} />}
      {tab === 'keys' && <KeysPanel apiKey={auth.apiKey} />}
      {tab === 'documents' && <DocumentsPanel apiKey={auth.apiKey} />}
    </div>
  </main></>;
}

function useLoad<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function load() { setLoading(true); setError(null); try { setData(await loader()); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setLoading(false); } }
  useEffect(() => { load(); }, deps);
  return { data, error, loading, reload: load };
}

function SourceStatusPanel({ apiKey }: { apiKey: string }) {
  const { data, error, loading } = useLoad<SourceStatus[]>(() => fetchSourceStatus(apiKey), [apiKey]);
  return <Panel title="Source freshness/status" error={error} loading={loading}><table className="w-full text-[12px]"><thead><tr className="text-left text-stone-400"><th>Source</th><th>Tier</th><th>Enabled</th><th>Latest run</th><th>Age</th></tr></thead><tbody>{(data ?? []).map((s) => <tr key={s.id} className="border-t"><td className="py-2 font-medium">{s.short_name}<div className="text-[10px] text-stone-400">{s.display_name}</div></td><td>{s.tier}</td><td>{s.ingestion_enabled ? 'yes' : 'no'}</td><td>{s.latest_run_status ?? 'none'}<div className="text-[10px] text-stone-400">{s.latest_run_completed_at ? new Date(s.latest_run_completed_at).toLocaleString() : 'never'}</div></td><td>{s.freshness_age_seconds == null ? '—' : `${Math.round(s.freshness_age_seconds / 3600)}h`}</td></tr>)}</tbody></table></Panel>;
}

function IngestionPanel({ apiKey }: { apiKey: string }) {
  const { data, error, loading } = useLoad<IngestionRun[]>(() => fetchIngestionRuns(apiKey), [apiKey]);
  return <Panel title="Recent ingestion runs" error={error} loading={loading}><div className="space-y-2">{(data ?? []).map((r) => <div key={r.id} className="border rounded-lg p-3 bg-white"><div className="flex gap-2 items-center"><Badge>{r.status}</Badge><span className="font-medium text-[13px]">{r.source_name}</span><span className="text-[11px] text-stone-400">{new Date(r.started_at).toLocaleString()}</span></div><div className="grid grid-cols-2 md:grid-cols-6 gap-2 mt-2 text-[11px] text-stone-500"><span>fetched {r.records_fetched}</span><span>new {r.snapshots_new}</span><span>events {r.events_created}/{r.events_updated}</span><span>claims {r.claims_written}</span><span>ingest errors {r.ingestion_errors}</span><span>projection errors {r.projection_errors}</span></div>{r.errors?.length ? <pre className="mt-2 text-[10px] bg-red-50 p-2 rounded overflow-auto">{r.errors.join('\n')}</pre> : null}</div>)}</div></Panel>;
}

function ArchivePanel({ apiKey }: { apiKey: string }) {
  const { data, error, loading, reload } = useLoad<ArchiveManifest[]>(() => fetchArchiveManifests(apiKey), [apiKey]);
  const [verifyResult, setVerifyResult] = useState<Record<string, unknown> | null>(null);
  async function verify(id: string) { setVerifyResult(await verifyArchiveManifest(id, apiKey)); }
  return <Panel title="Archive manifests" error={error} loading={loading}><div className="space-y-2">{(data ?? []).map((m) => <div key={m.id} className="border rounded-lg p-3 bg-white"><div className="flex gap-2 items-center"><Badge>{m.status}</Badge><span className="font-medium text-[13px]">{m.archive_type}</span><span className="text-[11px] text-stone-400">{m.id}</span></div><div className="text-[11px] text-stone-500 mt-1">cutoff {new Date(m.cutoff_at).toLocaleString()} · {m.output_uri}</div><button onClick={() => verify(m.id)} className="mt-2 px-2 py-1 rounded bg-stone-800 text-white text-[11px]">verify integrity</button></div>)}</div>{verifyResult && <pre className="mt-4 text-[11px] bg-stone-100 rounded p-3 overflow-auto">{JSON.stringify(verifyResult, null, 2)}</pre>}<button onClick={reload} className="mt-3 text-[11px] underline">refresh</button></Panel>;
}

function AuditPanel({ apiKey }: { apiKey: string }) {
  const { data, error, loading } = useLoad<AuditLogItem[]>(() => fetchAuditLog(apiKey), [apiKey]);
  return <Panel title="Audit log" error={error} loading={loading}><div className="space-y-2">{(data ?? []).map((i) => <div key={`${i.kind}-${i.id}`} className="border rounded-lg p-3 bg-white text-[12px]"><div className="flex gap-2"><Badge>{i.kind}</Badge><span className="font-medium">{i.action ?? 'action'}</span><span className="text-stone-400">{i.occurred_at ? new Date(i.occurred_at).toLocaleString() : 'unknown time'}</span></div><div className="text-stone-500 mt-1">actor {i.actor ?? 'unknown'} · event {i.event_id ?? '—'}</div><div className="text-stone-600 mt-1">{i.description ?? '—'}</div></div>)}</div></Panel>;
}

function KeysPanel({ apiKey }: { apiKey: string }) {
  const { data, error, loading, reload } = useLoad<ApiKeyRecord[]>(() => fetchApiKeys(apiKey), [apiKey]);
  const [operatorId, setOperatorId] = useState('');
  const [role, setRole] = useState<'reviewer' | 'admin'>('reviewer');
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  async function create() { const row = await createApiKey(apiKey, { operator_id: operatorId, role }); setCreated(row); setOperatorId(''); await reload(); }
  async function revoke(id: string) { await revokeApiKey(id, apiKey); await reload(); }
  return <Panel title="API keys" error={error} loading={loading}><div className="bg-amber-50 border border-amber-200 rounded p-3 text-[12px] text-amber-800 mb-3">Raw keys are shown once. Copy immediately.</div>{created && <pre className="bg-emerald-50 border border-emerald-200 rounded p-3 text-[11px] overflow-auto mb-3">{created.raw_key}</pre>}<div className="flex gap-2 mb-3"><input value={operatorId} onChange={(e) => setOperatorId(e.target.value)} placeholder="operator email/name" className="border rounded px-2 py-1 text-[12px]" /><select value={role} onChange={(e) => setRole(e.target.value as 'reviewer' | 'admin')} className="border rounded px-2 py-1 text-[12px]"><option value="reviewer">reviewer</option><option value="admin">admin</option></select><button disabled={!operatorId || !apiKey} onClick={create} className="px-3 py-1 rounded bg-stone-800 text-white text-[12px] disabled:opacity-40">create key</button></div><div className="space-y-2">{(data ?? []).map((k) => <div key={k.id} className="border rounded-lg p-3 bg-white text-[12px] flex items-center gap-3"><Badge>{k.role}</Badge><span className="flex-1"><span className="font-medium">{k.operator_id}</span><span className="text-stone-400 ml-2">{k.id}</span></span><span>{k.is_active ? 'active' : 'revoked'}</span>{k.is_active && <button onClick={() => revoke(k.id)} className="px-2 py-1 rounded bg-red-50 text-red-700 border border-red-100 text-[11px]">revoke</button>}</div>)}</div></Panel>;
}

function DocumentsPanel({ apiKey }: { apiKey: string }) {
  const { data, error, loading, reload } = useLoad<SourceDocument[]>(() => fetchSourceDocuments(apiKey), [apiKey]);
  async function markFinal(doc: SourceDocument) { await reviewSourceDocument(doc.id, apiKey, { document_type: 'final_report', url_verified: true, is_available: true }); await reload(); }
  return <Panel title="Source documents" error={error} loading={loading}><div className="space-y-2">{(data ?? []).map((d) => <div key={d.id} className="border rounded-lg p-3 bg-white text-[12px]"><div className="flex gap-2 items-center"><Badge>{d.document_type}</Badge><span className="font-medium truncate">{d.title ?? d.url}</span></div><div className="text-[11px] text-stone-500 mt-1">event {d.event_id} · source {d.source_id} · verified {String(d.url_verified)} · available {String(d.is_available)}</div><button onClick={() => markFinal(d)} disabled={!apiKey} className="mt-2 px-2 py-1 rounded bg-blue-600 text-white text-[11px] disabled:opacity-40">mark verified final report</button></div>)}</div></Panel>;
}

function Panel({ title, error, loading, children }: { title: string; error: string | null; loading: boolean; children: ReactNode }) {
  return <section className="bg-white border border-stone-200 rounded-xl p-4"><h2 className="font-semibold text-stone-800 mb-3">{title}</h2>{loading && <div className="text-[12px] text-stone-500">Loading…</div>}{error && <div className="p-3 rounded bg-red-50 text-red-700 text-[12px] mb-3">{error}</div>}{!loading && !error && children}</section>;
}
