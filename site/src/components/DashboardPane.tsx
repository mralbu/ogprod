import {
  AlertTriangle,
  ArrowLeft,
  Database,
  Download,
  ExternalLink,
  ImageDown,
  Loader2,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { buildSeriesCsv, downloadCsv } from "../lib/csvExport";
import {
  convertRate,
  formatCompactNumber,
  formatPercent,
  PRODUCT_SPECS,
  unitLabelFor,
} from "../lib/units";
import type {
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  KnownGap,
  MonthlyPoint,
  RateColumn,
  SiteMeta,
  UnitSystem,
} from "../types/data";
import { ALL_RATE_COLUMNS } from "../types/data";
import { TimeSeriesChart, type TimeSeriesChartHandle } from "./TimeSeriesChart";
import { UtilizationChart } from "./UtilizationChart";

interface DashboardPaneProps {
  meta: SiteMeta;
  activeSource: string;
  selection: EntitySelection;
  series: MonthlyPoint[];
  loadingSeries: boolean;
  seriesError: string | null;
  wellPartitionsQueried: number | null;
  activeProducts: RateColumn[];
  onToggleProduct: (col: RateColumn) => void;
  unitSystem: UnitSystem;
  fieldsSummary: FieldSummary[];
  facilitiesSummary: FacilitySummary[];
  basinMonthly: Record<string, Record<string, MonthlyPoint[]>>;
  onSelectEntity: (sel: EntitySelection) => void;
}

type OverviewTab = "fields" | "facilities" | "basins";

export function DashboardPane({
  meta,
  activeSource,
  selection,
  series,
  loadingSeries,
  seriesError,
  wellPartitionsQueried,
  activeProducts,
  onToggleProduct,
  unitSystem,
  fieldsSummary,
  facilitiesSummary,
  basinMonthly,
  onSelectEntity,
}: DashboardPaneProps) {
  const chartRef = useRef<TimeSeriesChartHandle>(null);
  const [tab, setTab] = useState<OverviewTab>("fields");

  const activeSourcesMeta = useMemo(() => {
    const scopeSource =
      selection.kind === "overview" ? activeSource : selection.source;
    if (scopeSource === "all") return meta.sources;
    return meta.sources.filter((s) => s.id === scopeSource);
  }, [meta.sources, activeSource, selection]);

  const selectedField = useMemo(() => {
    if (selection.kind !== "field") return null;
    return (
      fieldsSummary.find(
        (f) => f.source === selection.source && f.field_id === selection.id,
      ) ?? null
    );
  }, [selection, fieldsSummary]);

  const selectedFacility = useMemo(() => {
    if (selection.kind !== "facility") return null;
    return (
      facilitiesSummary.find(
        (f) => f.source === selection.source && f.facility_id === selection.id,
      ) ?? null
    );
  }, [selection, facilitiesSummary]);

  const availableProducts = useMemo(() => {
    const present = new Set<RateColumn>();
    for (const row of series) {
      for (const col of ALL_RATE_COLUMNS) {
        if (row[col] !== undefined && row[col] !== null) {
          present.add(col);
        }
      }
    }
    if (present.size === 0) {
      for (const src of activeSourcesMeta) {
        for (const col of src.available_products) present.add(col);
      }
    }
    return ALL_RATE_COLUMNS.filter((c) => present.has(c));
  }, [series, activeSourcesMeta]);

  const visibleProducts = useMemo(() => {
    const filtered = activeProducts.filter((c) => availableProducts.includes(c));
    return filtered.length > 0 ? filtered : availableProducts.slice(0, 2);
  }, [activeProducts, availableProducts]);

  const latestRow = series.length > 0 ? series[series.length - 1] : null;
  const firstRow = series.length > 0 ? series[0] : null;

  const applicableGaps = useMemo<KnownGap[]>(() => {
    const gaps: KnownGap[] = [];
    for (const src of activeSourcesMeta) {
      for (const g of src.known_gaps) {
        if (visibleProducts.includes(g.column)) {
          gaps.push(g);
        }
      }
    }
    return gaps;
  }, [activeSourcesMeta, visibleProducts]);

  const filteredFields = useMemo(
    () =>
      fieldsSummary
        .filter((f) => activeSource === "all" || f.source === activeSource)
        .slice(0, 25),
    [fieldsSummary, activeSource],
  );

  const filteredFacilities = useMemo(
    () =>
      facilitiesSummary
        .filter((f) => activeSource === "all" || f.source === activeSource)
        .slice(0, 25),
    [facilitiesSummary, activeSource],
  );

  const basinRows = useMemo(() => {
    const list: { source: string; basin: string; latestOil: number; latestGas: number }[] = [];
    for (const [src, basins] of Object.entries(basinMonthly)) {
      if (activeSource !== "all" && src !== activeSource) continue;
      for (const [basin, pts] of Object.entries(basins)) {
        if (!pts.length) continue;
        const last = pts[pts.length - 1];
        list.push({
          source: src,
          basin,
          latestOil: last.oil_prod ?? 0,
          latestGas: last.gas_prod ?? 0,
        });
      }
    }
    return list.sort((a, b) => b.latestOil - a.latestOil).slice(0, 25);
  }, [basinMonthly, activeSource]);

  const title = useMemo(() => {
    if (selection.kind === "field") return selectedField?.field_name ?? selection.name ?? selection.id;
    if (selection.kind === "facility")
      return selectedFacility?.facility_name ?? selection.name ?? selection.id;
    if (selection.kind === "well") return selection.name ?? selection.id;
    if (selection.kind === "basin") return `${selection.basin} Basin`;
    if (activeSource === "all") return "All Sources Overview";
    const s = meta.sources.find((item) => item.id === activeSource);
    return s ? `${s.country} (${s.publisher})` : activeSource;
  }, [selection, selectedField, selectedFacility, activeSource, meta.sources]);

  const exportSlug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "ogprod";

  const handleExportCsv = () => {
    const csv = buildSeriesCsv(series, visibleProducts, unitSystem, {
      includeUtilization: selection.kind === "facility",
    });
    downloadCsv(`${exportSlug}-production.csv`, csv);
  };

  const handleExportPng = () => {
    chartRef.current?.exportPng(`${exportSlug}-chart.png`);
  };

  const latestOilConverted = convertRate(latestRow?.oil_prod, "oil_prod", unitSystem);
  const latestGasConverted = convertRate(latestRow?.gas_prod, "gas_prod", unitSystem);

  return (
    <aside className="flex h-full flex-col overflow-y-auto bg-[#f8f9fb]">
      <div className="flex flex-col gap-4 p-4 sm:p-5">
        {/* Header & Breadcrumb */}
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            {selection.kind !== "overview" && (
              <button
                type="button"
                onClick={() => onSelectEntity({ kind: "overview" })}
                className="mb-1.5 inline-flex items-center gap-1 text-xs font-medium text-cyan-800 hover:text-cyan-950"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Back to overview
              </button>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h2>
              {selection.kind !== "overview" && (
                <span className="rounded bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-700">
                  {selection.kind}
                </span>
              )}
              {selection.kind === "well" && wellPartitionsQueried !== null && (
                <span className="inline-flex items-center gap-1 rounded bg-cyan-50 px-2 py-0.5 text-[10px] font-medium text-cyan-900 ring-1 ring-cyan-200">
                  <Database className="h-3 w-3" />
                  DuckDB-WASM · {wellPartitionsQueried} yr partition
                  {wellPartitionsQueried === 1 ? "" : "s"}
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-500">
              {selection.kind === "field" && selectedField && (
                <>
                  {selectedField.source.toUpperCase()}
                  {selectedField.basin ? ` · ${selectedField.basin} Basin` : ""}
                  {selectedField.operator ? ` · ${selectedField.operator}` : ""}
                  {selectedField.water_depth_m ? ` · ${selectedField.water_depth_m} m depth` : ""}
                </>
              )}
              {selection.kind === "facility" && selectedFacility && (
                <>
                  {selectedFacility.facility_type ?? "Facility"} ·{" "}
                  {selectedFacility.source.toUpperCase()}
                  {selectedFacility.fields ? ` · Fields: ${selectedFacility.fields}` : ""}
                  {selectedFacility.operator ? ` · ${selectedFacility.operator}` : ""}
                </>
              )}
              {selection.kind === "well" && (
                <>
                  {selection.source.toUpperCase()}
                  {selection.fieldName ? ` · Field: ${selection.fieldName}` : ""}
                  {selection.status ? ` · ${selection.status}` : ""}
                  {selection.liftMethod ? ` · ${selection.liftMethod}` : ""}
                </>
              )}
              {selection.kind === "overview" && (
                <>
                  {activeSourcesMeta.map((s) => `${s.country} (${s.publisher})`).join(" · ")}
                </>
              )}
            </p>
          </div>

          {/* Export buttons */}
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleExportPng}
              disabled={series.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              <ImageDown className="h-3.5 w-3.5" />
              PNG
            </button>
            <button
              type="button"
              onClick={handleExportCsv}
              disabled={series.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              <Download className="h-3.5 w-3.5" />
              CSV
            </button>
          </div>
        </div>

        {/* KPI Strip */}
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
            <div className="text-[11px] font-medium text-slate-500">Latest Oil</div>
            <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
              {formatCompactNumber(latestOilConverted)}
            </div>
            <div className="text-[10px] text-slate-400">
              {unitLabelFor("oil_prod", unitSystem)}
            </div>
          </div>

          <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
            <div className="text-[11px] font-medium text-slate-500">Latest Gas</div>
            <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
              {formatCompactNumber(latestGasConverted)}
            </div>
            <div className="text-[10px] text-slate-400">
              {unitLabelFor("gas_prod", unitSystem)}
            </div>
          </div>

          <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
            <div className="text-[11px] font-medium text-slate-500">
              {selection.kind === "facility" ? "Oil Utilization" : "Date Range"}
            </div>
            <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
              {selection.kind === "facility"
                ? formatPercent(selectedFacility?.oil_utilization)
                : firstRow && latestRow
                  ? `${firstRow.p.slice(0, 4)}–${latestRow.p.slice(0, 4)}`
                  : "—"}
            </div>
            <div className="text-[10px] text-slate-400">
              {selection.kind === "facility"
                ? selectedFacility?.oil_capacity_bpd
                  ? `Cap ${formatCompactNumber(convertRate(selectedFacility.oil_capacity_bpd, "oil_prod", unitSystem))} ${unitLabelFor("oil_prod", unitSystem)}`
                  : "No capacity listed"
                : latestRow
                  ? `Latest ${latestRow.p.slice(0, 7)}`
                  : "No series"}
            </div>
          </div>

          <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
            <div className="text-[11px] font-medium text-slate-500">
              {selection.kind === "field"
                ? "Cum. Oil"
                : selection.kind === "facility"
                  ? "Water Depth"
                  : "Months"}
            </div>
            <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
              {selection.kind === "field"
                ? selectedField?.cum_oil_mmbbl !== null && selectedField?.cum_oil_mmbbl !== undefined
                  ? `${selectedField.cum_oil_mmbbl.toFixed(1)} MMbbl`
                  : "—"
                : selection.kind === "facility"
                  ? selectedFacility?.water_depth_m
                    ? `${Math.round(selectedFacility.water_depth_m)} m`
                    : "—"
                  : series.length.toLocaleString()}
            </div>
            <div className="text-[10px] text-slate-400">
              {selection.kind === "field"
                ? "cumulative crude"
                : selection.kind === "facility"
                  ? selectedFacility?.start_year
                    ? `Start ${selectedFacility.start_year}`
                    : "offshore / onshore"
                  : "monthly points"}
            </div>
          </div>
        </div>

        {/* Known data gaps alert */}
        {applicableGaps.length > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/90 px-3 py-2 text-xs text-amber-900">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" />
            <div>
              {applicableGaps.map((g) => (
                <p key={`${g.column}-${g.start}`}>
                  <span className="font-semibold">
                    Known reporting gap ({g.start.slice(0, 7)} to {g.end.slice(0, 7)}):
                  </span>{" "}
                  {g.description}
                </p>
              ))}
            </div>
          </div>
        )}

        {/* Product & injection toggle bar + Main Chart */}
        <div className="rounded-lg border border-slate-200/90 bg-white p-3.5 shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {availableProducts.map((col) => {
              const spec = PRODUCT_SPECS[col];
              const active = visibleProducts.includes(col);
              return (
                <button
                  key={col}
                  type="button"
                  onClick={() => onToggleProduct(col)}
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                    active
                      ? "border-slate-800 bg-slate-900 text-white"
                      : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: spec.color }}
                  />
                  {spec.shortLabel}
                </button>
              );
            })}
          </div>

          {loadingSeries ? (
            <div className="flex h-[310px] flex-col items-center justify-center gap-2 text-xs text-slate-500">
              <Loader2 className="h-5 w-5 animate-spin text-cyan-700" />
              <span>
                {selection.kind === "well"
                  ? "Initializing DuckDB-WASM & querying HF Parquet over HTTP Range…"
                  : "Loading monthly series…"}
              </span>
            </div>
          ) : seriesError ? (
            <div className="flex h-[240px] flex-col items-center justify-center gap-1 text-center text-xs text-red-700">
              <p className="font-medium">Unable to load production series</p>
              <p className="max-w-md text-slate-500">{seriesError}</p>
            </div>
          ) : series.length === 0 ? (
            <div className="flex h-[220px] items-center justify-center text-xs text-slate-500">
              No monthly production records available for this selection.
            </div>
          ) : (
            <TimeSeriesChart
              ref={chartRef}
              rows={series}
              activeProducts={visibleProducts}
              unitSystem={unitSystem}
              oilCapacityBpd={
                selection.kind === "facility" ? selectedFacility?.oil_capacity_bpd : null
              }
            />
          )}
        </div>

        {/* Dedicated FPSO / Facility Utilization Chart */}
        {selection.kind === "facility" &&
          selectedFacility?.oil_capacity_bpd &&
          series.some((r) => r.oil_util !== undefined) && (
            <div className="rounded-lg border border-slate-200/90 bg-white p-3.5">
              <div className="mb-1 flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                  Oil Capacity Utilization (Actual / Nameplate)
                </h3>
                <span className="font-tabular text-xs font-medium text-cyan-800">
                  Latest: {formatPercent(selectedFacility.oil_utilization)}
                </span>
              </div>
              <UtilizationChart rows={series} />
            </div>
          )}

        {/* Overview Leaderboards (Fields / FPSOs / Basins) */}
        {selection.kind === "overview" && (
          <div className="rounded-lg border border-slate-200/90 bg-white">
            <div className="flex items-center justify-between border-b border-slate-200 px-3.5 pt-2">
              <div className="flex gap-4">
                {(
                  [
                    ["fields", "Top Fields"],
                    ["facilities", "FPSOs & Platforms"],
                    ["basins", "Basins"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTab(key)}
                    className={`border-b-2 pb-2 text-xs font-medium transition-colors ${
                      tab === key
                        ? "border-cyan-700 text-slate-900"
                        : "border-transparent text-slate-500 hover:text-slate-800"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div className="max-h-72 overflow-y-auto">
              {tab === "fields" && (
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3.5 py-2 font-medium">Field</th>
                      <th className="px-2 py-2 font-medium">Basin</th>
                      <th className="px-2 py-2 text-right font-medium">
                        Oil ({unitLabelFor("oil_prod", unitSystem)})
                      </th>
                      <th className="px-3.5 py-2 text-right font-medium">Cum. Oil</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredFields.map((f) => (
                      <tr
                        key={`${f.source}:${f.field_id}`}
                        onClick={() =>
                          onSelectEntity({
                            kind: "field",
                            source: f.source,
                            id: f.field_id,
                            name: f.field_name,
                            lon: f.lon ?? undefined,
                            lat: f.lat ?? undefined,
                          })
                        }
                        className="cursor-pointer hover:bg-slate-50"
                      >
                        <td className="px-3.5 py-2 font-medium text-slate-900">
                          {f.field_name}
                          <span className="ml-1.5 text-[10px] font-normal uppercase text-slate-400">
                            {f.source}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-slate-500">{f.basin ?? "—"}</td>
                        <td className="font-tabular px-2 py-2 text-right text-slate-800">
                          {formatCompactNumber(convertRate(f.latest_oil_bpd, "oil_prod", unitSystem))}
                        </td>
                        <td className="font-tabular px-3.5 py-2 text-right text-slate-500">
                          {f.cum_oil_mmbbl !== null ? `${f.cum_oil_mmbbl.toFixed(1)}M` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {tab === "facilities" && (
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3.5 py-2 font-medium">Facility</th>
                      <th className="px-2 py-2 text-right font-medium">
                        Oil ({unitLabelFor("oil_prod", unitSystem)})
                      </th>
                      <th className="px-2 py-2 text-right font-medium">Capacity</th>
                      <th className="px-3.5 py-2 text-right font-medium">Util %</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredFacilities.map((fac) => (
                      <tr
                        key={`${fac.source}:${fac.facility_id}`}
                        onClick={() =>
                          onSelectEntity({
                            kind: "facility",
                            source: fac.source,
                            id: fac.facility_id,
                            name: fac.facility_name,
                            lon: fac.lon ?? undefined,
                            lat: fac.lat ?? undefined,
                          })
                        }
                        className="cursor-pointer hover:bg-slate-50"
                      >
                        <td className="px-3.5 py-2 font-medium text-slate-900">
                          {fac.facility_name}
                          <span className="ml-1.5 text-[10px] font-normal uppercase text-slate-400">
                            {fac.source}
                          </span>
                        </td>
                        <td className="font-tabular px-2 py-2 text-right text-slate-800">
                          {formatCompactNumber(
                            convertRate(fac.latest_oil_bpd, "oil_prod", unitSystem),
                          )}
                        </td>
                        <td className="font-tabular px-2 py-2 text-right text-slate-500">
                          {formatCompactNumber(
                            convertRate(fac.oil_capacity_bpd, "oil_prod", unitSystem),
                          )}
                        </td>
                        <td className="font-tabular px-3.5 py-2 text-right font-medium text-cyan-800">
                          {formatPercent(fac.oil_utilization)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {tab === "basins" && (
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3.5 py-2 font-medium">Basin</th>
                      <th className="px-2 py-2 font-medium">Source</th>
                      <th className="px-2 py-2 text-right font-medium">
                        Oil ({unitLabelFor("oil_prod", unitSystem)})
                      </th>
                      <th className="px-3.5 py-2 text-right font-medium">
                        Gas ({unitLabelFor("gas_prod", unitSystem)})
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {basinRows.map((b) => (
                      <tr
                        key={`${b.source}:${b.basin}`}
                        onClick={() =>
                          onSelectEntity({ kind: "basin", source: b.source, basin: b.basin })
                        }
                        className="cursor-pointer hover:bg-slate-50"
                      >
                        <td className="px-3.5 py-2 font-medium text-slate-900">{b.basin}</td>
                        <td className="px-2 py-2 uppercase text-slate-500">{b.source}</td>
                        <td className="font-tabular px-2 py-2 text-right text-slate-800">
                          {formatCompactNumber(convertRate(b.latestOil, "oil_prod", unitSystem))}
                        </td>
                        <td className="font-tabular px-3.5 py-2 text-right text-slate-600">
                          {formatCompactNumber(convertRate(b.latestGas, "gas_prod", unitSystem))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}

        {/* Attribution & Freshness Footer */}
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3 text-[11px] text-slate-500">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {activeSourcesMeta.map((s) => (
              <a
                key={s.id}
                href={s.docs_url || "#"}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 hover:text-slate-800"
              >
                {s.publisher}
                <ExternalLink className="h-2.5 w-2.5" />
              </a>
            ))}
          </div>
          <div className="font-tabular">
            Mirror:{" "}
            <a
              href={`https://huggingface.co/datasets/${meta.mirror.repo}`}
              target="_blank"
              rel="noreferrer"
              className="underline hover:text-slate-800"
            >
              {meta.mirror.repo}@{meta.mirror.revision}
            </a>{" "}
            · Built {meta.generated_at.slice(0, 10)}
          </div>
        </footer>
      </div>
    </aside>
  );
}
