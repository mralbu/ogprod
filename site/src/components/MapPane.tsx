import { Layers } from "lucide-react";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { useEffect, useRef, useState } from "react";
import { pmtilesUrl } from "../lib/dataClient";
import {
  buildFallbackLayers,
  buildSelectionFilter,
  buildVectorLayers,
  createDiamondSdfImage,
  FACILITY_ICON_ID,
  INITIAL_MAP_ZOOM,
  isLayerZoomHidden,
} from "../lib/mapStyle";
import { formatCompactNumber, formatPercent } from "../lib/units";
import type {
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  SiteMeta,
  SourceFilter,
} from "../types/data";

interface MapPaneProps {
  meta: SiteMeta;
  activeSource: SourceFilter;
  selection: EntitySelection;
  fieldsSummary: FieldSummary[];
  facilitiesSummary: FacilitySummary[];
  onSelectEntity: (selection: EntitySelection) => void;
}

let protocolRegistered = false;

function ensurePmtilesProtocol(): void {
  if (protocolRegistered) return;
  const protocol = new Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  protocolRegistered = true;
}

function absolutePmtilesUrl(): string {
  const rel = pmtilesUrl();
  if (rel.startsWith("http://") || rel.startsWith("https://")) {
    return rel;
  }
  return new URL(rel, window.location.origin).href;
}

function buildFallbackGeoJson(
  items: { lon: number | null; lat: number | null; [key: string]: unknown }[],
): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: items
      .filter((i) => i.lon !== null && i.lat !== null)
      .map((item) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [item.lon as number, item.lat as number] },
        properties: { ...item },
      })),
  };
}

