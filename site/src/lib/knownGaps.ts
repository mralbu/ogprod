import type {
  ChartSeriesKey,
  EntitySelection,
  KnownGap,
  MonthlyPoint,
  RateColumn,
  SourceMeta,
} from "../types/data";
import { isDerivedSeries } from "../types/data";
import { DERIVED_SPECS } from "./units";

export interface ScopedKnownGap extends KnownGap {
  sourceId: string;
  sourceLabel: string;
}

export interface SelectApplicableGapsParams {
  sourcesMeta: readonly SourceMeta[];
  selectionKind: EntitySelection["kind"];
  visibleProducts: readonly ChartSeriesKey[];
  series: readonly MonthlyPoint[];
}

/**
 * Select known reporting gaps that actually apply to the currently displayed view:
 * - the gap's product (or a derived ratio depending on it) is toggled on;
 * - overview selections skip gaps already resolved in the country rollup;
 * - the displayed time series overlaps the gap's [start, end] date window.
 */
export function selectApplicableGaps({
  sourcesMeta,
  selectionKind,
  visibleProducts,
  series,
}: SelectApplicableGapsParams): ScopedKnownGap[] {
  if (series.length === 0) return [];

  const effectiveColumns = new Set<RateColumn>();
  for (const key of visibleProducts) {
    if (isDerivedSeries(key)) {
      for (const dep of DERIVED_SPECS[key].dependsOn) {
        effectiveColumns.add(dep);
      }
    } else {
      effectiveColumns.add(key);
    }
  }

  let firstPeriod = series[0].p;
  let lastPeriod = series[0].p;
  for (let i = 1; i < series.length; i++) {
    const p = series[i].p;
    if (p < firstPeriod) firstPeriod = p;
    if (p > lastPeriod) lastPeriod = p;
  }

  const gaps: ScopedKnownGap[] = [];
  for (const src of sourcesMeta) {
    const sourceLabel = `${src.country} (${src.publisher})`;
    for (const g of src.known_gaps) {
      if (!effectiveColumns.has(g.column)) continue;
      if (selectionKind === "overview" && g.resolved_in_country_rollup) continue;
      if (lastPeriod < g.start || firstPeriod > g.end) continue;
      gaps.push({
        ...g,
        sourceId: src.id,
        sourceLabel,
      });
    }
  }
  return gaps;
}
