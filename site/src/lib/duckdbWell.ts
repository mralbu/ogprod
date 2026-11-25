import type { MonthlyPoint } from "../types/data";

export type WellQueryPlan =
  | {
      canQuery: true;
      firstYear: number;
      lastYear: number;
    }
  | {
      canQuery: false;
      reason: "field_level_source" | "no_well_history";
    };

/**
 * Decide whether a selected well has well-level production partitions to query.
 * Avoids booting DuckDB-WASM or scanning full history for field-only sources and dry wellbores.
 */
export function resolveWellQueryPlan(params: {
  sourceEntityType?: "well" | "field";
  firstYear?: number;
  lastYear?: number;
}): WellQueryPlan {
  if (params.sourceEntityType && params.sourceEntityType !== "well") {
    return { canQuery: false, reason: "field_level_source" };
  }
  if (
    params.firstYear === undefined ||
    params.lastYear === undefined ||
    !Number.isInteger(params.firstYear) ||
    !Number.isInteger(params.lastYear) ||
    params.firstYear > params.lastYear
  ) {
    return { canQuery: false, reason: "no_well_history" };
  }
  return { canQuery: true, firstYear: params.firstYear, lastYear: params.lastYear };
}

/** Build the pruned list of HF Parquet URLs covering ONLY year=firstYear..lastYear. */
export function buildWellPartitionUrls(
  mirrorBaseUrl: string,
  source: string,
  firstYear: number,
  lastYear: number,
  availableYears?: readonly number[],
): string[] {
  if (!Number.isInteger(firstYear) || !Number.isInteger(lastYear) || firstYear > lastYear) {
    return [];
  }
  const cleanBase = mirrorBaseUrl.replace(/\/+$/, "");
  const allowed = availableYears ? new Set(availableYears) : null;
  const urls: string[] = [];
  for (let y = firstYear; y <= lastYear; y += 1) {
    if (allowed && !allowed.has(y)) {
      continue;
    }
    urls.push(`${cleanBase}/production/source=${encodeURIComponent(source)}/year=${y}/data.parquet`);
  }
  return urls;
}

function escapeSqlLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

/** Build the DuckDB SQL query over pruned remote Parquet partitions for a single well. */
export function buildWellQuerySql(urls: readonly string[], wellId: string): string {
  const listSql = urls.map(escapeSqlLiteral).join(", ");
  const idSql = escapeSqlLiteral(wellId);
  return `
    SELECT strftime(period, '%Y-%m-%d') AS p,
           SUM(CAST(oil_prod AS DOUBLE)) AS oil_prod,
           SUM(CAST(condensate_prod AS DOUBLE)) AS condensate_prod,
           SUM(CAST(ngl_prod AS DOUBLE)) AS ngl_prod,
           SUM(CAST(gas_prod AS DOUBLE)) AS gas_prod,
           SUM(CAST(water_prod AS DOUBLE)) AS water_prod,
           SUM(CAST(water_inj AS DOUBLE)) AS water_inj,
           SUM(CAST(gas_inj AS DOUBLE)) AS gas_inj,
           SUM(CAST(co2_inj AS DOUBLE)) AS co2_inj
    FROM read_parquet([${listSql}])
    WHERE entity_id = ${idSql}
    GROUP BY period
    ORDER BY period
  `.trim();
}

interface DuckDbHandle {
  query(sql: string): Promise<MonthlyPoint[]>;
}

let duckdbPromise: Promise<DuckDbHandle> | null = null;

/** Lazily initialize DuckDB-WASM only on first well click (never on initial page load). */
async function getDuckDb(): Promise<DuckDbHandle> {
  if (duckdbPromise) {
    return duckdbPromise;
  }
  duckdbPromise = (async () => {
    const duckdb = await import("@duckdb/duckdb-wasm");
    const bundles = duckdb.getJsDelivrBundles();
    const bundle = await duckdb.selectBundle(bundles);
    const workerUrl = URL.createObjectURL(
      new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }),
    );
    const worker = new Worker(workerUrl);
    const logger = new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING);
    const db = new duckdb.AsyncDuckDB(logger, worker);
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    URL.revokeObjectURL(workerUrl);
    const conn = await db.connect();

    return {
      async query(sql: string): Promise<MonthlyPoint[]> {
        const result = await conn.query(sql);
        const rows: MonthlyPoint[] = [];
        for (const raw of result.toArray()) {
          const obj = raw.toJSON() as Record<string, unknown>;
          const point: MonthlyPoint = { p: String(obj.p ?? "") };
          for (const col of [
            "oil_prod",
            "condensate_prod",
            "ngl_prod",
            "gas_prod",
            "water_prod",
            "water_inj",
            "gas_inj",
            "co2_inj",
          ] as const) {
            const v = obj[col];
            if (typeof v === "number" && Number.isFinite(v)) {
              point[col] = v;
            }
          }
          rows.push(point);
        }
        return rows;
      },
    };
  })();
  return duckdbPromise;
}

const wellSeriesCache = new Map<string, MonthlyPoint[]>();

export async function queryWellSeriesFromMirror(params: {
  mirrorBaseUrl: string;
  source: string;
  wellId: string;
  firstYear: number;
  lastYear: number;
  availableYears?: readonly number[];
}): Promise<{ rows: MonthlyPoint[]; partitionsQueried: number }> {
  const cacheKey = `${params.source}:${params.wellId}:${params.firstYear}-${params.lastYear}`;
  const urls = buildWellPartitionUrls(
    params.mirrorBaseUrl,
    params.source,
    params.firstYear,
    params.lastYear,
    params.availableYears,
  );
  if (urls.length === 0) {
    return { rows: [], partitionsQueried: 0 };
  }
  const cached = wellSeriesCache.get(cacheKey);
  if (cached) {
    return { rows: cached, partitionsQueried: urls.length };
  }
  const db = await getDuckDb();
  const sql = buildWellQuerySql(urls, params.wellId);
  const rows = await db.query(sql);
  wellSeriesCache.set(cacheKey, rows);
  return { rows, partitionsQueried: urls.length };
}
