import * as echarts from "echarts";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { convertRate, PRODUCT_SPECS, UNIT_SYSTEMS, unitLabelFor } from "../lib/units";
import type { MonthlyPoint, RateColumn, UnitSystem } from "../types/data";

export interface TimeSeriesChartHandle {
  exportPng: (filename: string) => void;
}

interface TimeSeriesChartProps {
  rows: MonthlyPoint[];
  activeProducts: RateColumn[];
  unitSystem: UnitSystem;
  oilCapacityBpd?: number | null;
  height?: number;
}

export const TimeSeriesChart = forwardRef<TimeSeriesChartHandle, TimeSeriesChartProps>(
  function TimeSeriesChart({ rows, activeProducts, unitSystem, oilCapacityBpd, height = 310 }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<echarts.ECharts | null>(null);

    useImperativeHandle(ref, () => ({
      exportPng(filename: string) {
        if (!chartRef.current) return;
        const dataUrl = chartRef.current.getDataURL({
          type: "png",
          pixelRatio: 2,
          backgroundColor: "#ffffff",
        });
        const a = document.createElement("a");
        a.href = dataUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      },
    }));

    useEffect(() => {
      if (!containerRef.current) return;
      const chart = echarts.init(containerRef.current);
      chartRef.current = chart;
      const observer = new ResizeObserver(() => chart.resize());
      observer.observe(containerRef.current);
      return () => {
        observer.disconnect();
        chart.dispose();
        chartRef.current = null;
      };
    }, []);

    useEffect(() => {
      const chart = chartRef.current;
      if (!chart) return;

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
        return baseSeries;
      });

      const yAxis: echarts.YAXisComponentOption[] = [];
      if (hasLiquids) {
        yAxis.push({
          type: "value",
          name: unitMeta.liquidUnit,
          nameTextStyle: { color: "#64748b", fontSize: 11, padding: [0, 0, 0, 8] },
          axisLabel: {
            color: "#64748b",
            fontSize: 11,
            fontFamily: "JetBrains Mono, monospace",
            formatter: (v: number) =>
              v >= 1_000_000
                ? `${(v / 1_000_000).toFixed(1)}M`
                : v >= 1_000
                  ? `${(v / 1_000).toFixed(0)}k`
                  : String(v),
          },
          splitLine: { lineStyle: { color: "#f1f5f9" } },
        });
      }
      if (hasGases) {
        yAxis.push({
          type: "value",
          name: unitMeta.gasUnit,
          nameTextStyle: { color: "#0284c7", fontSize: 11 },
          axisLabel: {
            color: "#0284c7",
            fontSize: 11,
            fontFamily: "JetBrains Mono, monospace",
            formatter: (v: number) =>
              v >= 1_000_000
                ? `${(v / 1_000_000).toFixed(1)}M`
                : v >= 1_000
                  ? `${(v / 1_000).toFixed(0)}k`
                  : String(v),
          },
          splitLine: { show: !hasLiquids, lineStyle: { color: "#f1f5f9" } },
        });
      }
      if (yAxis.length === 0) {
        yAxis.push({ type: "value" });
      }

      chart.setOption(
        {
          animationDuration: 260,
          grid: {
            left: 56,
            right: hasLiquids && hasGases ? 58 : 20,
            top: 34,
            bottom: rows.length > 18 ? 58 : 30,
          },
          tooltip: {
            trigger: "axis",
            backgroundColor: "rgba(255,255,255,0.97)",
            borderColor: "#cbd5e1",
            textStyle: { color: "#0f172a", fontSize: 12 },
          },
          legend: {
            top: 0,
            left: 0,
            itemWidth: 14,
            itemHeight: 8,
            textStyle: { color: "#334155", fontSize: 11 },
          },
          xAxis: {
            type: "category",
            data: periods,
            boundaryGap: false,
            axisLine: { lineStyle: { color: "#cbd5e1" } },
            axisLabel: { color: "#64748b", fontSize: 11 },
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
        },
        true,
      );
    }, [rows, activeProducts, unitSystem, oilCapacityBpd]);

    return <div ref={containerRef} style={{ height }} className="w-full" />;
  },
);
