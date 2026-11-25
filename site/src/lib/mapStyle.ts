import type * as maplibregl from "maplibre-gl";
import type { EntitySelection, SourceFilter } from "../types/data";

// Synced with src/ogprod/export_web.py (_TILE_LAYER_MIN_ZOOM).
export const MAP_LAYER_MIN_ZOOM = {
  fields: 0,
  fieldLabels: 6,
  facilities: 7,
  wells: 8,
} as const;

export const INITIAL_MAP_ZOOM = 2.1;
export const MOBILE_OVERVIEW_ZOOM = 1.0;
export const FACILITY_ICON_ID = "facility-diamond";

export interface BasemapSpec {
  id: "openfreemap" | "versatiles";
  styleUrl: string;
  labelFont: string[];
}

export const PRIMARY_BASEMAP: BasemapSpec = {
  id: "openfreemap",
  styleUrl: "https://tiles.openfreemap.org/styles/positron",
  labelFont: ["Noto Sans Regular"],
};

export const FALLBACK_BASEMAP: BasemapSpec = {
  id: "versatiles",
  styleUrl: "https://tiles.versatiles.org/assets/styles/gray/style.json",
  labelFont: ["noto_sans_regular"],
};

const DATA_SOURCE_IDS = new Set(["ogprod", "fields-fallback", "facilities-fallback"]);
const OPENFREEMAP_SOURCE_IDS = new Set(["openmaptiles", "ne2_shaded"]);

export interface MapErrorLike {
  sourceId?: string;
  error?: { name?: string; message?: string };
}

/**
 * Decide whether a MapLibre error indicates the primary OpenFreeMap basemap failed
 * and should fall back to VersaTiles (ignoring aborts and our own data sources).
 */
export function shouldTriggerBasemapFallback(
  event: MapErrorLike,
  styleLoaded: boolean,
): boolean {
  const errName = event.error?.name ?? "";
  const errMsg = event.error?.message ?? "";
  if (errName === "AbortError" || /\babort(ed)?\b/i.test(errMsg)) {
    return false;
  }
  if (event.sourceId && DATA_SOURCE_IDS.has(event.sourceId)) {
    return false;
  }
  if (event.sourceId && OPENFREEMAP_SOURCE_IDS.has(event.sourceId)) {
    return true;
  }
  if (!styleLoaded && !event.sourceId) {
    return true;
  }
  return errMsg.toLowerCase().includes("openfreemap.org");
}

/** Pick fallback overview center and zoom when source bboxes are unavailable or span the globe. */
export function getOverviewView(containerWidth: number): {
  center: [number, number];
  zoom: number;
} {
  if (containerWidth > 0 && containerWidth < 640) {
    return { center: [-30, 18], zoom: MOBILE_OVERVIEW_ZOOM };
  }
  return { center: [-30, 14], zoom: INITIAL_MAP_ZOOM };
}

/**
 * Compute the union [[minLng, minLat], [maxLng, maxLat]] bounding box across all
 * sources with valid bboxes. Returns null when no valid bboxes exist or when the
 * span exceeds 300° longitude (where a full-world center/zoom view frames better).
 */
export function computeOverviewBounds(
  sources: readonly { bbox: [number, number, number, number] | null }[],
): [[number, number], [number, number]] | null {
  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;
  let count = 0;

  for (const src of sources) {
    const b = src.bbox;
    if (!b || b.length !== 4 || b.some((v) => !Number.isFinite(v))) continue;
    if (b[0] < minLng) minLng = b[0];
    if (b[1] < minLat) minLat = b[1];
    if (b[2] > maxLng) maxLng = b[2];
    if (b[3] > maxLat) maxLat = b[3];
    count += 1;
  }

  if (count === 0) return null;
  if (maxLng - minLng > 300) return null;
  return [
    [Math.max(-180, minLng), Math.max(-75, minLat)],
    [Math.min(180, maxLng), Math.min(80, maxLat)],
  ];
}

// MapLibre's symbol_sdf shader defines SDF_PX = 8 and edge alpha = 0.75 (cutoff = 0.25).
const SDF_RADIUS_PX = 8;
const SDF_CUTOFF = 0.25;

const NEVER_MATCH_FILTER: maplibregl.FilterSpecification = ["==", ["get", "source"], "__none__"];

export interface SdfImageData {
  width: number;
  height: number;
  data: Uint8Array;
}

/**
 * Generates an exact Euclidean signed distance field (SDF) diamond texture
 * compatible with `map.addImage(id, img, { pixelRatio: 2, sdf: true })`.
 */
