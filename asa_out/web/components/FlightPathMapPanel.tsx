/**
 * FlightPathMapPanel
 *
 * Advanced accident flight path reconstruction panel using Leaflet (already
 * installed as the project's map library).
 *
 * Rendering contract (MUST be preserved in all future changes)
 * ─────────────────────────────────────────────────────────────
 * - Recorded/observed path  → solid blue polyline (#185FA5), weight 3
 * - Estimated/inferred path → dashed amber polyline (#D97706), weight 2, dashArray "6 5"
 * - Disputed segment        → red-violet polyline (#7C3AED), weight 2, dashArray "3 4"
 * - Last recorded point     → blue circle marker, popup labelled "Last Recorded"
 * - Impact/accident site    → red star SVG marker
 * - GPWS/warning annotation → yellow triangle marker
 * - Other annotations       → orange circle marker
 * - Disputed points         → red circle border
 * - Low confidence (< 0.5)  → 60% opacity
 * - Uncertainty circles     → light blue semi-transparent circle if uncertainty_radius_m set
 *
 * The panel NEVER presents estimated points as recorded fact.
 * The data_note from the API is always displayed.
 *
 * Architecture
 * ─────────────
 * Uses the same imperative Leaflet pattern as MapView.tsx (existing component).
 * Map and layer group refs are managed via useRef.  Each data change rebuilds
 * only the flight-path layer group — the base tile layer is untouched.
 *
 * Profile chart below the map
 * ────────────────────────────
 * Rendered as a minimal inline SVG — no new charting library required.
 * Altitude points are normalised to 80px height.  Estimated points are
 * rendered dashed/amber.  Selecting a profile point highlights the map
 * marker (via a CSS class toggle using the point_id as a layer ID).
 *
 * Extension points
 * ─────────────────
 * - 3D / terrain: replace the Leaflet tile layer section with Mapbox GL /
 *   Cesium and connect to the same reconstruction payload (same data shape).
 * - ADS-B real-time feed: push new points into a reactive store; the
 *   component can subscribe and call _rebuildLayers() on new data.
 * - Path simplification: apply Douglas-Peucker to points before rendering
 *   when point_count > 500.
 */
import { useEffect, useRef, useState } from "react";
import type {
  FlightPathAnnotation,
  FlightPathPoint,
  FlightPathProfilePoint,
  FlightPathReconstruction,
  FlightPathProfile,
} from "../types";
import { fetchFlightPath, fetchFlightPathProfile } from "../lib/api";
import {
  EmptyState,
  LoadingState,
  MetricCard,
  Panel,
  SectionHeader,
  StatusBadge,
} from "./UI";

// ── Constants ─────────────────────────────────────────────────────────────────

const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const DEFAULT_LAT = 37.0902;
const DEFAULT_LON = -95.7129;
const DEFAULT_ZOOM = 4;

const COLOR_RECORDED = "#185FA5"; // solid blue — matches existing map accent
const COLOR_ESTIMATED = "#D97706"; // amber
const COLOR_DISPUTED = "#7C3AED"; // purple-violet
const COLOR_IMPACT = "#DC2626"; // red
const COLOR_LKP = "#1D4ED8"; // dark blue
const COLOR_WARNING = "#EAB308"; // yellow

// ── Time label helper ─────────────────────────────────────────────────────────

function formatPointTime(p: FlightPathPoint): string {
  if (p.time_precision === "exact" && p.recorded_time_utc) {
    return new Date(p.recorded_time_utc).toUTCString().replace(" GMT", " UTC");
  }
  if (p.recorded_time_utc) {
    return `~${new Date(p.recorded_time_utc).toUTCString().replace(" GMT", " UTC")}`;
  }
  if (p.relative_offset_seconds != null) {
    const abs = Math.abs(p.relative_offset_seconds);
    const sign = p.relative_offset_seconds < 0 ? "before" : "after";
    const m = Math.floor(abs / 60),
      s = abs % 60;
    return `${m > 0 ? m + "m " : ""}${s}s ${sign} impact (${p.time_precision})`;
  }
  if (p.sequence_index != null)
    return `Step ${p.sequence_index + 1} (sequence only)`;
  return "Time unknown";
}

