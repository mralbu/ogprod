import { useMemo, useState } from "react";
import { convertRate, formatCompactNumber, formatPercent, unitLabelFor } from "../lib/units";
import type {
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  MonthlyPoint,
  SourceFilter,
  UnitSystem,
} from "../types/data";

type OverviewTab = "fields" | "facilities" | "basins";

interface OverviewLeaderboardsProps {
  activeSource: SourceFilter;
  unitSystem: UnitSystem;
  fieldsSummary: FieldSummary[];
  facilitiesSummary: FacilitySummary[];
  basinMonthly: Record<string, Record<string, MonthlyPoint[]>>;
  onSelectEntity: (sel: EntitySelection) => void;
}

export function OverviewLeaderboards({
  activeSource,
  unitSystem,
  fieldsSummary,
  facilitiesSummary,
  basinMonthly,
  onSelectEntity,
}: OverviewLeaderboardsProps) {
  const [tab, setTab] = useState<OverviewTab>("fields");

  const filteredFields = useMemo(
    () =>
      fieldsSummary
        .filter((f) => activeSource === null || f.source === activeSource)
        .slice(0, 25),
    [fieldsSummary, activeSource],
  );

  const filteredFacilities = useMemo(
    () =>
      facilitiesSummary
        .filter((f) => activeSource === null || f.source === activeSource)
        .slice(0, 25),
    [facilitiesSummary, activeSource],
  );

  const basinRows = useMemo(() => {
    const list: { source: string; basin: string; latestOil: number; latestGas: number }[] = [];
    for (const [src, basins] of Object.entries(basinMonthly)) {
      if (activeSource !== null && src !== activeSource) continue;
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

  return (
    <div className="rounded-lg border border-slate-200/90 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-3.5 pt-2">
        <div className="flex gap-4">
          {(
            [
              ["fields", "Top Fields"],
              ["facilities", "FPSOs & Platforms"],
              ["basins", "Basins & Areas"],
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
                <th className="px-2 py-2 font-medium">Basin / Area</th>
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
                  <td className="px-2 py-2 text-slate-500">{f.basin ?? f.region ?? "—"}</td>
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
                    {formatCompactNumber(convertRate(fac.latest_oil_bpd, "oil_prod", unitSystem))}
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
                <th className="px-3.5 py-2 font-medium">Basin / Area</th>
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
  );
}
