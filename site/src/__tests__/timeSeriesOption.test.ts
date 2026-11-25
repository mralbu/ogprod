import type * as echarts from "echarts";
import { describe, expect, it } from "vitest";
import {
  buildTimeSeriesOption,
  GRID_TOP_PX,
  LEGEND_TOP_PX,
  RIGHT_AXIS_OFFSET_PX,
  Y_AXIS_NAME_GAP_PX,
} from "../lib/timeSeriesOption";
import type { KnownGap, MonthlyPoint } from "../types/data";

const SAMPLE_ROWS: MonthlyPoint[] = [
  { p: "2025-01-01", oil_prod: 6_400_000, gas_prod: 520_700, water_prod: 1_600_000 },
  { p: "2025-02-01", oil_prod: 6_500_000, gas_prod: 530_000, water_prod: 3_500_000 },
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

  it("overlays Oil and Water Cut on dual y-axes with 0-100% scale", () => {
    const option = buildTimeSeriesOption({
      rows: SAMPLE_ROWS,
      activeProducts: ["oil_prod", "water_cut"],
      unitSystem: "oilfield_e3m3",
    });

    const yAxes = option.yAxis as echarts.YAXisComponentOption[];
    const series = option.series as echarts.LineSeriesOption[];
    const grid = option.grid as echarts.GridComponentOption;

    expect(yAxes).toHaveLength(2);
    expect(yAxes[0].name).toBe("bbl/d");
    expect(yAxes[1].name).toBe("%");
    expect(yAxes[1].min).toBe(0);
    expect(yAxes[1].max).toBe(100);
    expect(grid.right).toBe(58);

    expect(series[0].yAxisIndex).toBe(0);
    expect(series[1].name).toBe("WCUT (%)");
    expect(series[1].yAxisIndex).toBe(1);
    expect(series[1].data).toEqual([20, 35]);
  });

  it("uses m³/m³ or scf/bbl for GOR depending on unit system", () => {
    const rows: MonthlyPoint[] = [{ p: "2025-01-01", oil_prod: 10_000, gas_prod: 300 }];

    const metricOpt = buildTimeSeriesOption({
      rows,
      activeProducts: ["gor"],
      unitSystem: "oilfield_e3m3",
    });
    const metricAxes = metricOpt.yAxis as echarts.YAXisComponentOption[];
    const metricSeries = metricOpt.series as echarts.LineSeriesOption[];
    expect(metricAxes).toHaveLength(1);
    expect(metricAxes[0].name).toBe("m³/m³");
    expect(metricSeries[0].name).toBe("GOR (m³/m³)");
    expect(metricSeries[0].data).toEqual([188.69]);

    const mmcfOpt = buildTimeSeriesOption({
      rows,
      activeProducts: ["gor"],
      unitSystem: "oilfield_mmcf",
    });
    const mmcfAxes = mmcfOpt.yAxis as echarts.YAXisComponentOption[];
    const mmcfSeries = mmcfOpt.series as echarts.LineSeriesOption[];
    expect(mmcfAxes[0].name).toBe("scf/bbl");
    expect(mmcfSeries[0].name).toBe("GOR (scf/bbl)");
    expect(mmcfSeries[0].data).toEqual([1059.44]);
  });

  it("offsets extra right y-axes and expands grid.right when 3+ unit groups are active", () => {
    const option = buildTimeSeriesOption({
      rows: SAMPLE_ROWS,
      activeProducts: ["oil_prod", "gas_prod", "gor", "water_cut"],
      unitSystem: "oilfield_mmcf",
    });

    const yAxes = option.yAxis as echarts.YAXisComponentOption[];
    const series = option.series as echarts.LineSeriesOption[];
    const grid = option.grid as echarts.GridComponentOption;

    expect(yAxes).toHaveLength(4);
    expect(yAxes.map((a) => a.name)).toEqual(["bbl/d", "MMcf/d", "scf/bbl", "%"]);
    expect(yAxes[0].position).toBe("left");
    expect(yAxes[1].position).toBe("right");
    expect(yAxes[1].offset).toBeUndefined();
    expect(yAxes[2].position).toBe("right");
    expect(yAxes[2].offset).toBe(RIGHT_AXIS_OFFSET_PX);
    expect(yAxes[2].nameTextStyle?.align).toBe("left");
    expect(yAxes[3].position).toBe("right");
    expect(yAxes[3].offset).toBe(RIGHT_AXIS_OFFSET_PX * 2);
    expect(yAxes[3].nameTextStyle?.align).toBe("left");
    expect(grid.right).toBe(58 + RIGHT_AXIS_OFFSET_PX * 2);

    expect(series.map((s) => s.yAxisIndex)).toEqual([0, 1, 2, 3]);
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

  it("attaches gas reporting gap markArea to GOR when gas_prod is hidden, without duplicating", () => {
    const partialOverlapRows: MonthlyPoint[] = [
      { p: "2004-02-01", oil_prod: 10_000 },
      { p: "2005-06-01", oil_prod: 11_000 },
      { p: "2005-07-01", oil_prod: 12_000, gas_prod: 400 },
    ];

    const gorOnlyOpt = buildTimeSeriesOption({
      rows: partialOverlapRows,
      activeProducts: ["oil_prod", "gor"],
      unitSystem: "oilfield_e3m3",
      knownGaps: [BRAZIL_GAS_GAP],
    });
    const gorOnlySeries = gorOnlyOpt.series as echarts.LineSeriesOption[];
    expect(gorOnlySeries[0].markArea).toBeUndefined();
    expect(gorOnlySeries[1].markArea?.data).toEqual([
      [{ name: "Reporting gap", xAxis: "2004-02" }, { xAxis: "2005-06" }],
    ]);

    const bothOpt = buildTimeSeriesOption({
      rows: partialOverlapRows,
      activeProducts: ["oil_prod", "gas_prod", "gor"],
      unitSystem: "oilfield_e3m3",
      knownGaps: [BRAZIL_GAS_GAP],
    });
    const bothSeries = bothOpt.series as echarts.LineSeriesOption[];
    expect(bothSeries[1].markArea).toBeDefined();
    expect(bothSeries[2].markArea).toBeUndefined();
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
