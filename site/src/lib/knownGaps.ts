import type {
  EntitySelection,
  KnownGap,
  MonthlyPoint,
  RateColumn,
  SourceMeta,
} from "../types/data";

export interface ScopedKnownGap extends KnownGap {
  sourceId: string;
  sourceLabel: string;
}

export interface SelectApplicableGapsParams {
  sourcesMeta: readonly SourceMeta[];
  selectionKind: EntitySelection["kind"];
  visibleProducts: readonly RateColumn[];
  series: readonly MonthlyPoint[];
}

/**
 * Select known reporting gaps that actually apply to the currently displayed view:
 * - the gap's product is toggled on;
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
      if (!visibleProducts.includes(g.column)) continue;
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
