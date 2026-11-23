import { AlertTriangle, ExternalLink, Loader2 } from "lucide-react";
import { useMemo, useRef } from "react";
import { buildSeriesCsv, downloadCsv } from "../lib/csvExport";
import { formatPercent, PRODUCT_SPECS } from "../lib/units";
import type {
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  KnownGap,
  MonthlyPoint,
  RateColumn,
  SiteMeta,
  SourceFilter,
  SourceMeta,
  UnitSystem,
} from "../types/data";
import { ALL_RATE_COLUMNS } from "../types/data";
import { DashboardHeader } from "./DashboardHeader";
import { KpiStrip } from "./KpiStrip";
import { OverviewLeaderboards } from "./OverviewLeaderboards";
import { TimeSeriesChart, type TimeSeriesChartHandle } from "./TimeSeriesChart";
import { UtilizationChart } from "./UtilizationChart";

interface DashboardPaneProps {
  meta: SiteMeta;
  activeSource: SourceFilter;
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

  const activeSourcesMeta = useMemo(() => {
    const scopeSource = selection.kind === "overview" ? activeSource : selection.source;
    if (scopeSource === null) return meta.sources;
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

  const title = useMemo(() => {
    if (selection.kind === "field") return selectedField?.field_name ?? selection.name ?? selection.id;
    if (selection.kind === "facility")
      return selectedFacility?.facility_name ?? selection.name ?? selection.id;
    if (selection.kind === "well") return selection.name ?? selection.id;
    if (selection.kind === "basin") return `${selection.basin} Basin`;
    if (activeSource === null) return "All Sources Overview";
    const s = meta.sources.find((item) => item.id === activeSource);
    return s ? `${s.country} (${s.publisher})` : activeSource;
  }, [selection, selectedField, selectedFacility, activeSource, meta.sources]);

  const exportSlug =
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "ogprod";

  const handleExportCsv = () => {
    const csv = buildSeriesCsv(series, visibleProducts, unitSystem, {
      includeUtilization: selection.kind === "facility",
    });
    downloadCsv(`${exportSlug}-production.csv`, csv);
  };

  const handleExportPng = () => {
    chartRef.current?.exportPng(`${exportSlug}-chart.png`);
  };

  return (
    <aside className="flex h-full flex-col overflow-y-auto bg-[#f8f9fb]">
      <div className="flex flex-col gap-4 p-4 sm:p-5">
        <DashboardHeader
          title={title}
          selection={selection}
          selectedField={selectedField}
          selectedFacility={selectedFacility}
          activeSourcesMeta={activeSourcesMeta}
          wellPartitionsQueried={wellPartitionsQueried}
          canExport={series.length > 0}
          onBackToOverview={() => onSelectEntity({ kind: "overview" })}
          onExportPng={handleExportPng}
          onExportCsv={handleExportCsv}
        />

        <KpiStrip
          selection={selection}
          selectedField={selectedField}
          selectedFacility={selectedFacility}
          firstRow={firstRow}
          latestRow={latestRow}
          seriesLength={series.length}
          unitSystem={unitSystem}
        />

        {applicableGaps.length > 0 && <KnownGapsBanner gaps={applicableGaps} />}

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

        {selection.kind === "facility" &&
          selectedFacility?.oil_capacity_bpd &&
          series.some((r) => r.oil_util !== undefined) && (
            <FacilityUtilizationCard facility={selectedFacility} rows={series} />
          )}

        {selection.kind === "overview" && (
          <OverviewLeaderboards
            activeSource={activeSource}
            unitSystem={unitSystem}
            fieldsSummary={fieldsSummary}
            facilitiesSummary={facilitiesSummary}
            basinMonthly={basinMonthly}
            onSelectEntity={onSelectEntity}
          />
        )}

        <DashboardFooter meta={meta} activeSourcesMeta={activeSourcesMeta} />
      </div>
    </aside>
  );
}

function KnownGapsBanner({ gaps }: { gaps: KnownGap[] }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/90 px-3 py-2 text-xs text-amber-900">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" />
      <div>
        {gaps.map((g) => (
          <p key={`${g.column}-${g.start}`}>
            <span className="font-semibold">
              Known reporting gap ({g.start.slice(0, 7)} to {g.end.slice(0, 7)}):
            </span>{" "}
            {g.description}
          </p>
        ))}
      </div>
    </div>
  );
}

function FacilityUtilizationCard({
  facility,
  rows,
}: {
  facility: FacilitySummary;
  rows: MonthlyPoint[];
}) {
  return (
    <div className="rounded-lg border border-slate-200/90 bg-white p-3.5">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
          Oil Capacity Utilization (Actual / Nameplate)
        </h3>
        <span className="font-tabular text-xs font-medium text-cyan-800">
          Latest: {formatPercent(facility.oil_utilization)}
        </span>
      </div>
      <UtilizationChart rows={rows} />
    </div>
  );
}

function DashboardFooter({
  meta,
  activeSourcesMeta,
}: {
  meta: SiteMeta;
  activeSourcesMeta: SourceMeta[];
}) {
  return (
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
  );
}
