export type RateColumn =
  | "oil_prod"
  | "condensate_prod"
  | "ngl_prod"
  | "gas_prod"
  | "water_prod"
  | "water_inj"
  | "gas_inj"
  | "co2_inj";

export const ALL_RATE_COLUMNS: readonly RateColumn[] = [
  "oil_prod",
  "gas_prod",
  "water_prod",
  "condensate_prod",
  "ngl_prod",
  "water_inj",
  "gas_inj",
  "co2_inj",
] as const;

export type UnitSystem = "oilfield_e3m3" | "oilfield_mmcf" | "metric_m3";

export interface KnownGap {
  column: RateColumn;
  start: string;
  end: string;
  description: string;
}

export interface SourceMeta {
  id: string;
  country: string;
  country_code: string;
  publisher: string;
  docs_url: string;
  licence: string;
  entity_type: "well" | "field";
  bbox: [number, number, number, number] | null;
  years: number[];
  first_period: string | null;
  last_period: string | null;
  updated_at: string | null;
  counts: {
    production_rows: number;
    producing_entities: number;
    fields: number;
    facilities: number;
    wells: number;
  };
  latest_oil_bpd: number | null;
  latest_gas_e3m3d: number | null;
  available_products: RateColumn[];
  known_gaps: KnownGap[];
}

export interface SiteMeta {
  schema_version: number;
  generated_at: string;
  mirror: {
    repo: string;
    revision: string;
    base_url: string;
  };
  units: Record<RateColumn, string>;
  has_tiles: boolean;
  sources: SourceMeta[];
}

export interface MonthlyPoint {
  p: string;
  oil_prod?: number;
  condensate_prod?: number;
  ngl_prod?: number;
  gas_prod?: number;
  water_prod?: number;
  water_inj?: number;
  gas_inj?: number;
  co2_inj?: number;
  oil_util?: number;
}

export interface FieldSummary {
  source: string;
  field_id: string;
  field_name: string;
  basin: string | null;
  region: string | null;
  environment: string | null;
  operator: string | null;
  status: string | null;
  water_depth_m: number | null;
  area_km2: number | null;
  first_year: number | null;
  last_year: number | null;
  latest_period: string | null;
  cum_oil_mmbbl: number | null;
  latest_oil_bpd: number | null;
  latest_gas_e3m3d: number | null;
  lon: number | null;
  lat: number | null;
}

export interface FacilitySummary {
  source: string;
  facility_id: string;
  facility_name: string;
  facility_type: string | null;
  fields: string | null;
  basin: string | null;
  operator: string | null;
  status: string | null;
  start_year: number | null;
  water_depth_m: number | null;
  oil_capacity_bpd: number | null;
  gas_capacity_e3m3d: number | null;
  first_year: number | null;
  last_year: number | null;
  latest_period: string | null;
  latest_oil_bpd: number | null;
  latest_gas_e3m3d: number | null;
  oil_utilization: number | null;
  gas_utilization: number | null;
  lon: number | null;
  lat: number | null;
}

export interface SearchEntry {
  t: "field" | "facility" | "well";
  s: string;
  id: string;
  n: string;
  f?: string;
  b?: string;
  lon?: number;
  lat?: number;
  fy?: number;
  ly?: number;
  lo?: number;
  cap?: number;
}

export type EntitySelection =
  | { kind: "overview" }
  | { kind: "basin"; source: string; basin: string }
  | { kind: "field"; source: string; id: string; name?: string; lon?: number; lat?: number }
  | { kind: "facility"; source: string; id: string; name?: string; lon?: number; lat?: number }
  | {
      kind: "well";
      source: string;
      id: string;
      name?: string;
      fieldName?: string;
      basin?: string;
      status?: string;
      liftMethod?: string;
      firstYear?: number;
      lastYear?: number;
      lon?: number;
      lat?: number;
    };
