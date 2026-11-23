import type { MonthlyPoint, RateColumn, UnitSystem } from "../types/data";
import { convertRate, PRODUCT_SPECS, unitLabelFor } from "./units";

function escapeCsvCell(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replaceAll('"', '""')}"`;
  }
  return value;
}

export function buildSeriesCsv(
  rows: readonly MonthlyPoint[],
  columns: readonly RateColumn[],
  unitSystem: UnitSystem,
  options?: { includeUtilization?: boolean },
): string {
  const headers = [
    "period",
    ...columns.map((c) => `${PRODUCT_SPECS[c].shortLabel} (${unitLabelFor(c, unitSystem)})`),
    ...(options?.includeUtilization ? ["Oil Utilization (%)"] : []),
  ];
  const lines = [headers.map(escapeCsvCell).join(",")];
  for (const row of rows) {
    const cells: string[] = [row.p];
    for (const col of columns) {
      const converted = convertRate(row[col], col, unitSystem);
      cells.push(converted === null ? "" : converted.toFixed(2));
    }
    if (options?.includeUtilization) {
      cells.push(row.oil_util === undefined ? "" : (row.oil_util * 100).toFixed(2));
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
