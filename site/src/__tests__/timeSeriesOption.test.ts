import type * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import {
  buildTimeSeriesOption,
  GRID_TOP_PX,
  LEGEND_TOP_PX,
  Y_AXIS_NAME_GAP_PX,
} from "../lib/timeSeriesOption";
import type { KnownGap, MonthlyPoint } from "../types/data";

const SAMPLE_ROWS: MonthlyPoint[] = [
  { p: "2025-01-01", oil_prod: 6_400_000, gas_prod: 520_700 },
  { p: "2025-02-01", oil_prod: 6_500_000, gas_prod: 530_000 },
];

const BRAZIL_GAS_GAP: KnownGap = {
  column: "gas_prod",
  start: "2003-07-01",
  end: "2005-06-01",
  description: "incomplete ANP gas reporting",
  resolved_in_country_rollup: true,
};

describe("buildTimeSeriesOption", () => {
  it("separates the centered scroll legend from vertical axis unit names", () => {
    const option = buildTimeSeriesOption({
      rows: SAMPLE_ROWS,
      activeProducts: ["oil_prod", "gas_prod"],
      unitSystem: "oilfield_e3m3",
    });

    const legend = option.legend as echarts.LegendComponentOption;
    const grid = option.grid as echarts.GridComponentOption;
    const yAxes = option.yAxis as echarts.YAXisComponentOption[];

    expect(legend.type).toBe("scroll");
    expect(legend.left).toBe("center");
    expect(legend.top).toBe(LEGEND_TOP_PX);

    // Ensure vertical clearance between top legend row (~20px) and y-axis name baseline (grid.top - nameGap).
    const gridTop = Number(grid.top);
    expect(gridTop).toBe(GRID_TOP_PX);
    expect(gridTop - Y_AXIS_NAME_GAP_PX - LEGEND_TOP_PX).toBeGreaterThanOrEqual(40);

    expect(yAxes).toHaveLength(2);
    expect(yAxes[0].name).toBe("bbl/d");
    expect(yAxes[0].nameGap).toBe(Y_AXIS_NAME_GAP_PX);
    expect(yAxes[0].nameTextStyle?.align).toBe("left");

    expect(yAxes[1].name).toBe("10³ m³/d");
    expect(yAxes[1].nameGap).toBe(Y_AXIS_NAME_GAP_PX);
    expect(yAxes[1].nameTextStyle?.align).toBe("right");
  });

  it("uses 10³ m³/d in series legend names and left-aligns gas axis when alone", () => {
    const option = buildTimeSeriesOption({
      rows: SAMPLE_ROWS,
      activeProducts: ["gas_prod"],
      unitSystem: "oilfield_e3m3",
    });

    const yAxes = option.yAxis as echarts.YAXisComponentOption[];
    const series = option.series as echarts.LineSeriesOption[];

    expect(series[0].name).toBe("Gas (10³ m³/d)");
    expect(yAxes).toHaveLength(1);
    expect(yAxes[0].name).toBe("10³ m³/d");
    expect(yAxes[0].nameTextStyle?.align).toBe("left");
  });

  it("shades overlapping known reporting gaps clamped to displayed category periods", () => {
    const partialOverlapRows: MonthlyPoint[] = [
      { p: "2004-02-01", oil_prod: 10_000 },
      { p: "2005-06-01", oil_prod: 11_000 },
      { p: "2005-07-01", oil_prod: 12_000, gas_prod: 400 },
    ];
    const option = buildTimeSeriesOption({
      rows: partialOverlapRows,
      activeProducts: ["oil_prod", "gas_prod"],
      unitSystem: "oilfield_e3m3",
      knownGaps: [BRAZIL_GAS_GAP],
    });

    const series = option.series as echarts.LineSeriesOption[];
    expect(series[0].markArea).toBeUndefined();
    expect(series[1].markArea?.data).toEqual([
      [{ name: "Reporting gap", xAxis: "2004-02" }, { xAxis: "2005-06" }],
    ]);
  });

  it("omits markArea when series does not overlap known gaps", () => {
    const option = buildTimeSeriesOption({
      rows: SAMPLE_ROWS,
      activeProducts: ["oil_prod", "gas_prod"],
      unitSystem: "oilfield_e3m3",
      knownGaps: [BRAZIL_GAS_GAP],
    });

    const series = option.series as echarts.LineSeriesOption[];
    expect(series[0].markArea).toBeUndefined();
    expect(series[1].markArea).toBeUndefined();
  });
});