export function MapPane({
  meta,
  activeSource,
  selection,
  fieldsSummary,
  facilitiesSummary,
  onSelectEntity,
}: MapPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const onSelectRef = useRef(onSelectEntity);
  onSelectRef.current = onSelectEntity;

  const [showFields, setShowFields] = useState(true);
  const [showFacilities, setShowFacilities] = useState(true);
  const [showWells, setShowWells] = useState(true);
  const [mapReady, setMapReady] = useState(false);
  const [currentZoom, setCurrentZoom] = useState(INITIAL_MAP_ZOOM);

  useEffect(() => {
    if (!containerRef.current) return;
    ensurePmtilesProtocol();

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: "https://tiles.openfreemap.org/styles/positron",
      center: [-30, 12],
      zoom: INITIAL_MAP_ZOOM,
      minZoom: 1.5,
      maxZoom: 16,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;
    popupRef.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });

    const syncZoomBand = () => {
      const z = map.getZoom();
      setCurrentZoom((prev) =>
        isLayerZoomHidden("facilities", prev) === isLayerZoomHidden("facilities", z) &&
        isLayerZoomHidden("wells", prev) === isLayerZoomHidden("wells", z)
          ? prev
          : z,
      );
    };
    map.on("zoom", syncZoomBand);

    map.on("load", () => {
      if (!map.hasImage(FACILITY_ICON_ID)) {
        map.addImage(FACILITY_ICON_ID, createDiamondSdfImage(), { pixelRatio: 2, sdf: true });
      }

      if (meta.has_tiles) {
        map.addSource("ogprod", {
          type: "vector",
          url: `pmtiles://${absolutePmtilesUrl()}`,
        });
        for (const layer of buildVectorLayers()) {
          map.addLayer(layer);
        }
      } else {
        map.addSource("fields-fallback", {
          type: "geojson",
          data: buildFallbackGeoJson(fieldsSummary as unknown as { lon: number | null; lat: number | null }[]),
        });
        map.addSource("facilities-fallback", {
          type: "geojson",
          data: buildFallbackGeoJson(
            facilitiesSummary as unknown as { lon: number | null; lat: number | null }[],
          ),
        });
        for (const layer of buildFallbackLayers()) {
          map.addLayer(layer);
        }
      }

      const interactiveLayers = ["facilities-symbol", "wells-circle", "fields-fill"].filter((id) =>
        Boolean(map.getLayer(id)),
      );

      map.on("mousemove", (e) => {
        const feats = map.queryRenderedFeatures(e.point, { layers: interactiveLayers });
        if (!feats.length) {
          map.getCanvas().style.cursor = "";
          popupRef.current?.remove();
          return;
        }
        map.getCanvas().style.cursor = "pointer";
        const top = feats[0];
        const props = top.properties ?? {};
        const layerId = top.layer.id;
        let html = "";
        if (layerId === "wells-circle") {
          html = `<div class="font-semibold text-slate-900">${props.well_name ?? props.well_id}</div>
            <div class="text-[11px] text-slate-500">Well · ${String(props.source ?? "").toUpperCase()}${props.field_name ? ` · ${props.field_name}` : ""}</div>
            ${props.latest_oil_bpd ? `<div class="font-tabular mt-1 text-[11px]">Latest Oil: ${formatCompactNumber(Number(props.latest_oil_bpd))} bbl/d</div>` : ""}`;
        } else if (layerId === "facilities-symbol") {
          html = `<div class="font-semibold text-slate-900">${props.facility_name ?? props.facility_id}</div>
            <div class="text-[11px] text-slate-500">${props.facility_type ?? "Facility"} · ${String(props.source ?? "").toUpperCase()}</div>
            ${props.oil_capacity_bpd ? `<div class="font-tabular mt-1 text-[11px]">Cap: ${formatCompactNumber(Number(props.oil_capacity_bpd))} bbl/d${props.oil_utilization ? ` · Util: ${formatPercent(Number(props.oil_utilization))}` : ""}</div>` : ""}`;
        } else {
          html = `<div class="font-semibold text-slate-900">${props.field_name ?? props.field_id}</div>
            <div class="text-[11px] text-slate-500">Field · ${String(props.source ?? "").toUpperCase()}${props.basin ? ` · ${props.basin}` : ""}</div>
            ${props.latest_oil_bpd ? `<div class="font-tabular mt-1 text-[11px]">Latest Oil: ${formatCompactNumber(Number(props.latest_oil_bpd))} bbl/d</div>` : ""}`;
        }
        popupRef.current?.setLngLat(e.lngLat).setHTML(html).addTo(map);
      });

      map.on("click", (e) => {
        const feats = map.queryRenderedFeatures(e.point, { layers: interactiveLayers });
        if (!feats.length) return;
        const top = feats[0];
        const props = top.properties ?? {};
        const src = String(props.source ?? "");
        if (!src) return;
        const lng = e.lngLat.lng;
        const lat = e.lngLat.lat;

        if (top.layer.id === "wells-circle") {
          onSelectRef.current({
            kind: "well",
            source: src,
            id: String(props.well_id),
            name: String(props.well_name ?? props.well_id),
            fieldName: props.field_name ? String(props.field_name) : undefined,
            basin: props.basin ? String(props.basin) : undefined,
            status: props.status ? String(props.status) : undefined,
            liftMethod: props.lift_method ? String(props.lift_method) : undefined,
            firstYear: props.first_year ? Number(props.first_year) : undefined,
            lastYear: props.last_year ? Number(props.last_year) : undefined,
            lon: lng,
            lat,
          });
        } else if (top.layer.id === "facilities-symbol") {
          onSelectRef.current({
            kind: "facility",
            source: src,
            id: String(props.facility_id),
            name: String(props.facility_name ?? props.facility_id),
            lon: lng,
            lat,
          });
        } else {
          onSelectRef.current({
            kind: "field",
            source: src,
            id: String(props.field_id),
            name: String(props.field_name ?? props.field_id),
            lon: lng,
            lat,
          });
        }
      });

      setCurrentZoom(map.getZoom());
      setMapReady(true);
    });

    return () => {
      popupRef.current?.remove();
      map.remove();
      mapRef.current = null;
      setMapReady(false);
    };
  }, [meta.has_tiles, fieldsSummary, facilitiesSummary]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const filter: maplibregl.FilterSpecification | null =
      activeSource === null ? null : ["==", ["get", "source"], activeSource];

    const toggles: [string, boolean][] = [
      ["fields-fill", showFields],
      ["fields-outline", showFields],
      ["fields-label", showFields],
      ["facilities-symbol", showFacilities],
      ["wells-circle", showWells],
    ];
    for (const [id, visible] of toggles) {
      if (!map.getLayer(id)) continue;
      map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      map.setFilter(id, filter);
    }

    const selectionToggles: [string, "field" | "facility" | "well", boolean][] = [
      ["fields-selected", "field", showFields],
      ["facilities-selected", "facility", showFacilities],
      ["wells-selected", "well", showWells],
    ];
    for (const [id, kind, visible] of selectionToggles) {
      if (!map.getLayer(id)) continue;
      map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
      map.setFilter(id, buildSelectionFilter(kind, selection, activeSource));
    }
  }, [activeSource, showFields, showFacilities, showWells, selection, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || selection.kind !== "overview") return;
    if (activeSource === null) {
      map.flyTo({ center: [-30, 14], zoom: INITIAL_MAP_ZOOM, duration: 700 });
      return;
    }
    const metaSrc = meta.sources.find((s) => s.id === activeSource);
    if (metaSrc?.bbox) {
      const [minx, miny, maxx, maxy] = metaSrc.bbox;
      map.fitBounds(
        [
          [minx, miny],
          [maxx, maxy],
        ],
        { padding: 48, duration: 750, maxZoom: 8 },
      );
    }
  }, [activeSource, selection.kind, meta.sources, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (
      (selection.kind === "field" || selection.kind === "facility" || selection.kind === "well") &&
      selection.lon !== undefined &&
      selection.lat !== undefined
    ) {
      const targetZoom = selection.kind === "well" ? 10.5 : selection.kind === "facility" ? 9.5 : 8;
      map.flyTo({
        center: [selection.lon, selection.lat],
        zoom: Math.max(map.getZoom(), targetZoom),
        duration: 650,
      });
    }
  }, [selection, mapReady]);

  return (
    <section
      aria-label="Interactive production map"
      className="relative h-[46vh] min-h-[340px] w-full overflow-hidden border-b border-slate-200 bg-slate-100 lg:h-full lg:border-b-0 lg:border-r"
    >
      <div ref={containerRef} className="h-full w-full" />

      <LayerTogglePanel
        showFields={showFields}
        showFacilities={showFacilities}
        showWells={showWells}
        facilitiesZoomHidden={isLayerZoomHidden("facilities", currentZoom)}
        wellsZoomHidden={isLayerZoomHidden("wells", currentZoom)}
        onToggleFields={setShowFields}
        onToggleFacilities={setShowFacilities}
        onToggleWells={setShowWells}
      />

      <MapLegend />
    </section>
  );
}