function formatAnnotationTime(a: FlightPathAnnotation): string {
  if (a.annotation_time_utc)
    return new Date(a.annotation_time_utc)
      .toUTCString()
      .replace(" GMT", " UTC");
  if (a.relative_offset_seconds != null) {
    const abs = Math.abs(a.relative_offset_seconds);
    const sign = a.relative_offset_seconds < 0 ? "before" : "after";
    return `${abs}s ${sign} impact`;
  }
  return "Time unknown";
}

// ── Inline SVG altitude profile ───────────────────────────────────────────────

function AltitudeProfile({
  altitude,
  selectedId,
  onSelect,
}: {
  altitude: FlightPathProfilePoint[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const pts = altitude.filter((p) => p.altitude_ft != null);
  if (pts.length < 2) return null;

  const W = 600,
    H = 80,
    PAD = 4;
  const vals = pts.map((p) => p.altitude_ft as number);
  const minV = Math.min(...vals),
    maxV = Math.max(...vals);
  const range = maxV - minV || 1;

  const sx = (i: number) => PAD + (i / (pts.length - 1)) * (W - PAD * 2);
  const sy = (v: number) => PAD + (1 - (v - minV) / range) * (H - PAD * 2);

  // Build recorded and estimated path segments
  const recordedPath = pts
    .map(
      (p, i) => `${i === 0 ? "M" : "L"}${sx(i)},${sy(p.altitude_ft as number)}`,
    )
    .join(" ");

  return (
    <div className="mt-2">
      <div
        className="text-[9px] text-stone-400 uppercase tracking-wide mb-1"
        style={{ fontFamily: "var(--ff-mono)" }}
      >
        Altitude profile (ft)
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height: 80, background: "#fafaf9", borderRadius: 4 }}
        aria-label="Altitude profile chart"
      >
        {/* Grid line at mid-height */}
        <line
          x1={PAD}
          y1={H / 2}
          x2={W - PAD}
          y2={H / 2}
          stroke="#e5e5e4"
          strokeWidth="0.5"
        />

        {/* Path — split into recorded (solid) and estimated (dashed) */}
        {pts.map((p, i) => {
          if (i === 0) return null;
          const prev = pts[i - 1];
          const isEst = p.is_estimated || prev.is_estimated;
          const isDisp = p.is_disputed || prev.is_disputed;
          const stroke = isDisp
            ? COLOR_DISPUTED
            : isEst
              ? COLOR_ESTIMATED
              : COLOR_RECORDED;
          const dash = isEst || isDisp ? "4 3" : undefined;
          return (
            <line
              key={p.point_id}
              x1={sx(i - 1)}
              y1={sy(prev.altitude_ft as number)}
              x2={sx(i)}
              y2={sy(p.altitude_ft as number)}
              stroke={stroke}
              strokeWidth="1.5"
              strokeDasharray={dash}
            />
          );
        })}

        {/* Click targets */}
        {pts.map((p, i) => (
          <circle
            key={p.point_id}
            cx={sx(i)}
            cy={sy(p.altitude_ft as number)}
            r={selectedId === p.point_id ? 4 : 2}
            fill={p.is_estimated ? COLOR_ESTIMATED : COLOR_RECORDED}
            stroke={selectedId === p.point_id ? "#fff" : "none"}
            strokeWidth={1.5}
            style={{ cursor: "pointer" }}
            onClick={() => onSelect(p.point_id)}
          >
            <title>{`${Math.round(p.altitude_ft as number).toLocaleString()} ft${p.is_estimated ? " (estimated)" : ""}`}</title>
          </circle>
        ))}

        {/* Axis labels */}
        <text
          x={PAD + 2}
          y={PAD + 8}
          fontSize="7"
          fill="#a8a29e"
          style={{ fontFamily: "var(--ff-mono)" }}
        >
          {Math.round(maxV).toLocaleString()}ft
        </text>
        <text
          x={PAD + 2}
          y={H - PAD - 2}
          fontSize="7"
          fill="#a8a29e"
          style={{ fontFamily: "var(--ff-mono)" }}
        >
          {Math.round(minV).toLocaleString()}ft
        </text>
      </svg>
      <div className="flex gap-3 mt-1">
        <span className="flex items-center gap-1 text-[9px] text-stone-400">
          <span
            style={{
              display: "inline-block",
              width: 16,
              height: 2,
              background: COLOR_RECORDED,
            }}
          />
          Recorded
        </span>
        <span className="flex items-center gap-1 text-[9px] text-stone-400">
          <span
            style={{
              display: "inline-block",
              width: 16,
              height: 2,
              background: COLOR_ESTIMATED,
              borderTop: `2px dashed ${COLOR_ESTIMATED}`,
            }}
          />
          Estimated
        </span>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface SelectedDetail {
  type: "point" | "annotation";
  data: FlightPathPoint | FlightPathAnnotation;
}

export default function FlightPathMapPanel({
  accidentEventId,
}: {
  accidentEventId: string;
}) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<unknown>(null);
  const layerGroupRef = useRef<unknown>(null);

  const [recon, setRecon] = useState<FlightPathReconstruction | null>(null);
  const [profile, setProfile] = useState<FlightPathProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedDetail | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(
    null,
  );

  // Fetch data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchFlightPath(accidentEventId),
      fetchFlightPathProfile(accidentEventId),
    ])
      .then(([r, p]) => {
        if (!cancelled) {
          setRecon(r);
          setProfile(p);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [accidentEventId]);

  // Initialise Leaflet map
  useEffect(() => {
    if (typeof window === "undefined" || !mapRef.current) return;
    if (mapInstanceRef.current) return; // already initialised

    import("leaflet").then((L) => {
      // Fix default icon paths (same fix as MapView.tsx)
      delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)
        ._getIconUrl;
      L.Icon.Default.mergeOptions({
        iconRetinaUrl:
          "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png",
        iconUrl:
          "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png",
        shadowUrl:
          "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
      });

      const map = L.map(mapRef.current!, {
        center: [DEFAULT_LAT, DEFAULT_LON],
        zoom: DEFAULT_ZOOM,
        zoomControl: true,
      });
      L.tileLayer(TILE_URL, { attribution: TILE_ATTR, maxZoom: 18 }).addTo(map);
      mapInstanceRef.current = map;
      layerGroupRef.current = L.layerGroup().addTo(map);
    });

    return () => {
      if (mapInstanceRef.current) {
        (mapInstanceRef.current as { remove: () => void }).remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  // Rebuild flight-path layers when reconstruction data changes
  useEffect(() => {
    if (!recon || !mapInstanceRef.current || !layerGroupRef.current) return;

    import("leaflet").then((L) => {
      const lg = layerGroupRef.current as {
        clearLayers: () => void;
        addLayer: (l: unknown) => void;
      };
      lg.clearLayers();

      const map = mapInstanceRef.current as {
        fitBounds: (b: unknown) => void;
        setView: (c: [number, number], z: number) => void;
      };

      // ── Segment polylines ──────────────────────────────────────────────────
      const pointById = new Map(recon.points.map((p) => [p.id, p]));

      for (const seg of recon.segments) {
        const a = seg.start_point_id ? pointById.get(seg.start_point_id) : null;
        const b = seg.end_point_id ? pointById.get(seg.end_point_id) : null;
        if (
          !a ||
          !b ||
          a.latitude == null ||
          a.longitude == null ||
          b.latitude == null ||
          b.longitude == null
        )
          continue;

        let color: string, weight: number, dash: string | undefined;
        switch (seg.render_style) {
          case "solid_recorded":
            color = COLOR_RECORDED;
            weight = 3;
            break;
          case "dashed_estimated":
            color = COLOR_ESTIMATED;
            weight = 2;
            dash = "8 6";
            break;
          case "disputed":
            color = COLOR_DISPUTED;
            weight = 2;
            dash = "4 4";
            break;
          default:
            color = "#a8a29e";
            weight = 1.5;
            dash = "2 3";
        }

        const poly = L.polyline(
          [
            [a.latitude, a.longitude],
            [b.latitude, b.longitude],
          ],
          {
            color,
            weight,
            dashArray: dash,
            opacity: seg.is_disputed ? 0.75 : 1.0,
          },
        );

        // Direction arrowhead at midpoint — simple SVG divIcon
        const midLat = (a.latitude + b.latitude) / 2;
        const midLon = (a.longitude + b.longitude) / 2;
        const brng = seg.bearing_degrees ?? 0;
        const arrowIcon = L.divIcon({
          className: "",
          html: `<svg width="12" height="12" viewBox="-6 -6 12 12" style="transform:rotate(${brng}deg)">
            <polygon points="0,-5 3,3 0,1 -3,3" fill="${color}" opacity="0.9"/>
          </svg>`,
          iconSize: [12, 12],
          iconAnchor: [6, 6],
        });
        lg.addLayer(
          L.marker([midLat, midLon], { icon: arrowIcon, interactive: false }),
        );
        lg.addLayer(poly);
      }

      // ── Path points ────────────────────────────────────────────────────────
      for (const pt of recon.points) {
        if (pt.latitude == null || pt.longitude == null) continue;

        const isLKP = pt.id === recon.last_recorded_point_id;
        const isImpact = pt.id === recon.impact_point_id;
        const opacity = (pt.confidence_score ?? 1) < 0.5 ? 0.55 : 1.0;

        let fillColor = pt.is_estimated ? COLOR_ESTIMATED : COLOR_RECORDED;
        let radius = 5;
        if (isLKP) {
          fillColor = COLOR_LKP;
          radius = 8;
        }
        if (isImpact) {
          fillColor = COLOR_IMPACT;
          radius = 9;
        }
        if (pt.is_disputed) {
          fillColor = "#E11D48";
        }

        const circleMarker = L.circleMarker([pt.latitude, pt.longitude], {
          radius,
          fillColor,
          fillOpacity: opacity,
          color: pt.is_disputed ? "#BE123C" : "#fff",
          weight: pt.is_disputed ? 2 : 1,
        });

        // Popup content — built with DOM to prevent injection
        const popup = document.createElement("div");
        popup.style.cssText =
          "font-family:var(--ff-mono,monospace);font-size:11px;min-width:180px;max-width:260px";

        const addRow = (
          label: string,
          value: string | null | undefined,
          warn = false,
        ) => {
          if (!value) return;
          const row = document.createElement("div");
          row.style.cssText = `display:flex;gap:6px;margin:1px 0;${warn ? "color:#DC2626" : ""}`;
          const l = document.createElement("span");
          l.style.cssText = "color:#a8a29e;min-width:80px";
          l.textContent = label;
          const v = document.createElement("span");
          v.style.cssText = "color:#1c1917;font-weight:500";
          v.textContent = value;
          row.appendChild(l);
          row.appendChild(v);
          popup.appendChild(row);
        };

        if (isLKP) {
          const badge = document.createElement("div");
          badge.textContent = "LAST RECORDED POSITION";
          badge.style.cssText = `background:${COLOR_LKP};color:#fff;font-size:9px;padding:2px 6px;border-radius:3px;margin-bottom:4px;font-weight:700;letter-spacing:.05em`;
          popup.prepend(badge);
        }
        if (isImpact) {
          const badge = document.createElement("div");
          badge.textContent = "ACCIDENT SITE / IMPACT";
          badge.style.cssText = `background:${COLOR_IMPACT};color:#fff;font-size:9px;padding:2px 6px;border-radius:3px;margin-bottom:4px;font-weight:700;letter-spacing:.05em`;
          popup.prepend(badge);
        }
        if (pt.is_estimated) {
          const est = document.createElement("div");
          est.textContent =
            "⚠ Estimated/inferred — not a confirmed recorded position";
          est.style.cssText = `background:#FEF3C7;color:#92400E;font-size:9px;padding:3px 6px;border-radius:3px;margin-bottom:4px;line-height:1.4`;
          popup.prepend(est);
        }

        addRow("Type", pt.point_type.replace(/_/g, " "));
        addRow("Method", pt.source_method?.replace(/_/g, " "));
        addRow("Time", formatPointTime(pt));
        addRow("Precision", pt.time_precision);
        if (pt.altitude_ft != null)
          addRow(
            "Altitude",
            `${Math.round(pt.altitude_ft).toLocaleString()} ft ${pt.altitude_reference ?? ""}`,
          );
        if (pt.ground_speed_kt != null)
          addRow("G/S", `${Math.round(pt.ground_speed_kt)} kt`);
        if (pt.vertical_speed_fpm != null)
          addRow("V/S", `${Math.round(pt.vertical_speed_fpm)} fpm`);
        if (pt.heading_degrees != null)
          addRow("HDG", `${Math.round(pt.heading_degrees)}°`);
        if (pt.uncertainty_radius_m != null)
          addRow("Uncertainty", `±${Math.round(pt.uncertainty_radius_m)} m`);
        if (pt.confidence_score != null)
          addRow("Confidence", `${Math.round(pt.confidence_score * 100)}%`);
        if (pt.is_disputed)
          addRow(
            "⚡ Dispute",
            pt.dispute_summary ?? "Disputed by sources",
            true,
          );
        if (pt.notes) addRow("Notes", pt.notes);

        circleMarker.bindPopup(popup);
        circleMarker.on("click", () =>
          setSelected({ type: "point", data: pt }),
        );
        lg.addLayer(circleMarker);

        // Uncertainty circle
        if (pt.uncertainty_radius_m != null && pt.uncertainty_radius_m > 0) {
          lg.addLayer(
            L.circle([pt.latitude, pt.longitude], {
              radius: pt.uncertainty_radius_m,
              color: COLOR_RECORDED,
              fillColor: COLOR_RECORDED,
              fillOpacity: 0.04,
              weight: 0.5,
              dashArray: "3 3",
              interactive: false,
            }),
          );
        }
      }

      // ── Accident site marker (if no impact point in track) ─────────────────
      if (
        recon.accident_site?.latitude != null &&
        recon.accident_site?.longitude != null
      ) {
        const impactIcon = L.divIcon({
          className: "",
          html: `<svg width="22" height="22" viewBox="0 0 22 22">
            <polygon points="11,2 14,9 22,9 16,14 18,21 11,17 4,21 6,14 0,9 8,9"
              fill="${COLOR_IMPACT}" stroke="#fff" stroke-width="1.5"/>
          </svg>`,
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        });
        const site = recon.accident_site;
        const marker = L.marker([site.latitude, site.longitude], {
          icon: impactIcon,
          zIndexOffset: 1000,
        });
        const pop = document.createElement("div");
        pop.style.cssText =
          "font-family:var(--ff-mono,monospace);font-size:11px";
        const h = document.createElement("strong");
        h.textContent = "Accident Site";
        h.style.color = COLOR_IMPACT;
        pop.appendChild(h);
        marker.bindPopup(pop);
        lg.addLayer(marker);
      }

      // ── Event annotations ─────────────────────────────────────────────────
      for (const ann of recon.annotations) {
        // Find co-located point if linked
        const pt = ann.flight_path_point_id
          ? pointById.get(ann.flight_path_point_id)
          : null;
        if (!pt || pt.latitude == null || pt.longitude == null) continue;

        const isWarning =
          ann.annotation_type.startsWith("gpws") ||
          ann.annotation_type === "terrain_warning" ||
          ann.annotation_type === "stall_warning";

        const annIcon = L.divIcon({
          className: "",
          html: isWarning
            ? `<svg width="16" height="16" viewBox="0 0 16 16">
                <polygon points="8,1 15,14 1,14" fill="${COLOR_WARNING}" stroke="#78350F" stroke-width="1"/>
                <text x="8" y="12" text-anchor="middle" font-size="8" fill="#78350F" font-weight="bold">!</text>
               </svg>`
            : `<svg width="14" height="14" viewBox="0 0 14 14">
                <circle cx="7" cy="7" r="6" fill="#F97316" stroke="#fff" stroke-width="1.5"/>
                <text x="7" y="10" text-anchor="middle" font-size="8" fill="#fff" font-weight="bold">E</text>
               </svg>`,
          iconSize: [16, 16],
          iconAnchor: [8, 8],
        });

        const annMarker = L.marker([pt.latitude, pt.longitude], {
          icon: annIcon,
          zIndexOffset: 500,
        });
        const pop = document.createElement("div");
        pop.style.cssText =
          "font-family:var(--ff-mono,monospace);font-size:11px;min-width:160px;max-width:240px";

        const title = document.createElement("div");
        title.style.cssText = "font-weight:700;color:#1c1917;margin-bottom:3px";
        title.textContent = ann.title;
        pop.appendChild(title);

        const subtype = document.createElement("div");
        subtype.style.cssText =
          "font-size:9px;color:#a8a29e;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em";
        subtype.textContent = ann.annotation_type.replace(/_/g, " ");
        pop.appendChild(subtype);

        const addARow = (label: string, value: string | null | undefined) => {
          if (!value) return;
          const row = document.createElement("div");
          row.style.cssText = "display:flex;gap:6px;margin:1px 0";
          const l = document.createElement("span");
          l.style.cssText = "color:#a8a29e;min-width:70px;font-size:10px";
          l.textContent = label;
          const v = document.createElement("span");
          v.style.cssText = "color:#1c1917;font-size:10px";
          v.textContent = value;
          row.appendChild(l);
          row.appendChild(v);
          pop.appendChild(row);
        };

        addARow("Time", formatAnnotationTime(ann));
        if (ann.altitude_ft != null)
          addARow(
            "Altitude",
            `${Math.round(ann.altitude_ft).toLocaleString()} ft`,
          );
        if (ann.radio_altitude_ft != null)
          addARow("Radio alt", `${Math.round(ann.radio_altitude_ft)} ft AGL`);
        if (ann.confidence_score != null)
          addARow("Confidence", `${Math.round(ann.confidence_score * 100)}%`);
        if (ann.is_disputed) {
          const d = document.createElement("div");
          d.textContent = `⚡ ${ann.dispute_summary ?? "Disputed"}`;
          d.style.cssText = "color:#DC2626;margin-top:3px;font-size:10px";
          pop.appendChild(d);
        }
        if (ann.description) {
          const desc = document.createElement("div");
          desc.textContent = ann.description;
          desc.style.cssText =
            "margin-top:4px;color:#57534e;font-size:10px;line-height:1.4;border-top:1px solid #f5f5f4;padding-top:3px";
          pop.appendChild(desc);
        }

        annMarker.bindPopup(pop);
        annMarker.on("click", () =>
          setSelected({ type: "annotation", data: ann }),
        );
        lg.addLayer(annMarker);
      }

      // ── Fit map to bounds ──────────────────────────────────────────────────
      if (recon.bounds) {
        const b = recon.bounds;
        map.fitBounds(
          [
            [b.min_lat, b.min_lon],
            [b.max_lat, b.max_lon],
          ],
          { padding: [30, 30], maxZoom: 13 },
        );
      } else if (recon.accident_site?.latitude != null) {
        map.setView(
          [recon.accident_site.latitude, recon.accident_site.longitude],
          10,
        );
      }
    });
  }, [recon]);

  // ── Render ─────────────────────────────────────────────────────────────────

  if (loading)
    return (
      <Panel className="mb-6">
        <SectionHeader
          eyebrow="Trajectory"
          title="Flight Path Reconstruction"
          description="Loading route geometry, altitude profile, and source confidence metadata."
        />
        <LoadingState label="Loading flight path data…" rows={3} />
      </Panel>
    );

  if (error)
    return (
      <Panel className="mb-6">
        <SectionHeader
          eyebrow="Trajectory"
          title="Flight Path Reconstruction"
        />
        <EmptyState
          icon="⚠"
          title="Could not load flight path"
          description={error}
          className="border-red-200 bg-red-50/60"
        />
      </Panel>
    );

  // Empty state
  if (!recon?.has_path && !recon?.accident_site)
    return (
      <Panel className="mb-6">
        <SectionHeader
          eyebrow="Trajectory"
          title="Flight Path Reconstruction"
        />
        <EmptyState
          title="No reconstructed flight path yet"
          description="Path points can be added by a reviewer via the API. The accident site will appear here as soon as coordinates exist."
        />
      </Panel>
    );

  const avgConf = recon?.confidence_summary?.avg_confidence;
  const hasDisputed =
    (recon?.confidence_summary?.disputed_point_count ?? 0) > 0;

  return (
    <Panel className="mb-6 overflow-hidden">
      {/* Leaflet CSS */}
      <link
        rel="stylesheet"
        href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"
      />

      <SectionHeader
        eyebrow="Trajectory"
        title="Flight Path Reconstruction"
        description={
          recon
            ? `${recon.point_count} point${recon.point_count !== 1 ? "s" : ""}${recon.path_length_km > 0 ? ` · ${recon.path_length_km.toFixed(1)} km path length` : ""}`
            : undefined
        }
        actions={
          <div className="flex flex-wrap gap-2">
            {avgConf != null && (
              <StatusBadge
                tone={
                  avgConf >= 0.75 ? "green" : avgConf >= 0.5 ? "amber" : "red"
                }
              >
                {Math.round(avgConf * 100)}% avg confidence
              </StatusBadge>
            )}
            {hasDisputed && (
              <StatusBadge tone="purple">disputed points present</StatusBadge>
            )}
          </div>
        }
      />

      {/* Confidence, dispute, and route metadata */}
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <MetricCard
          label="Waypoints"
          value={recon?.point_count ?? 0}
          sub={
            (recon?.confidence_summary?.estimated_point_count ?? 0) > 0
              ? `${recon?.confidence_summary?.estimated_point_count} estimated`
              : "recorded/site points"
          }
        />
        <MetricCard
          label="Path length"
          value={
            recon && recon.path_length_km > 0
              ? `${recon.path_length_km.toFixed(1)} km`
              : "—"
          }
          sub="computed from available route points"
        />
        <MetricCard
          label="Disputed"
          value={recon?.confidence_summary?.disputed_point_count ?? 0}
          sub="points needing review"
          tone={hasDisputed ? "purple" : "green"}
        />
      </div>

      <div className="mb-4 space-y-2" aria-live="polite">
        {hasDisputed && (
          <div className="rounded-xl border border-purple-200 bg-purple-50 px-3 py-2 text-[11px] leading-relaxed text-purple-700">
            ⚡ This flight path contains disputed points. Multiple sources
            disagree on position or timing, and all claims are preserved.
          </div>
        )}
        {avgConf != null && avgConf < 0.5 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-700">
            ⚠ Average path confidence is {Math.round(avgConf * 100)}% — treat
            this reconstruction as low-confidence analysis.
          </div>
        )}
      </div>

      {/* Legend */}
      <div
        className="mb-3 rounded-xl border border-stone-200 bg-stone-50/70 px-3 py-2"
        aria-label="Flight path legend"
      >
        <div className="mb-2 text-[10px] uppercase tracking-[0.16em] text-stone-400 font-mono">
          Legend
        </div>
        <div className="flex flex-wrap gap-3">
          {[
            {
              color: COLOR_RECORDED,
              label: "Recorded / observed",
              dash: false,
            },
            {
              color: COLOR_ESTIMATED,
              label: "Estimated / inferred",
              dash: true,
            },
            { color: COLOR_DISPUTED, label: "Disputed", dash: true },
          ].map(({ color, label, dash }) => (
            <span
              key={label}
              className="flex items-center gap-1.5 text-[10px] text-stone-600"
            >
              <span
                style={{
                  display: "inline-block",
                  width: 24,
                  height: 2,
                  background: dash ? "transparent" : color,
                  borderTop: dash ? `2px dashed ${color}` : undefined,
                }}
              />
              {label}
            </span>
          ))}
          <span className="flex items-center gap-1.5 text-[10px] text-stone-600">
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
              <polygon points="5,1 9,9 1,9" fill={COLOR_WARNING} />
            </svg>
            Warning event
          </span>
        </div>
      </div>

      {/* Map */}
      <div className="overflow-hidden rounded-2xl border border-stone-200 bg-stone-100 shadow-inner">
        <div
          ref={mapRef}
          className="min-h-[320px] sm:min-h-[380px]"
          style={{ height: 380, width: "100%", zIndex: 0 }}
          role="application"
          aria-label="Interactive map showing reconstructed accident flight path"
        />
      </div>

      {/* Single-point note */}
      {recon.point_count === 1 && (
        <p className="mt-2 rounded-lg bg-stone-50 px-3 py-2 text-[10px] italic text-stone-400">
          Only one path point is available — a full track reconstruction is not
          possible.
        </p>
      )}

      {/* Altitude profile chart */}
      {profile && profile.altitude.length >= 2 && (
        <AltitudeProfile
          altitude={profile.altitude}
          selectedId={selectedProfileId}
          onSelect={(id) => {
            setSelectedProfileId(id);
          }}
        />
      )}

      {/* Detail panel for selected point / annotation */}
      {selected && (
        <div className="mt-4 rounded-2xl border border-stone-200 bg-stone-50 p-3 text-[11px] shadow-inner">
          <div className="flex justify-between items-start mb-1.5">
            <span className="font-semibold text-stone-700 uppercase text-[9px] tracking-wide">
              {selected.type === "point"
                ? (selected.data as FlightPathPoint).point_type.replace(
                    /_/g,
                    " ",
                  )
                : (
                    selected.data as FlightPathAnnotation
                  ).annotation_type.replace(/_/g, " ")}
            </span>
            <button
              onClick={() => setSelected(null)}
              aria-label="Close selected flight path detail"
              className="rounded-md px-2 py-1 text-[10px] text-stone-400 hover:bg-white hover:text-stone-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
            >
              ✕
            </button>
          </div>
          {selected.type === "point" ? (
            <PointDetail point={selected.data as FlightPathPoint} />
          ) : (
            <AnnotationDetail ann={selected.data as FlightPathAnnotation} />
          )}
        </div>
      )}

      {/* Data note */}
      {recon?.data_note && (
        <p className="mt-4 rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-[10px] italic leading-relaxed text-stone-400">
          {recon.data_note}
        </p>
      )}
    </Panel>
  );
}

function PointDetail({ point: p }: { point: FlightPathPoint }) {
  return (
    <div className="space-y-0.5 text-stone-600">
      {p.is_estimated && (
        <div className="text-amber-700 bg-amber-50 rounded px-1.5 py-0.5 mb-1 text-[10px]">
          Estimated/inferred — not a confirmed recorded position
        </div>
      )}
      {p.is_disputed && (
        <div className="text-red-600 bg-red-50 rounded px-1.5 py-0.5 mb-1 text-[10px]">
          ⚡ Disputed: {p.dispute_summary}
        </div>
      )}
      <Row l="Time" v={formatPointTime(p)} />
      <Row l="Precision" v={p.time_precision} />
      {p.altitude_ft != null && (
        <Row
          l="Altitude"
          v={`${Math.round(p.altitude_ft).toLocaleString()} ft ${p.altitude_reference ?? ""}`}
        />
      )}
      {p.ground_speed_kt != null && (
        <Row l="Ground speed" v={`${Math.round(p.ground_speed_kt)} kt`} />
      )}
      {p.vertical_speed_fpm != null && (
        <Row l="Vertical speed" v={`${Math.round(p.vertical_speed_fpm)} fpm`} />
      )}
      {p.heading_degrees != null && (
        <Row l="Heading" v={`${Math.round(p.heading_degrees)}°`} />
      )}
      {p.uncertainty_radius_m != null && (
        <Row l="Uncertainty" v={`±${Math.round(p.uncertainty_radius_m)} m`} />
      )}
      {p.confidence_score != null && (
        <Row l="Confidence" v={`${Math.round(p.confidence_score * 100)}%`} />
      )}
      <Row l="Source" v={p.source_method?.replace(/_/g, " ")} />
      {p.notes && <Row l="Notes" v={p.notes} />}
      {p.supporting_claims.length > 0 && (
        <div className="mt-1.5 text-[9px] text-stone-400">
          {p.supporting_claims.length} supporting claim
          {p.supporting_claims.length > 1 ? "s" : ""}
        </div>
      )}
    </div>
  );
}

function AnnotationDetail({ ann: a }: { ann: FlightPathAnnotation }) {
  return (
    <div className="space-y-0.5 text-stone-600">
      <div className="font-medium text-stone-800 mb-1">{a.title}</div>
      {a.is_disputed && (
        <div className="text-red-600 bg-red-50 rounded px-1.5 py-0.5 mb-1 text-[10px]">
          ⚡ Disputed: {a.dispute_summary}
        </div>
      )}
      <Row l="Time" v={formatAnnotationTime(a)} />
      <Row l="Precision" v={a.time_precision} />
      {a.altitude_ft != null && (
        <Row
          l="Altitude"
          v={`${Math.round(a.altitude_ft).toLocaleString()} ft`}
        />
      )}
      {a.radio_altitude_ft != null && (
        <Row l="Radio alt" v={`${Math.round(a.radio_altitude_ft)} ft AGL`} />
      )}
      {a.confidence_score != null && (
        <Row l="Confidence" v={`${Math.round(a.confidence_score * 100)}%`} />
      )}
      {a.description && (
        <div className="mt-1 text-[10px] text-stone-500 border-t border-stone-100 pt-1">
          {a.description}
        </div>
      )}
    </div>
  );
}

function Row({ l, v }: { l: string; v: string | null | undefined }) {
  if (!v) return null;
  return (
    <div className="flex gap-2">
      <span className="text-stone-400 min-w-[80px]">{l}</span>
      <span className="text-stone-700">{v}</span>
    </div>
  );
}