export function createDiamondSdfImage(size = 48, halfDiagonal = 14): SdfImageData {
  const data = new Uint8Array(size * size * 4);
  const center = size / 2;
  const r = halfDiagonal;
  const invTwoR = 1 / (2 * r);

  for (let y = 0; y < size; y++) {
    const ay = Math.abs(y + 0.5 - center);
    for (let x = 0; x < size; x++) {
      const ax = Math.abs(x + 0.5 - center);
      // Closest point on the first-quadrant diamond edge segment (r, 0) -> (0, r).
      const t = Math.max(0, Math.min(1, (r - ax + ay) * invTwoR));
      const qx = r * (1 - t);
      const qy = r * t;
      const dist = Math.hypot(ax - qx, ay - qy);
      const signedDist = ax + ay <= r ? -dist : dist;
      const alpha = Math.max(
        0,
        Math.min(255, Math.round(255 * (1 - SDF_CUTOFF - signedDist / SDF_RADIUS_PX))),
      );

      const offset = (y * size + x) * 4;
      data[offset] = 255;
      data[offset + 1] = 255;
      data[offset + 2] = 255;
      data[offset + 3] = alpha;
    }
  }

  return { width: size, height: size, data };
}

export function isLayerZoomHidden(layer: "facilities" | "wells", zoom: number): boolean {
  return zoom < MAP_LAYER_MIN_ZOOM[layer];
}

export function buildSelectionFilter(
  kind: "field" | "facility" | "well",
  selection: EntitySelection,
  activeSource: SourceFilter,
): maplibregl.FilterSpecification {
  if (selection.kind !== kind) return NEVER_MATCH_FILTER;
  if (activeSource !== null && activeSource !== selection.source) return NEVER_MATCH_FILTER;
  const idProp = kind === "field" ? "field_id" : kind === "facility" ? "facility_id" : "well_id";
  return [
    "all",
    ["==", ["get", "source"], selection.source],
    ["==", ["get", idProp], selection.id],
  ];
}

const FACILITY_COLOR_EXPR: maplibregl.ExpressionSpecification = [
  "case",
  ["has", "oil_utilization"],
  [
    "interpolate",
    ["linear"],
    ["get", "oil_utilization"],
    0.2,
    "#0284c7",
    0.7,
    "#0d9488",
    0.95,
    "#dc2626",
  ],
  "#0e7490",
];

const FACILITY_SIZE_EXPR: maplibregl.ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["zoom"],
  MAP_LAYER_MIN_ZOOM.facilities,
  [
    "interpolate",
    ["linear"],
    ["coalesce", ["get", "oil_capacity_bpd"], 0],
    0,
    0.52,
    100000,
    0.68,
    200000,
    0.82,
  ],
  9,
  [
    "interpolate",
    ["linear"],
    ["coalesce", ["get", "oil_capacity_bpd"], 0],
    0,
    0.78,
    100000,
    1.05,
    200000,
    1.3,
  ],
];

const FACILITY_SELECTED_SIZE_EXPR: maplibregl.ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["zoom"],
  MAP_LAYER_MIN_ZOOM.facilities,
  0.95,
  9,
  1.4,
];

