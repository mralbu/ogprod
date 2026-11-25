import { convertRate, formatCompactNumber, formatPercent, unitLabelFor } from "../lib/units";
import type {
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  MonthlyPoint,
  UnitSystem,
} from "../types/data";

export interface AllSourcesKpiSummary {
  latestOilBpd: number | null;
  latestGasE3m3d: number | null;
  firstPeriod: string | null;
  lastPeriod: string | null;
  sourceCount: number;
}

interface KpiStripProps {
  selection: EntitySelection;
  selectedField: FieldSummary | null;
  selectedFacility: FacilitySummary | null;
  firstRow: MonthlyPoint | null;
  latestRow: MonthlyPoint | null;
  seriesLength: number;
  unitSystem: UnitSystem;
  allSourcesOverview?: AllSourcesKpiSummary | null;
}

export function KpiStrip({
  selection,
  selectedField,
  selectedFacility,
  firstRow,
  latestRow,
  seriesLength,
  unitSystem,
  allSourcesOverview,
}: KpiStripProps) {
  const rawOil = allSourcesOverview ? allSourcesOverview.latestOilBpd : latestRow?.oil_prod;
  const rawGas = allSourcesOverview ? allSourcesOverview.latestGasE3m3d : latestRow?.gas_prod;
  const latestOilConverted = convertRate(rawOil, "oil_prod", unitSystem);
  const latestGasConverted = convertRate(rawGas, "gas_prod", unitSystem);

  const rangeStart = allSourcesOverview ? allSourcesOverview.firstPeriod : firstRow?.p ?? null;
  const rangeEnd = allSourcesOverview ? allSourcesOverview.lastPeriod : latestRow?.p ?? null;

  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
      <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
        <div className="text-[11px] font-medium text-slate-500">Latest Oil</div>
        <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
          {formatCompactNumber(latestOilConverted)}
        </div>
        <div className="text-[10px] text-slate-400">
          {unitLabelFor("oil_prod", unitSystem)}
          {allSourcesOverview ? " · sum of latest" : ""}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
        <div className="text-[11px] font-medium text-slate-500">Latest Gas</div>
        <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
          {formatCompactNumber(latestGasConverted)}
        </div>
        <div className="text-[10px] text-slate-400">
          {unitLabelFor("gas_prod", unitSystem)}
          {allSourcesOverview ? " · sum of latest" : ""}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
        <div className="text-[11px] font-medium text-slate-500">
          {selection.kind === "facility" ? "Oil Utilization" : "Date Range"}
        </div>
        <div className="font-tabular mt-1 text-base font-semibold text-slate-900">
          {selection.kind === "facility"
            ? formatPercent(selectedFacility?.oil_utilization)
            : rangeStart && rangeEnd
              ? `${rangeStart.slice(0, 4)}–${rangeEnd.slice(0, 4)}`
              : "—"}
        </div>
        <div className="text-[10px] text-slate-400">
          {selection.kind === "facility"
            ? selectedFacility?.oil_capacity_bpd
              ? `Cap ${formatCompactNumber(convertRate(selectedFacility.oil_capacity_bpd, "oil_prod", unitSystem))} ${unitLabelFor("oil_prod", unitSystem)}`
              : "No capacity listed"
            : rangeEnd
              ? `Latest ${rangeEnd.slice(0, 7)}`
              : "No series"}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
        <div className="text-[11px] font-medium text-slate-500">
          {selection.kind === "field"
            ? "Cum. Oil"
            : selection.kind === "facility"
              ? "Water Depth"
              : allSourcesOverview
                ? "Sources"
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
              : allSourcesOverview
                ? allSourcesOverview.sourceCount.toLocaleString()
                : seriesLength.toLocaleString()}
        </div>
        <div className="text-[10px] text-slate-400">
          {selection.kind === "field"
            ? "cumulative crude"
            : selection.kind === "facility"
              ? selectedFacility?.start_year
                ? `Start ${selectedFacility.start_year}`
                : "offshore / onshore"
              : allSourcesOverview
                ? "active jurisdictions"
                : "monthly points"}
        </div>
      </div>
    </div>
  );
}
