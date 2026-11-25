import {
  ALL_CHART_SERIES,
  type ChartSeriesKey,
  type EntitySelection,
  type SourceFilter,
  type UnitSystem,
} from "../types/data";
import { UNIT_SYSTEMS } from "./units";

export interface AppUrlState {
  source: SourceFilter;
  selection: EntitySelection;
  products: ChartSeriesKey[];
  units: UnitSystem;
}

export const DEFAULT_PRODUCTS: ChartSeriesKey[] = ["oil_prod", "gas_prod"];
export const DEFAULT_UNITS: UnitSystem = "oilfield_e3m3";

const VALID_COLUMNS = new Set<string>(ALL_CHART_SERIES);
const VALID_UNITS = new Set<string>(UNIT_SYSTEMS.map((u) => u.id));

export function parseUrlState(search: string): AppUrlState {
  const params = new URLSearchParams(search);
  const rawSource = params.get("source")?.trim() ?? "";
  const source: SourceFilter = rawSource && rawSource !== "all" ? rawSource : null;

  const rawUnits = params.get("units") || "";
  const units: UnitSystem = VALID_UNITS.has(rawUnits) ? (rawUnits as UnitSystem) : DEFAULT_UNITS;

  const rawProducts = params.get("products");
  let products = DEFAULT_PRODUCTS;
  if (rawProducts) {
    const parsed = rawProducts
      .split(",")
      .map((s) => s.trim())
      .filter((s): s is ChartSeriesKey => VALID_COLUMNS.has(s));
    if (parsed.length > 0) {
      products = parsed;
    }
  }

  const selection = parseEntityParam(params.get("entity"), source);
  return { source, selection, products, units };
}

function parseEntityParam(raw: string | null, fallbackSource: SourceFilter): EntitySelection {
  if (!raw) {
    return { kind: "overview" };
  }
  const idx = raw.indexOf(":");
  if (idx <= 0) {
    return { kind: "overview" };
  }
  const kind = raw.slice(0, idx);
  const rest = raw.slice(idx + 1);
  const slashIdx = rest.indexOf("/");
  const entitySource = slashIdx > 0 ? rest.slice(0, slashIdx) : fallbackSource;
  const entityId = slashIdx > 0 ? rest.slice(slashIdx + 1) : rest;
  if (!entityId || !entitySource || entitySource === "all") {
    return { kind: "overview" };
  }
  if (kind === "field") {
    return { kind: "field", source: entitySource, id: entityId };
  }
  if (kind === "facility") {
    return { kind: "facility", source: entitySource, id: entityId };
  }
  if (kind === "well") {
    return { kind: "well", source: entitySource, id: entityId };
  }
  if (kind === "basin") {
    return { kind: "basin", source: entitySource, basin: entityId };
  }
  return { kind: "overview" };
}

export function serializeUrlState(state: AppUrlState): string {
  const params = new URLSearchParams();
  if (state.source) {
    params.set("source", state.source);
  }
  if (state.selection.kind === "field") {
    params.set("entity", `field:${state.selection.source}/${state.selection.id}`);
  } else if (state.selection.kind === "facility") {
    params.set("entity", `facility:${state.selection.source}/${state.selection.id}`);
  } else if (state.selection.kind === "well") {
    params.set("entity", `well:${state.selection.source}/${state.selection.id}`);
  } else if (state.selection.kind === "basin") {
    params.set("entity", `basin:${state.selection.source}/${state.selection.basin}`);
  }

  const isDefaultProducts =
    state.products.length === DEFAULT_PRODUCTS.length &&
    state.products.every((p, i) => p === DEFAULT_PRODUCTS[i]);
  if (!isDefaultProducts && state.products.length > 0) {
    params.set("products", state.products.join(","));
  }
  if (state.units !== DEFAULT_UNITS) {
    params.set("units", state.units);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
