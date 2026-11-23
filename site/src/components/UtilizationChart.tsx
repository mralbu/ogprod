import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import type { MonthlyPoint } from "../types/data";

interface UtilizationChartProps {
  rows: MonthlyPoint[];
  height?: number;
}

export function UtilizationChart({ rows, height = 160 }: UtilizationChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

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
    const periods = rows.map((r) => r.p.slice(0, 7));
    const pct = rows.map((r) =>
      r.oil_util !== undefined ? Number((r.oil_util * 100).toFixed(1)) : null,
    );

    chart.setOption(
      {
        animationDuration: 240,
        grid: { left: 46, right: 18, top: 24, bottom: 26 },
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(255,255,255,0.97)",
          borderColor: "#cbd5e1",
          textStyle: { color: "#0f172a", fontSize: 12 },
          valueFormatter: (v: unknown) => (typeof v === "number" ? `${v.toFixed(1)}%` : "—"),
        },
        xAxis: {
          type: "category",
          data: periods,
          boundaryGap: false,
          axisLine: { lineStyle: { color: "#cbd5e1" } },
          axisLabel: { color: "#64748b", fontSize: 10 },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: (v: { max: number }) => Math.max(100, Math.ceil(v.max / 10) * 10),
          axisLabel: {
            color: "#64748b",
            fontSize: 10,
            fontFamily: "JetBrains Mono, monospace",
            formatter: "{value}%",
          },
          splitLine: { lineStyle: { color: "#f1f5f9" } },
        },
        series: [
          {
            name: "Oil Capacity Utilization",
            type: "line",
            showSymbol: rows.length <= 24,
            lineStyle: { width: 2, color: "#0e7490" },
            itemStyle: { color: "#0e7490" },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: "rgba(14, 116, 144, 0.22)" },
                { offset: 1, color: "rgba(14, 116, 144, 0.02)" },
              ]),
            },
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: "#dc2626", type: "dashed", width: 1.2 },
              label: { formatter: "100% Nameplate", position: "insideEndTop", fontSize: 10 },
              data: [{ yAxis: 100 }],
            },
            data: pct,
          },
        ],
      },
      true,
    );
  }, [rows]);

  return <div ref={containerRef} style={{ height }} className="w-full" />;
}
