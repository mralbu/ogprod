import * as echarts from "echarts";
import type {
  ChartSeriesKey,
  KnownGap,
  MonthlyPoint,
  SourceMeta,
  UnitSystem,
} from "../types/data";
import { isDerivedSeries } from "../types/data";
import { formatCountryName, formatSourceDisplayName } from "./sourceLabels";
import {
  type AxisGroup,
  computeSeriesValue,
  convertRate,
  DERIVED_SPECS,
  getSeriesSpec,
  UNIT_SYSTEMS,
  unitLabelFor,
} from "./units";

const SANS_FONT = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const MONO_FONT = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace";

export const LEGEND_TOP_PX = 2;
export const Y_AXIS_NAME_GAP_PX = 14;
/** Vertical clearance so the top legend row and y-axis unit labels never collide. */
export const GRID_TOP_PX = 58;
/** Horizontal spacing between additional offset right y-axes when 3+ unit groups are active. */
export const RIGHT_AXIS_OFFSET_PX = 60;

const AXIS_GROUP_ORDER: readonly AxisGroup[] = ["liquid", "gas", "gor", "water_cut"];

export interface BuildTimeSeriesOptionParams {
  rows: readonly MonthlyPoint[];
  activeProducts: readonly ChartSeriesKey[];
  unitSystem: UnitSystem;
  oilCapacityBpd?: number | null;
  knownGaps?: readonly KnownGap[];
}

function formatAxisTick(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) {
    if (v % 1_000 === 0 || v >= 10_000) return `${(v / 1_000).toFixed(0)}k`;
    return `${(v / 1_000).toFixed(1)}k`;
  }
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

function mapGapsToSeries(
  activeProducts: readonly ChartSeriesKey[],
  knownGaps: readonly KnownGap[],
): Map<ChartSeriesKey, KnownGap[]> {
  const activeSet = new Set<ChartSeriesKey>(activeProducts);
  const bySeries = new Map<ChartSeriesKey, KnownGap[]>();
  for (const gap of knownGaps) {
    let target: ChartSeriesKey | undefined;
    if (activeSet.has(gap.column)) {
      target = gap.column;
    } else {
      target = activeProducts.find(
        (key) => isDerivedSeries(key) && DERIVED_SPECS[key].dependsOn.includes(gap.column),
      );
    }
    if (!target) continue;
    const list = bySeries.get(target);
    if (list) {
      list.push(gap);
    } else {
      bySeries.set(target, [gap]);
    }
  }
  return bySeries;
}

