import type {
  FacilitySummary,
  FieldSummary,
  MonthlyPoint,
  SearchEntry,
  SiteMeta,
} from "../types/data";

function dataUrl(relativePath: string): string {
  const base = import.meta.env.BASE_URL ?? "/";
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  return `${normalizedBase}data/${relativePath.replace(/^\/+/, "")}`;
}

async function fetchJson<T>(relativePath: string): Promise<T> {
  const resp = await fetch(dataUrl(relativePath));
  if (!resp.ok) {
    throw new Error(`Failed to fetch ${relativePath} (${resp.status})`);
  }
  return (await resp.json()) as T;
}

export function pmtilesUrl(): string {
  return dataUrl("tiles/ogprod.pmtiles");
}

export function llmsTxtUrl(): string {
  const base = import.meta.env.BASE_URL ?? "/";
  const normalizedBase = base.endsWith("/") ? base : `${base}/`;
  return `${normalizedBase}llms.txt`;
}

export async function loadInitialBundle(): Promise<{
  meta: SiteMeta;
  countryMonthly: Record<string, MonthlyPoint[]>;
  basinMonthly: Record<string, Record<string, MonthlyPoint[]>>;
  fieldsSummary: FieldSummary[];
  facilitiesSummary: FacilitySummary[];
}> {
  const [meta, countryMonthly, basinMonthly, fieldsSummary, facilitiesSummary] = await Promise.all([
    fetchJson<SiteMeta>("meta.json"),
    fetchJson<Record<string, MonthlyPoint[]>>("rollups/country_monthly.json"),
    fetchJson<Record<string, Record<string, MonthlyPoint[]>>>("rollups/basin_monthly.json"),
    fetchJson<FieldSummary[]>("rollups/fields_summary.json"),
    fetchJson<FacilitySummary[]>("rollups/facilities_summary.json"),
  ]);
  return { meta, countryMonthly, basinMonthly, fieldsSummary, facilitiesSummary };
}

const fieldShardCache = new Map<string, Promise<Record<string, MonthlyPoint[]>>>();
const facilityShardCache = new Map<string, Promise<Record<string, MonthlyPoint[]>>>();
let searchIndexPromise: Promise<SearchEntry[]> | null = null;

export function loadFieldMonthlyShard(source: string): Promise<Record<string, MonthlyPoint[]>> {
  let pending = fieldShardCache.get(source);
  if (!pending) {
    pending = fetchJson<Record<string, MonthlyPoint[]>>(`rollups/field_monthly/${source}.json`).catch(
      () => ({}),
    );
    fieldShardCache.set(source, pending);
  }
  return pending;
}

export function loadFacilityMonthlyShard(source: string): Promise<Record<string, MonthlyPoint[]>> {
  let pending = facilityShardCache.get(source);
  if (!pending) {
    pending = fetchJson<Record<string, MonthlyPoint[]>>(
      `rollups/facility_monthly/${source}.json`,
    ).catch(() => ({}));
    facilityShardCache.set(source, pending);
  }
  return pending;
}

export function loadSearchIndex(): Promise<SearchEntry[]> {
  if (!searchIndexPromise) {
    searchIndexPromise = fetchJson<SearchEntry[]>("search/index.json").catch(() => []);
  }
  return searchIndexPromise;
}