interface LayerTogglePanelProps {
  showFields: boolean;
  showFacilities: boolean;
  showWells: boolean;
  facilitiesZoomHidden: boolean;
  wellsZoomHidden: boolean;
  onToggleFields: (visible: boolean) => void;
  onToggleFacilities: (visible: boolean) => void;
  onToggleWells: (visible: boolean) => void;
}

function LayerTogglePanel({
  showFields,
  showFacilities,
  showWells,
  facilitiesZoomHidden,
  wellsZoomHidden,
  onToggleFields,
  onToggleFacilities,
  onToggleWells,
}: LayerTogglePanelProps) {
  return (
    <div className="absolute left-3 top-3 z-10 rounded-lg border border-slate-200/90 bg-white/95 px-3 py-2 shadow-sm backdrop-blur-xs">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        <Layers className="h-3.5 w-3.5" />
        Layers
      </div>
      <div className="flex flex-col gap-1 text-xs text-slate-700">
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={showFields}
            onChange={(e) => onToggleFields(e.target.checked)}
            className="accent-amber-600"
          />
          <span className="h-2.5 w-2.5 rounded-xs bg-amber-600/80" />
          <span>Fields</span>
        </label>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={showFacilities}
            onChange={(e) => onToggleFacilities(e.target.checked)}
            className="accent-cyan-700"
          />
          <span className="h-2.5 w-2.5 rotate-45 border border-white bg-cyan-700 ring-1 ring-cyan-700" />
          <span className={showFacilities && facilitiesZoomHidden ? "text-slate-400" : undefined}>
            FPSOs / Platforms{showFacilities && facilitiesZoomHidden ? " (zoom in)" : ""}
          </span>
        </label>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={showWells}
            onChange={(e) => onToggleWells(e.target.checked)}
            className="accent-slate-700"
          />
          <span className="h-2 w-2 rounded-full bg-slate-700" />
          <span className={showWells && wellsZoomHidden ? "text-slate-400" : undefined}>
            Wells{showWells && wellsZoomHidden ? " (zoom in)" : ""}
          </span>
        </label>
      </div>
    </div>
  );
}

function MapLegend() {
  return (
    <div className="absolute bottom-3 left-3 z-10 rounded-lg border border-slate-200/90 bg-white/92 px-3 py-1.5 text-[11px] text-slate-600 shadow-xs backdrop-blur-xs">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-xs bg-[#fbbf24]" />
          <span>5k</span>
          <span className="inline-block h-2.5 w-2.5 rounded-xs bg-[#d97706]" />
          <span>50k</span>
          <span className="inline-block h-2.5 w-2.5 rounded-xs bg-[#9a3412]" />
          <span>250k+ bbl/d</span>
        </span>
        <span className="hidden text-slate-300 sm:inline">|</span>
        <span className="hidden sm:inline">Click well to query HF Parquet via DuckDB-WASM</span>
      </div>
    </div>
  );
}
