import type { ChartSeriesKey, MonthlyPoint, UnitSystem } from "../types/data";
import type { SourceComparisonTable } from "./timeSeriesOption";
import { computeSeriesValue, getSeriesSpec, unitLabelFor } from "./units";

function escapeCsvCell(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replaceAll('"', '""')}"`;
  }
  return value;
}

export function buildSeriesCsv(
  rows: readonly MonthlyPoint[],
  columns: readonly ChartSeriesKey[],
  unitSystem: UnitSystem,
  options?: { includeUtilization?: boolean },
): string {
  const headers = [
    "period",
    ...columns.map((c) => `${getSeriesSpec(c).shortLabel} (${unitLabelFor(c, unitSystem)})`),
    ...(options?.includeUtilization ? ["Oil Utilization (%)"] : []),
  ];
  const lines = [headers.map(escapeCsvCell).join(",")];
  for (const row of rows) {
    const cells: string[] = [row.p];
    for (const col of columns) {
      const value = computeSeriesValue(row, col, unitSystem);
      cells.push(value === null ? "" : value.toFixed(2));
    }
    if (options?.includeUtilization) {
      cells.push(row.oil_util === undefined ? "" : (row.oil_util * 100).toFixed(2));
    }
    lines.push(cells.map(escapeCsvCell).join(","));
  }
  return `${lines.join("\n")}\n`;
}

export function buildComparisonCsv(table: SourceComparisonTable): string {
  const headers = [
    "period",
    ...table.series.map((s) => `${s.label} (${table.unitLabel})`),
  ];
  const lines = [headers.map(escapeCsvCell).join(",")];
  for (let i = 0; i < table.periods.length; i += 1) {
    const cells: string[] = [table.periods[i]];
    for (const s of table.series) {
      const val = s.values[i];
      cells.push(val === null || val === undefined ? "" : val.toFixed(table.decimals));
    }
    lines.push(cells.map(escapeCsvCell).join(","));
  }
  return `${lines.join("\n")}\n`;
}

export function downloadCsv(filename: string, csvContent: string): void {
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