export function buildVectorLayers(
  labelFont: string[] = PRIMARY_BASEMAP.labelFont,
): maplibregl.LayerSpecification[] {
  const facMin = MAP_LAYER_MIN_ZOOM.facilities;
  const lblMin = MAP_LAYER_MIN_ZOOM.fieldLabels;
  const wellMin = MAP_LAYER_MIN_ZOOM.wells;

  return [
    {
      id: "fields-fill",
      type: "fill",
      source: "ogprod",
      "source-layer": "fields",
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["coalesce", ["get", "latest_oil_bpd"], 0],
          0,
          "#cbd5e1",
          5000,
          "#fbbf24",
          50000,
          "#d97706",
          250000,
          "#9a3412",
        ],
        "fill-opacity": 0.48,
      },
    },
    {
      id: "fields-outline",
      type: "line",
      source: "ogprod",
      "source-layer": "fields",
      paint: {
        "line-color": "#92400e",
        "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 10, 1.6],
        "line-opacity": 0.85,
      },
    },
    {
      id: "fields-selected",
      type: "line",
      source: "ogprod",
      "source-layer": "fields",
      filter: NEVER_MATCH_FILTER,
      paint: {
        "line-color": "#0f172a",
        "line-width": ["interpolate", ["linear"], ["zoom"], 4, 2, 10, 3.2],
        "line-opacity": 0.95,
      },
    },
    {
      id: "fields-point",
      type: "circle",
      source: "ogprod",
      "source-layer": "field_points",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2.8, 7, 4.5, 11, 6.5],
        "circle-color": [
          "interpolate",
          ["linear"],
          ["coalesce", ["get", "latest_oil_bpd"], 0],
          0,
          "#cbd5e1",
          5000,
          "#fbbf24",
          50000,
          "#d97706",
          250000,
          "#9a3412",
        ],
        "circle-opacity": 0.78,
        "circle-stroke-color": "#92400e",
        "circle-stroke-width": 1,
        "circle-stroke-opacity": 0.85,
      },
    },
    {
      id: "fields-point-selected",
      type: "circle",
      source: "ogprod",
      "source-layer": "field_points",
      filter: NEVER_MATCH_FILTER,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 5.5, 11, 9.5],
        "circle-color": "rgba(15, 23, 42, 0)",
        "circle-stroke-color": "#0f172a",
        "circle-stroke-width": 2.4,
      },
    },
    {
      id: "wells-circle",
      type: "circle",
      source: "ogprod",
      "source-layer": "wells",
      minzoom: wellMin,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], wellMin, 2.2, 10.5, 4, 13, 5.5],
        "circle-color": [
          "case",
          [">", ["coalesce", ["get", "latest_oil_bpd"], 0], 0],
          "#1e293b",
          "#64748b",
        ],
        "circle-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          wellMin,
          ["case", [">", ["coalesce", ["get", "latest_oil_bpd"], 0], 0], 0.4, 0.22],
          wellMin + 0.8,
          ["case", [">", ["coalesce", ["get", "latest_oil_bpd"], 0], 0], 0.9, 0.55],
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 0.8,
        "circle-stroke-opacity": ["interpolate", ["linear"], ["zoom"], wellMin, 0.35, wellMin + 0.8, 0.85],
      },
    },
    {
      id: "wells-selected",
      type: "circle",
      source: "ogprod",
      "source-layer": "wells",
      filter: NEVER_MATCH_FILTER,
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], wellMin, 6.5, 13, 9.5],
        "circle-color": "rgba(15, 23, 42, 0)",
        "circle-stroke-color": "#0f172a",
        "circle-stroke-width": 2.2,
      },
    },
    {
      id: "facilities-symbol",
      type: "symbol",
      source: "ogprod",
      "source-layer": "facilities",
      minzoom: facMin,
      layout: {
        "icon-image": FACILITY_ICON_ID,
        "icon-size": FACILITY_SIZE_EXPR,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "symbol-sort-key": ["coalesce", ["get", "oil_capacity_bpd"], 0],
      },
      paint: {
        "icon-color": FACILITY_COLOR_EXPR,
        "icon-halo-color": "#ffffff",
        "icon-halo-width": 1.4,
        "icon-opacity": ["interpolate", ["linear"], ["zoom"], facMin, 0.65, facMin + 0.8, 1],
      },
    },
    {
      id: "fields-label",
      type: "symbol",
      source: "ogprod",
      "source-layer": "field_centroids",
      minzoom: lblMin,
      layout: {
        "text-field": ["coalesce", ["get", "field_name"], ["get", "field_id"]],
        "text-font": labelFont,
        "text-size": ["interpolate", ["linear"], ["zoom"], lblMin, 10, 10, 12.5],
        "text-max-width": 10,
        "text-padding": 4,
        // Lower sort-key wins collision placement so top-producing fields label first.
        "symbol-sort-key": ["-", 0, ["coalesce", ["get", "latest_oil_bpd"], 0]],
      },
      paint: {
        "text-color": "#78350f",
        "text-halo-color": "rgba(255, 255, 255, 0.9)",
        "text-halo-width": 1.3,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], lblMin, 0, lblMin + 0.6, 0.9],
      },
    },
    {
      id: "facilities-selected",
      type: "symbol",
      source: "ogprod",
      "source-layer": "facilities",
      filter: NEVER_MATCH_FILTER,
      layout: {
        "icon-image": FACILITY_ICON_ID,
        "icon-size": FACILITY_SELECTED_SIZE_EXPR,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
      paint: {
        "icon-color": FACILITY_COLOR_EXPR,
        "icon-halo-color": "#0f172a",
        "icon-halo-width": 2.2,
      },
    },
  ];
}

export function buildFallbackLayers(): maplibregl.LayerSpecification[] {
  const facMin = MAP_LAYER_MIN_ZOOM.facilities;
  return [
    {
      id: "fields-fill",
      type: "circle",
      source: "fields-fallback",
      paint: {
        "circle-radius": 6,
        "circle-color": "#d97706",
        "circle-opacity": 0.72,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.2,
      },
    },
    {
      id: "fields-selected",
      type: "circle",
      source: "fields-fallback",
      filter: NEVER_MATCH_FILTER,
      paint: {
        "circle-radius": 9.5,
        "circle-color": "rgba(15, 23, 42, 0)",
        "circle-stroke-color": "#0f172a",
        "circle-stroke-width": 2.2,
      },
    },
    {
      id: "facilities-symbol",
      type: "symbol",
      source: "facilities-fallback",
      minzoom: facMin,
      layout: {
        "icon-image": FACILITY_ICON_ID,
        "icon-size": FACILITY_SIZE_EXPR,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
      paint: {
        "icon-color": FACILITY_COLOR_EXPR,
        "icon-halo-color": "#ffffff",
        "icon-halo-width": 1.4,
        "icon-opacity": ["interpolate", ["linear"], ["zoom"], facMin, 0.65, facMin + 0.8, 1],
      },
    },
    {
      id: "facilities-selected",
      type: "symbol",
      source: "facilities-fallback",
      filter: NEVER_MATCH_FILTER,
      layout: {
        "icon-image": FACILITY_ICON_ID,
        "icon-size": FACILITY_SELECTED_SIZE_EXPR,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
      paint: {
        "icon-color": FACILITY_COLOR_EXPR,
        "icon-halo-color": "#0f172a",
        "icon-halo-width": 2.2,
      },
    },
  ];
}
