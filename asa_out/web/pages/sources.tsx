import Head from 'next/head';
import Header from '../components/Header';

const SOURCES = [
  {
    id: 'ntsb', short: 'NTSB', name: 'National Transportation Safety Board',
    tier: 1, license: 'Public domain', status: 'active',
    coverage: 'US accidents 1962–present (~90,000 records)',
    url: 'https://www.ntsb.gov',
    apiUrl: 'https://data.ntsb.gov/carol-main-public/api',
    notes: 'US government work — no restrictions. Attribute in UI. Re-ingestible from official API or CSV bulk export.',
    ingestionEnabled: true,
  },
  {
    id: 'asn', short: 'ASN', name: 'Aviation Safety Network',
    tier: 2, license: 'Licensed (contact required)', status: 'pending',
    coverage: 'Global accidents 1919–present, hijackings',
    url: 'https://aviation-safety.net',
    apiUrl: null,
    notes: 'Proprietary database. Do not scrape. Contact ASN for research/licensing agreement. Currently shown as linked reference only.',
    ingestionEnabled: false,
  },
  {
    id: 'icao', short: 'ICAO', name: 'ICAO e-Library',
    tier: 3, license: 'Public reports (link only)', status: 'phase2',
    coverage: 'Major accident final investigation reports (PDF)',
    url: 'https://www.icao.int/safety/airnavigation/AIG/Pages/ICAO-Accident-Incident-Data-Reporting.aspx',
    apiUrl: null,
    notes: 'Official final reports available as public PDFs. Safe to link; bulk text extraction needs legal review. Planned for Phase 2.',
    ingestionEnabled: false,
  },
  {
    id: 'baaa', short: 'BAAA', name: 'Bureau of Aircraft Accidents Archives',
    tier: 4, license: 'Commercial license required', status: 'blocked',
    coverage: 'Global accidents, supplementary',
    url: 'https://www.baaa-acro.com',
    apiUrl: null,
    notes: 'Swiss private organization. Commercial license must be purchased before any use. Do not use without agreement.',
    ingestionEnabled: false,
  },
];

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  phase2: 'bg-blue-50 text-blue-700 border-blue-200',
  blocked: 'bg-red-50 text-red-700 border-red-200',
};

const STATUS_LABELS: Record<string, string> = {
  active: '✓ Active',
  pending: '⏸ Contact required',
  phase2: '→ Phase 2',
  blocked: '✗ License required',
};

const TIER_LABELS: Record<number, string> = {
  1: 'Primary / official',
  2: 'Historical / global',
  3: 'Reports / validation',
  4: 'Supplementary',
};

export default function SourcesPage() {
  return (
    <>
      <Head><title>Aviation Safety Atlas — Sources</title></Head>
      <div className="flex flex-col h-screen overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-3xl mx-auto">
            <h1
              className="text-[22px] text-stone-800 mb-2"
              style={{ fontFamily: 'var(--ff-serif)' }}
            >
              Source Registry
            </h1>
            <p className="text-[13px] text-stone-500 mb-6 leading-relaxed">
              Every claim in this platform traces to a named source in this registry.
              Sources have different access patterns, license types, and trust tiers.
              Tier 1 sources take precedence in conflict resolution.
            </p>

            <div className="space-y-4">
              {SOURCES.map((src) => (
                <div
                  key={src.id}
                  className="bg-white border border-stone-200 rounded-xl overflow-hidden"
                >
                  <div className="flex items-center gap-4 p-4 border-b border-stone-100 bg-stone-50">
                    <div
                      className="w-12 h-12 rounded-lg bg-blue-50 text-blue-700 flex items-center justify-center text-[11px] font-medium flex-shrink-0"
                      style={{ fontFamily: 'var(--ff-mono)' }}
                    >
                      {src.short}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[15px] font-medium text-stone-800">{src.name}</div>
                      <div className="text-[12px] text-stone-400">{src.coverage}</div>
                    </div>
                    <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${STATUS_STYLES[src.status]}`}
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        {STATUS_LABELS[src.status]}
                      </span>
                      <span
                        className="text-[10px] px-2 py-0.5 rounded-full border bg-stone-100 text-stone-500 border-stone-200"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        tier {src.tier} · {TIER_LABELS[src.tier]}
                      </span>
                    </div>
                  </div>

                  <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <Label>License</Label>
                      <Value>{src.license}</Value>
                    </div>
                    <div>
                      <Label>Ingestion</Label>
                      <Value>
                        {src.ingestionEnabled
                          ? '✓ Enabled — runs on schedule'
                          : '✗ Disabled — see notes'}
                      </Value>
                    </div>
                    <div>
                      <Label>Website</Label>
                      <a
                        href={src.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[12px] text-blue-600 source-link"
                        style={{ fontFamily: 'var(--ff-mono)' }}
                      >
                        {src.url}
                      </a>
                    </div>
                    {src.apiUrl && (
                      <div>
                        <Label>API endpoint</Label>
                        <a
                          href={src.apiUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[12px] text-blue-600 source-link"
                          style={{ fontFamily: 'var(--ff-mono)' }}
                        >
                          {src.apiUrl}
                        </a>
                      </div>
                    )}
                    <div className="md:col-span-2">
                      <Label>Compliance notes</Label>
                      <p className="text-[12px] text-stone-600 leading-relaxed">{src.notes}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div
              className="mt-8 p-4 bg-amber-50 border border-amber-200 rounded-xl text-[12px] text-amber-800 leading-relaxed"
            >
              <strong>Accuracy disclaimer:</strong> This platform is for research and informational purposes only.
              Do not use for safety-critical decisions. All data sourced from official or licensed providers
              and attributed accordingly. Source-completeness scores reflect data quality and coverage, not legal determinations.
            </div>
          </div>
        </main>
      </div>
    </>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="text-[10px] text-stone-400 uppercase tracking-wider mb-1"
      style={{ fontFamily: 'var(--ff-mono)' }}
    >
      {children}
    </div>
  );
}

function Value({ children }: { children: React.ReactNode }) {
  return <div className="text-[12px] text-stone-700">{children}</div>;
}