function buildYAxisOption(
  group: AxisGroup,
  axisIdx: number,
  totalAxes: number,
  unitMeta: (typeof UNIT_SYSTEMS)[number],
): echarts.YAXisComponentOption {
  const isRightAxis = axisIdx > 0;
  const rightOffset = axisIdx > 1 ? (axisIdx - 1) * RIGHT_AXIS_OFFSET_PX : 0;

  let name: string;
  let color: string;
  if (group === "liquid") {
    name = unitMeta.liquidUnit;
    color = "#64748b";
  } else if (group === "gas") {
    name = unitMeta.gasUnit;
    color = "#0284c7";
  } else if (group === "gor") {
    name = unitMeta.gorUnit;
    color = DERIVED_SPECS.gor.color;
  } else {
    name = "%";
    color = DERIVED_SPECS.water_cut.color;
  }

  const align = !isRightAxis ? "left" : rightOffset === 0 ? "right" : "left";

  const axis: echarts.YAXisComponentOption = {
    type: "value",
    position: isRightAxis ? "right" : "left",
    ...(rightOffset > 0 ? { offset: rightOffset } : {}),
    ...(group === "water_cut" ? { min: 0, max: 100 } : {}),
    name,
    nameLocation: "end",
    nameGap: Y_AXIS_NAME_GAP_PX,
    nameTextStyle: {
      color,
      fontSize: 11,
      fontWeight: 500,
      fontFamily: SANS_FONT,
      align,
      padding: [0, 0, 0, 0],
    },
    ...(totalAxes >= 3 && isRightAxis
      ? { axisLine: { show: true, lineStyle: { color } } }
      : {}),
    axisLabel: {
      color,
      fontSize: 11,
      fontFamily: MONO_FONT,
      formatter: group === "water_cut" ? (v: number) => `${v}%` : formatAxisTick,
    },
    splitLine: { show: axisIdx === 0, lineStyle: { color: "#f1f5f9" } },
  };
  return axis;
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

  const presentGroups = new Set<AxisGroup>(activeProducts.map((c) => getSeriesSpec(c).axisGroup));
  const activeGroups = AXIS_GROUP_ORDER.filter((g) => presentGroups.has(g));
  const groupToAxisIndex = new Map<AxisGroup, number>(activeGroups.map((g, i) => [g, i]));

  const convertedCap =
    oilCapacityBpd && oilCapacityBpd > 0
      ? convertRate(oilCapacityBpd, "oil_prod", unitSystem)
      : null;

  const gapsBySeries = mapGapsToSeries(activeProducts, knownGaps);

  const series: echarts.SeriesOption[] = activeProducts.map((col) => {
    const spec = getSeriesSpec(col);
    const yAxisIndex = groupToAxisIndex.get(spec.axisGroup) ?? 0;
    const decimals = col === "water_cut" ? 1 : 2;
    const data = rows.map((r) => {
      const val = computeSeriesValue(r, col, unitSystem);
      return val === null ? null : Number(val.toFixed(decimals));
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
        width:
          col === "oil_prod" || col === "gas_prod"
            ? 2
            : spec.category === "ratio"
              ? 1.75
              : 1.5,
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

    const colGaps = gapsBySeries.get(col);
    if (colGaps && colGaps.length > 0) {
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

  const yAxis: echarts.YAXisComponentOption[] = activeGroups.map((group, idx) =>
    buildYAxisOption(group, idx, activeGroups.length, unitMeta),
  );
  if (yAxis.length === 0) {
    yAxis.push({ type: "value" });
  }

  const rightAxisCount = Math.max(0, activeGroups.length - 1);
  const gridRight = rightAxisCount === 0 ? 20 : 58 + (rightAxisCount - 1) * RIGHT_AXIS_OFFSET_PX;

  return {
    animationDuration: 260,
    grid: {
      left: 56,
      right: gridRight,
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

/** Max individual lines on the All Sources comparison chart before bucketing tail into "Other". */
export const MAX_COMPARISON_SERIES = 8;

export const SOURCE_SERIES_COLORS: readonly string[] = [
  "#c26d0a",
  "#0284c7",
  "#0d9488",
  "#7c3aed",
  "#dc2626",
  "#2563eb",
  "#d97706",
  "#475569",
];

export interface SourceComparisonSeries {
  id: string;
  label: string;
  color: string;
  values: (number | null)[];
  latestValue: number;
}

export interface SourceComparisonTable {
  periods: string[];
  product: ChartSeriesKey;
  unitLabel: string;
  decimals: number;
  series: SourceComparisonSeries[];
}

export interface BuildSourceComparisonParams {
  countryMonthly: Record<string, MonthlyPoint[]>;
  sources: readonly SourceMeta[];
  product: ChartSeriesKey;
  unitSystem: UnitSystem;
  maxSeries?: number;
}

interface RawSourceTrack {
  id: string;
  label: string;
  byPeriod: Map<string, number>;
  latestValue: number;
}

function roundTo(value: number, decimals: number): number {
  return Number(value.toFixed(decimals));
}

function resolveComparisonLabel(sourceId: string, sources: readonly SourceMeta[]): string {
  const match = sources.find((s) => s.id === sourceId);
  if (match) {
    return formatSourceDisplayName(match, sources);
  }
  return formatCountryName(sourceId, sources);
}

/**
 * Align per-source monthly series onto a shared sorted period axis for a single metric,
 * preserving each source's own start and end months (null outside) so lagging regulators
 * never create a synthetic drop-off or fabricated carry-forward values.
 */
export function buildSourceComparisonTable({
  countryMonthly,
  sources,
  product,
  unitSystem,
  maxSeries = MAX_COMPARISON_SERIES,
}: BuildSourceComparisonParams): SourceComparisonTable {
  const decimals = product === "water_cut" ? 1 : 2;
  const unitLabel = unitLabelFor(product, unitSystem);
  const periodSet = new Set<string>();
  const tracks: RawSourceTrack[] = [];

  for (const [sourceId, points] of Object.entries(countryMonthly)) {
    const byPeriod = new Map<string, number>();
    let latestPeriod = "";
    let latestValue = 0;

    for (const pt of points) {
      const val = computeSeriesValue(pt, product, unitSystem);
      if (val === null) continue;
      byPeriod.set(pt.p, val);
      periodSet.add(pt.p);
      if (pt.p >= latestPeriod) {
        latestPeriod = pt.p;
        latestValue = val;
      }
    }

    if (byPeriod.size > 0) {
      tracks.push({
        id: sourceId,
        label: resolveComparisonLabel(sourceId, sources),
        byPeriod,
        latestValue,
      });
    }
  }

  tracks.sort((a, b) => b.latestValue - a.latestValue || a.id.localeCompare(b.id));
  const periods = Array.from(periodSet).sort((a, b) => a.localeCompare(b));

  const limit = Math.max(1, maxSeries);
  const canAggregateOther = !isDerivedSeries(product) && tracks.length > limit;
  const directTracks = canAggregateOther ? tracks.slice(0, limit - 1) : tracks.slice(0, limit);
  const tailTracks = canAggregateOther ? tracks.slice(limit - 1) : [];

  const series: SourceComparisonSeries[] = directTracks.map((track, idx) => ({
    id: track.id,
    label: track.label,
    color: SOURCE_SERIES_COLORS[idx % SOURCE_SERIES_COLORS.length],
    latestValue: roundTo(track.latestValue, decimals),
    values: periods.map((p) => {
      const val = track.byPeriod.get(p);
      return val === undefined ? null : roundTo(val, decimals);
    }),
  }));

  if (tailTracks.length > 0) {
    let otherLatest = 0;
    const otherValues = periods.map((p) => {
      let sum = 0;
      let present = false;
      for (const track of tailTracks) {
        const val = track.byPeriod.get(p);
        if (val !== undefined) {
          sum += val;
          present = true;
        }
      }
      if (!present) return null;
      otherLatest = sum;
      return roundTo(sum, decimals);
    });
    series.push({
      id: "__other__",
      label: `Other (${tailTracks.length})`,
      color: SOURCE_SERIES_COLORS[SOURCE_SERIES_COLORS.length - 1],
      latestValue: roundTo(otherLatest, decimals),
      values: otherValues,
    });
  }

  return { periods, product, unitLabel, decimals, series };
}

export function buildSourceComparisonOption(
  table: SourceComparisonTable,
  unitSystem: UnitSystem,
): echarts.EChartsOption {
  const unitMeta = UNIT_SYSTEMS.find((u) => u.id === unitSystem) ?? UNIT_SYSTEMS[0];
  const spec = getSeriesSpec(table.product);
  const periods = table.periods.map((p) => p.slice(0, 7));
  const yAxis = [buildYAxisOption(spec.axisGroup, 0, 1, unitMeta)];

  const series: echarts.LineSeriesOption[] = table.series.map((item) => ({
    name: item.label,
    type: "line",
    yAxisIndex: 0,
    showSymbol: periods.length <= 24,
    symbolSize: 4,
    smooth: false,
    connectNulls: false,
    lineStyle: {
      width: item.id === "__other__" ? 1.5 : 2,
      type: item.id === "__other__" ? "dashed" : "solid",
    },
    itemStyle: { color: item.color },
    data: item.values,
  }));

  return {
    animationDuration: 260,
    grid: {
      left: 56,
      right: 20,
      top: GRID_TOP_PX,
      bottom: periods.length > 18 ? 58 : 30,
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
      periods.length > 18
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
