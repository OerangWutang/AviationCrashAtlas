import { useState, useEffect } from 'react';
import dynamic from 'next/dynamic';
import Head from 'next/head';
import { useRouter } from 'next/router';
import Header from '../components/Header';
import { fetchMapAccidents } from '../lib/api';
import type { MapViewport } from '../lib/api';
import type { MapAccident, MapCluster } from '../types';
import { SEV_COLOR } from '../lib/utils';

// MapView must be dynamically imported with SSR disabled (Leaflet is browser-only)
const MapView = dynamic(() => import('../components/MapView'), { ssr: false });

const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === 'true';

const SEV_ITEMS = [
  { key: 'FATAL', label: 'Fatal' },
  { key: 'SERIOUS', label: 'Serious' },
  { key: 'MINOR', label: 'Minor' },
  { key: 'NONE', label: 'None' },
];

export default function MapPage() {
  const router = useRouter();
  const [sevFilter, setSevFilter] = useState('');
  const [yearFrom, setYearFrom] = useState('');
  const [yearTo, setYearTo] = useState('');
  const [viewport, setViewport] = useState<MapViewport | null>(null);
  const [accidents, setAccidents] = useState<MapAccident[]>([]);
  const [clusters, setClusters] = useState<MapCluster[]>([]);
  const [mapMode, setMapMode] = useState<'points' | 'clusters'>('points');
  const [truncated, setTruncated] = useState(false);
  const [mapLimit, setMapLimit] = useState<number | null>(null);
  const [loading, setLoading] = useState(!USE_MOCK);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (USE_MOCK) {
      setLoading(false);
      return;
    }
    if (!viewport) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);          // clear any previous error before re-fetching
      setTruncated(false);
      fetchMapAccidents({
        severity: sevFilter || undefined,
        year_from: yearFrom || undefined,
        year_to: yearTo || undefined,
        bounds: viewport,
        zoom: viewport.zoom,
      })
        .then((data) => {
          if (!cancelled) {
            setAccidents(data.items);
            setClusters(data.clusters ?? []);
            setMapMode(data.mode ?? ((data.clusters?.length ?? 0) > 0 ? 'clusters' : 'points'));
            setTruncated(data.truncated);
            setMapLimit(data.limit);
            setLoading(false);
          }
        })
        .catch((err) => {
          if (!cancelled) { setError(String(err)); setLoading(false); }
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [sevFilter, yearFrom, yearTo, viewport]);

  const handleSelect = (id: string) => {
    router.push(`/?selected=${id}`);
  };

  return (
    <>
      <Head>
        <title>Aviation Safety Atlas — Map</title>
      </Head>

      <div className="flex flex-col h-screen overflow-hidden">
        <Header />

        {/* Truncation warning — shown when the cap was hit */}
        {truncated && (
          <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-[12px] text-amber-700 flex items-center gap-2">
            <span>⚠</span>
            <span>
              Showing the first {mapLimit?.toLocaleString()} {mapMode === 'clusters' ? 'clusters' : 'geocoded accidents'} in the current viewport.
              Use severity, year filters, or zoom in to narrow the dataset and see more specific results.
            </span>
          </div>
        )}

        <div className="flex-1 relative">
          {/* Map overlaid controls */}
          <div className="absolute top-3 left-3 z-10 bg-white/95 border border-stone-200 rounded-lg shadow-sm p-3 text-[12px]">
            <div
              className="text-[10px] text-stone-400 uppercase tracking-wider mb-2"
              style={{ fontFamily: 'var(--ff-mono)' }}
            >
              Filter severity
            </div>
            <div className="flex flex-col gap-1">
              <button
                onClick={() => setSevFilter('')}
                className={`text-left px-2 py-1 rounded text-[11px] ${sevFilter === '' ? 'bg-blue-100 text-blue-700 font-medium' : 'text-stone-500 hover:bg-stone-50'}`}
                style={{ fontFamily: 'var(--ff-mono)' }}
              >
                All
              </button>
              {SEV_ITEMS.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setSevFilter(s.key)}
                  className={`flex items-center gap-2 text-left px-2 py-1 rounded text-[11px] ${sevFilter === s.key ? 'bg-blue-100 text-blue-700 font-medium' : 'text-stone-500 hover:bg-stone-50'}`}
                  style={{ fontFamily: 'var(--ff-mono)' }}
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ background: SEV_COLOR[s.key] }}
                  />
                  {s.label}
                </button>
              ))}
            </div>

            <div
              className="mt-3 pt-3 border-t border-stone-100"
              style={{ fontFamily: 'var(--ff-mono)' }}
            >
              <div className="text-[10px] text-stone-400 uppercase tracking-wider mb-2">
                Filter years
              </div>
              <div className="flex gap-2">
                <input
                  aria-label="Year from"
                  type="number"
                  min={1919}
                  max={2100}
                  placeholder="from"
                  value={yearFrom}
                  onChange={(e) => setYearFrom(e.target.value)}
                  className="w-20 border border-stone-200 rounded px-2 py-1 text-[11px]"
                />
                <input
                  aria-label="Year to"
                  type="number"
                  min={1919}
                  max={2100}
                  placeholder="to"
                  value={yearTo}
                  onChange={(e) => setYearTo(e.target.value)}
                  className="w-20 border border-stone-200 rounded px-2 py-1 text-[11px]"
                />
              </div>
            </div>
            <div
              className="mt-2 pt-2 border-t border-stone-100 text-[10px] text-stone-400"
              style={{ fontFamily: 'var(--ff-mono)' }}
            >
              {loading ? '…' : error ? 'error' : mapMode === 'clusters' ? `${clusters.length.toLocaleString()} clusters` : `${accidents.length.toLocaleString()} accidents`}
            </div>
          </div>

          {error && (
            <div className="absolute top-3 right-3 z-10 bg-red-50 border border-red-200 rounded-lg p-3 text-[12px] text-red-600">
              {error}
            </div>
          )}

          <MapView accidents={accidents} clusters={clusters} onSelect={handleSelect} onBoundsChange={setViewport} />
        </div>
      </div>
    </>
  );
}
