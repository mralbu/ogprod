/** Max gap between two clicks on the same legend item to count as a double click. */
export const LEGEND_DOUBLE_CLICK_MS = 350;

/**
 * ECharts has no legend double-click event: each click of a double click emits
 * `legendselectchanged`, so detect the pair by name and timing. Returns a
 * function that reports whether this click completes a double click.
 */
export function createLegendDoubleClickDetector(
  thresholdMs: number = LEGEND_DOUBLE_CLICK_MS,
): (name: string, nowMs: number) => boolean {
  let last: { name: string; at: number } | null = null;
  return (name, nowMs) => {
    const isDouble = last !== null && last.name === name && nowMs - last.at <= thresholdMs;
    // A completed double click must not pair with a third click.
    last = isDouble ? null : { name, at: nowMs };
    return isDouble;
  };
}

/**
 * Legend `selected` map after double-clicking `target`: keep only `target`
 * visible, or show everything if it already was the only visible item.
 * `current` is the selection before the double click (names missing from it
 * count as visible, matching the ECharts default).
 */
export function legendSelectionAfterDoubleClick(
  names: readonly string[],
  target: string,
  current: Readonly<Record<string, boolean>>,
): Record<string, boolean> {
  const isVisible = (name: string) => current[name] ?? true;
  const alreadyIsolated = names.every((name) => isVisible(name) === (name === target));
  return Object.fromEntries(names.map((name) => [name, alreadyIsolated || name === target]));
}
