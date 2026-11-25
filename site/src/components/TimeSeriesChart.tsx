import * as echarts from "echarts";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import {
  buildSourceComparisonOption,
  buildTimeSeriesOption,
  type SourceComparisonTable,
} from "../lib/timeSeriesOption";
import type { ChartSeriesKey, KnownGap, MonthlyPoint, UnitSystem } from "../types/data";

export interface TimeSeriesChartHandle {
  exportPng: (filename: string) => void;
}

interface TimeSeriesChartProps {
  rows: MonthlyPoint[];
  activeProducts: ChartSeriesKey[];
  unitSystem: UnitSystem;
  oilCapacityBpd?: number | null;
  knownGaps?: readonly KnownGap[];
  comparisonTable?: SourceComparisonTable | null;
  height?: number;
}

export const TimeSeriesChart = forwardRef<TimeSeriesChartHandle, TimeSeriesChartProps>(
  function TimeSeriesChart(
    {
      rows,
      activeProducts,
      unitSystem,
      oilCapacityBpd,
      knownGaps,
      comparisonTable,
      height = 330,
    },
    ref,
  ) {
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
      const option = comparisonTable
        ? buildSourceComparisonOption(comparisonTable, unitSystem)
        : buildTimeSeriesOption({ rows, activeProducts, unitSystem, oilCapacityBpd, knownGaps });
      chart.setOption(option, true);
    }, [rows, activeProducts, unitSystem, oilCapacityBpd, knownGaps, comparisonTable]);

    return <div ref={containerRef} style={{ height }} className="w-full" />;
  },
);
