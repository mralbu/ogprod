import { ArrowLeft, Database, Download, ImageDown } from "lucide-react";
import { formatCountryName, formatOverviewSubtitle } from "../lib/sourceLabels";
import type {
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  SourceMeta,
} from "../types/data";

interface DashboardHeaderProps {
  title: string;
  selection: EntitySelection;
  selectedField: FieldSummary | null;
  selectedFacility: FacilitySummary | null;
  activeSourcesMeta: SourceMeta[];
  wellPartitionsQueried: number | null;
  canExport: boolean;
  onBackToOverview: () => void;
  onExportPng: () => void;
  onExportCsv: () => void;
}

export function DashboardHeader({
  title,
  selection,
  selectedField,
  selectedFacility,
  activeSourcesMeta,
  wellPartitionsQueried,
  canExport,
  onBackToOverview,
  onExportPng,
  onExportCsv,
}: DashboardHeaderProps) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        {selection.kind !== "overview" && (
          <button
            type="button"
            onClick={onBackToOverview}
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
              {selection.kind === "basin" ? "basin / area" : selection.kind}
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
              {formatCountryName(selectedField.source, activeSourcesMeta)}
              {selectedField.basin ?? selectedField.region
                ? ` · ${selectedField.basin ?? selectedField.region}`
                : ""}
              {selectedField.operator ? ` · ${selectedField.operator}` : ""}
              {selectedField.water_depth_m ? ` · ${selectedField.water_depth_m} m depth` : ""}
            </>
          )}
          {selection.kind === "facility" && selectedFacility && (
            <>
              {selectedFacility.facility_type ?? "Facility"} ·{" "}
              {formatCountryName(selectedFacility.source, activeSourcesMeta)}
              {selectedFacility.fields ? ` · Fields: ${selectedFacility.fields}` : ""}
              {selectedFacility.operator ? ` · ${selectedFacility.operator}` : ""}
            </>
          )}
          {selection.kind === "well" && (
            <>
              {formatCountryName(selection.source, activeSourcesMeta)}
              {selection.fieldName ? ` · Field: ${selection.fieldName}` : ""}
              {selection.status ? ` · ${selection.status}` : ""}
              {selection.liftMethod ? ` · ${selection.liftMethod}` : ""}
            </>
          )}
          {selection.kind === "overview" && <>{formatOverviewSubtitle(activeSourcesMeta)}</>}
        </p>
      </div>

      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={onExportPng}
          disabled={!canExport}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
        >
          <ImageDown className="h-3.5 w-3.5" />
          PNG
        </button>
        <button
          type="button"
          onClick={onExportCsv}
          disabled={!canExport}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
        >
          <Download className="h-3.5 w-3.5" />
          CSV
        </button>
      </div>
    </div>
  );
}
