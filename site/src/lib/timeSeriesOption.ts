import * as echarts from "echarts";
import type { KnownGap, MonthlyPoint, RateColumn, UnitSystem } from "../types/data";
import { convertRate, PRODUCT_SPECS, UNIT_SYSTEMS, unitLabelFor } from "./units";

const SANS_FONT = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const MONO_FONT = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace";

export const LEGEND_TOP_PX = 2;
export const Y_AXIS_NAME_GAP_PX = 14;
/** Vertical clearance so the top legend row and y-axis unit labels never collide. */
export const GRID_TOP_PX = 58;

export interface BuildTimeSeriesOptionParams {
  rows: readonly MonthlyPoint[];
  activeProducts: readonly RateColumn[];
  unitSystem: UnitSystem;
  oilCapacityBpd?: number | null;
  knownGaps?: readonly KnownGap[];
}

function formatAxisTick(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}k`;
  return String(v);
}

function buildGapMarkAreas(
  periods: readonly string[],
  gaps: readonly KnownGap[],
): [{ name: string; xAxis: string }, { xAxis: string }][] {
  const areas: [{ name: string; xAxis: string }, { xAxis: string }][] = [];
  for (const g of gaps) {
    const startYm = g.start.slice(0, 7);
    const endYm = g.end.slice(0, 7);
    let first: string | null = null;
    let last: string | null = null;
    for (const p of periods) {
      if (p >= startYm && p <= endYm) {
        if (first === null) first = p;
        last = p;
      }
    }
    if (first !== null && last !== null) {
      areas.push([{ name: "Reporting gap", xAxis: first }, { xAxis: last }]);
    }
  }
  return areas;
}

export function buildTimeSeriesOption({
  rows,
  activeProducts,
  unitSystem,
  oilCapacityBpd,
  knownGaps = [],
}: BuildTimeSeriesOptionParams): echarts.EChartsOption {
  const unitMeta = UNIT_SYSTEMS.find((u) => u.id === unitSystem) ?? UNIT_SYSTEMS[0];
  const periods = rows.map((r) => r.p.slice(0, 7));

  const hasLiquids = activeProducts.some((c) => PRODUCT_SPECS[c].phase === "liquid");
  const hasGases = activeProducts.some((c) => PRODUCT_SPECS[c].phase === "gas");

  const convertedCap =
    oilCapacityBpd && oilCapacityBpd > 0
      ? convertRate(oilCapacityBpd, "oil_prod", unitSystem)
      : null;

  const series: echarts.SeriesOption[] = activeProducts.map((col) => {
    const spec = PRODUCT_SPECS[col];
    const isGas = spec.phase === "gas";
    const yAxisIndex = isGas && hasLiquids ? 1 : 0;
    const data = rows.map((r) => {
      const val = convertRate(r[col], col, unitSystem);
      return val === null ? null : Number(val.toFixed(2));
    });

    const baseSeries: echarts.LineSeriesOption = {
      name: `${spec.shortLabel} (${unitLabelFor(col, unitSystem)})`,
      type: "line",
      yAxisIndex,
      showSymbol: rows.length <= 24,
      symbolSize: 4,
      smooth: false,
      connectNulls: false,
      lineStyle: {
        width: col === "oil_prod" || col === "gas_prod" ? 2 : 1.5,
        type: spec.category === "injection" ? "dashed" : "solid",
      },
      itemStyle: { color: spec.color },
      areaStyle:
        activeProducts.length <= 2 && col === "oil_prod"
          ? {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: "rgba(194, 109, 10, 0.18)" },
                { offset: 1, color: "rgba(194, 109, 10, 0.01)" },
              ]),
            }
          : undefined,
      data,
    };

    if (col === "oil_prod" && convertedCap !== null) {
      baseSeries.markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { color: "#dc2626", type: "dashed", width: 1.5 },
        label: {
          formatter: `Oil Capacity: ${Math.round(convertedCap).toLocaleString()} ${unitMeta.liquidUnit}`,
          position: "insideEndTop",
          color: "#b91c1c",
          fontSize: 10,
        },
        data: [{ yAxis: convertedCap }],
      };
    }

    const colGaps = knownGaps.filter((g) => g.column === col);
    if (colGaps.length > 0) {
      const gapAreas = buildGapMarkAreas(periods, colGaps);
      if (gapAreas.length > 0) {
        baseSeries.markArea = {
          silent: true,
          itemStyle: {
            color: "rgba(245, 158, 11, 0.14)",
            borderColor: "rgba(217, 119, 6, 0.35)",
            borderWidth: 1,
            borderType: "dashed",
          },
          label: {
            show: true,
            position: "insideTop",
            color: "#92400e",
            fontSize: 10,
            fontFamily: SANS_FONT,
          },
          data: gapAreas,
        };
      }
    }
    return baseSeries;
  });

  const yAxis: echarts.YAXisComponentOption[] = [];
  if (hasLiquids) {
    yAxis.push({
      type: "value",
      name: unitMeta.liquidUnit,
      nameLocation: "end",
      nameGap: Y_AXIS_NAME_GAP_PX,
      nameTextStyle: {
        color: "#64748b",
        fontSize: 11,
        fontWeight: 500,
        fontFamily: SANS_FONT,
        align: "left",
        padding: [0, 0, 0, 0],
      },
      axisLabel: {
        color: "#64748b",
        fontSize: 11,
        fontFamily: MONO_FONT,
        formatter: formatAxisTick,
      },
      splitLine: { lineStyle: { color: "#f1f5f9" } },
    });
  }
  if (hasGases) {
    const isRightAxis = hasLiquids;
    yAxis.push({
      type: "value",
      name: unitMeta.gasUnit,
      nameLocation: "end",
      nameGap: Y_AXIS_NAME_GAP_PX,
      nameTextStyle: {
        color: "#0284c7",
        fontSize: 11,
        fontWeight: 500,
        fontFamily: SANS_FONT,
        align: isRightAxis ? "right" : "left",
        padding: [0, 0, 0, 0],
      },
      axisLabel: {
        color: "#0284c7",
        fontSize: 11,
        fontFamily: MONO_FONT,
        formatter: formatAxisTick,
      },
      splitLine: { show: !hasLiquids, lineStyle: { color: "#f1f5f9" } },
    });
  }
  if (yAxis.length === 0) {
    yAxis.push({ type: "value" });
  }

  return {
    animationDuration: 260,
    grid: {
      left: 56,
      right: hasLiquids && hasGases ? 58 : 20,
      top: GRID_TOP_PX,
      bottom: rows.length > 18 ? 58 : 30,
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255,255,255,0.97)",
      borderColor: "#cbd5e1",
      textStyle: { color: "#0f172a", fontSize: 12, fontFamily: SANS_FONT },
    },
    legend: {
      type: "scroll",
      top: LEGEND_TOP_PX,
      left: "center",
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 14,
      pageIconSize: 10,
      pageIconColor: "#475569",
      pageIconInactiveColor: "#cbd5e1",
      pageTextStyle: { color: "#64748b", fontSize: 10, fontFamily: SANS_FONT },
      textStyle: { color: "#334155", fontSize: 11, fontFamily: SANS_FONT },
    },
    xAxis: {
      type: "category",
      data: periods,
      boundaryGap: false,
      axisLine: { lineStyle: { color: "#cbd5e1" } },
      axisLabel: { color: "#64748b", fontSize: 11, fontFamily: SANS_FONT },
    },
    yAxis,
    dataZoom:
      rows.length > 18
        ? [
            {
              type: "slider",
              height: 18,
              bottom: 6,
              borderColor: "#e2e8f0",
              fillerColor: "rgba(14, 116, 144, 0.14)",
              handleStyle: { color: "#0e7490" },
            },
            { type: "inside" },
          ]
        : [],
    series,
  };
}
