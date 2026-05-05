import { useEffect, useRef } from 'react';
import type { MapAccident, MapCluster } from '../types';
import { SEV_COLOR } from '../lib/utils';

interface MapViewport {
  north: number;
  south: number;
  east: number;
  west: number;
  zoom: number;
}

interface Props {
  accidents: MapAccident[];
  clusters?: MapCluster[];
  onSelect: (id: string) => void;
  onBoundsChange?: (bounds: MapViewport) => void;
}

/**
 * Fixes from review:
 * 1. Uses real location_lat / location_lon from API (not hardcoded mock coords)
 * 2. Marker layer is rebuilt when accidents prop changes
 * 3. Popup is built with DOM nodes + textContent — no innerHTML for data values,
 *    preventing injection of HTML from external source fields.
 */
export default function MapView({ accidents, clusters = [], onSelect, onBoundsChange }: Props) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<unknown>(null);
  const markersRef = useRef<unknown[]>([]);
  const onBoundsChangeRef = useRef(onBoundsChange);
  // Track onSelect via ref so a new identity from the parent (e.g. handleSelect
  // recreated on every render of MapPage) does not cause the marker-rebuild
  // effect below to re-run on every parent render. The marker effect should
  // only rebuild when accidents/clusters actually change.
  const onSelectRef = useRef(onSelect);

  useEffect(() => {
    onBoundsChangeRef.current = onBoundsChange;
  }, [onBoundsChange]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  // Build/rebuild markers whenever accidents list changes
  useEffect(() => {
    if (!mapRef.current) return;

    import('leaflet').then((L) => {
      // Init map once
      if (!mapInstanceRef.current) {
        delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
        L.Icon.Default.mergeOptions({
          iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
          iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
          shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
        });
        const map = L.map(mapRef.current!, { center: [37.5, -96], zoom: 4 });
        L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
          attribution: '© OpenStreetMap contributors © CARTO',
          subdomains: 'abcd',
          maxZoom: 19,
        }).addTo(map);
        mapInstanceRef.current = map;

        const emitBounds = () => {
          const cb = onBoundsChangeRef.current;
          if (!cb) return;
          const b = map.getBounds();
          cb({
            north: b.getNorth(),
            south: b.getSouth(),
            east: b.getEast(),
            west: b.getWest(),
            zoom: map.getZoom(),
          });
        };
        emitBounds();
        map.on('moveend', emitBounds);
        map.on('zoomend', emitBounds);

        // Legend
        const legend = new (L.Control.extend({
          options: { position: 'bottomleft' },
          onAdd() {
            const div = L.DomUtil.create('div');
            div.style.cssText = 'background:rgba(255,255,255,.93);padding:10px 12px;border-radius:8px;font-family:DM Mono,monospace;font-size:10px;box-shadow:0 2px 8px rgba(0,0,0,.12);border:1px solid #E7E5E0;';
            div.innerHTML = [['#E24B4A','Fatal'],['#EF9F27','Serious'],['#639922','Minor'],['#888780','None']]
              .map(([c,l]) => `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px"><span style="width:10px;height:10px;border-radius:50%;background:${c};display:inline-block;opacity:.8"></span><span style="color:#44403C">${l}</span></div>`)
              .join('');
            return div;
          },
        }))();
        legend.addTo(map as unknown as L.Map);
      }

      const map = mapInstanceRef.current as L.Map;

      // Clear old markers (fix: markers update when filter changes)
      (markersRef.current as L.Layer[]).forEach((m) => map.removeLayer(m));
      markersRef.current = [];

      if (clusters.length > 0) {
        clusters.forEach((c) => {
          const radius = Math.min(28, Math.max(12, 8 + Math.log2(c.count + 1) * 4));
          const marker = L.circleMarker([c.location_lat, c.location_lon], {
            radius,
            color: '#1C1B19',
            weight: 1.5,
            fillColor: '#185FA5',
            fillOpacity: 0.72,
          }).addTo(map);

          marker.on('click', () => {
            map.setView([c.location_lat, c.location_lon], Math.min(map.getZoom() + 2, 12));
          });

          const popupDiv = document.createElement('div');
          popupDiv.style.cssText = 'font-family:DM Sans,sans-serif;min-width:190px';

          const titleEl = document.createElement('div');
          titleEl.style.cssText = 'font-size:14px;font-weight:700;color:#1C1B19;margin-bottom:6px';
          titleEl.textContent = `${c.count.toLocaleString()} accidents`;
          popupDiv.appendChild(titleEl);

          const fatalEl = document.createElement('div');
          fatalEl.style.cssText = 'font-size:12px;color:#44403C;margin-bottom:4px';
          fatalEl.textContent = `${c.fatalities_total.toLocaleString()} fatalities in cluster`;
          popupDiv.appendChild(fatalEl);

          const yearEl = document.createElement('div');
          yearEl.style.cssText = 'font-size:12px;color:#44403C;margin-bottom:8px';
          yearEl.textContent = c.latest_occurred_year ? `Latest year: ${c.latest_occurred_year}` : 'Latest year: —';
          popupDiv.appendChild(yearEl);

          const hintEl = document.createElement('div');
          hintEl.style.cssText = 'font-size:11px;color:#78716C;font-family:DM Mono,monospace';
          hintEl.textContent = 'Click cluster to zoom in';
          popupDiv.appendChild(hintEl);

          marker.bindPopup(popupDiv);
          markersRef.current.push(marker);
        });
      } else {
        accidents.forEach((a) => {
          // Use real coordinates from API (fixes hardcoded mock coords)
          if (a.location_lat == null || a.location_lon == null) return;

          const sev = a.injury_severity ?? 'UNKNOWN';
          const color = SEV_COLOR[sev] ?? SEV_COLOR.UNKNOWN;
          const radius = (a.fatalities_total ?? 0) > 5 ? 12 : (a.fatalities_total ?? 0) > 0 ? 9 : 7;
          const aircraft = [a.aircraft_make, a.aircraft_model].filter(Boolean).join(' ') || 'Unknown aircraft';

          const marker = L.circleMarker([a.location_lat, a.location_lon], {
            radius, color: '#fff', weight: 1.5, fillColor: color, fillOpacity: 0.8,
          }).addTo(map);

          // Build popup with DOM nodes — do NOT use innerHTML for data values
          // (canonical_id, aircraft, location_text all come from external sources).
          const popupDiv = document.createElement('div');
          popupDiv.style.cssText = 'font-family:DM Sans,sans-serif;min-width:200px';

          const idEl = document.createElement('div');
          idEl.style.cssText = 'font-size:10px;color:#78716C;font-family:DM Mono,monospace;margin-bottom:4px';
          idEl.textContent = a.canonical_id;
          popupDiv.appendChild(idEl);

          const titleEl = document.createElement('div');
          titleEl.style.cssText = 'font-size:14px;font-weight:600;color:#1C1B19;margin-bottom:6px';
          titleEl.textContent = aircraft;
          popupDiv.appendChild(titleEl);

          const locEl = document.createElement('div');
          locEl.style.cssText = 'font-size:12px;color:#44403C;margin-bottom:4px';
          locEl.textContent = `📍 ${a.location_text ?? '—'}`;
          popupDiv.appendChild(locEl);

          const dateEl = document.createElement('div');
          dateEl.style.cssText = 'font-size:12px;color:#44403C;margin-bottom:8px';
          dateEl.textContent = `${a.occurred_date ?? a.occurred_year ?? '—'} · ${a.phase_of_flight ?? '—'}`;
          popupDiv.appendChild(dateEl);

          const btn = document.createElement('button');
          btn.style.cssText = 'width:100%;font-size:12px;padding:6px;background:#185FA5;color:#fff;border:none;border-radius:6px;cursor:pointer;font-family:DM Mono,monospace';
          btn.textContent = 'View record →';
          btn.addEventListener('click', () => {
            onSelectRef.current(a.id);
            map.closePopup();
          });
          popupDiv.appendChild(btn);

          marker.bindPopup(popupDiv);
          markersRef.current.push(marker);
        });
      }    });
  }, [accidents, clusters]);

  useEffect(() => {
    return () => {
      if (mapInstanceRef.current) {
        (mapInstanceRef.current as { remove: () => void }).remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  return (
    <>
      <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
      <div ref={mapRef} id="accident-map" className="w-full h-full" />
    </>
  );
}
