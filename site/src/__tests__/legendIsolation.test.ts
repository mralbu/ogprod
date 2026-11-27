import { describe, expect, it } from "vitest";
import {
  createLegendDoubleClickDetector,
  LEGEND_DOUBLE_CLICK_MS,
  legendSelectionAfterDoubleClick,
} from "../lib/legendIsolation";

describe("createLegendDoubleClickDetector", () => {
  it("detects two quick clicks on the same item", () => {
    const detect = createLegendDoubleClickDetector();
    expect(detect("Brazil", 1000)).toBe(false);
    expect(detect("Brazil", 1000 + LEGEND_DOUBLE_CLICK_MS)).toBe(true);
  });

  it("ignores slow clicks and clicks on different items", () => {
    const detect = createLegendDoubleClickDetector();
    expect(detect("Brazil", 0)).toBe(false);
    expect(detect("Brazil", LEGEND_DOUBLE_CLICK_MS + 1)).toBe(false);
    expect(detect("Norway", LEGEND_DOUBLE_CLICK_MS + 50)).toBe(false);
  });

  it("does not pair a third click with a completed double click", () => {
    const detect = createLegendDoubleClickDetector();
    detect("Brazil", 0);
    expect(detect("Brazil", 100)).toBe(true);
    expect(detect("Brazil", 200)).toBe(false);
    expect(detect("Brazil", 300)).toBe(true);
  });
});

describe("legendSelectionAfterDoubleClick", () => {
  const names = ["Brazil", "Norway", "Other (3)"];

  it("isolates the target when several items are visible", () => {
    expect(legendSelectionAfterDoubleClick(names, "Norway", {})).toEqual({
      Brazil: false,
      Norway: true,
      "Other (3)": false,
    });
  });

  it("isolates a hidden target", () => {
    const current = { Brazil: true, Norway: false, "Other (3)": false };
    expect(legendSelectionAfterDoubleClick(names, "Norway", current)).toEqual({
      Brazil: false,
      Norway: true,
      "Other (3)": false,
    });
  });

  it("restores all items when the target was the only visible one", () => {
    const isolated = legendSelectionAfterDoubleClick(names, "Norway", {});
    expect(legendSelectionAfterDoubleClick(names, "Norway", isolated)).toEqual({
      Brazil: true,
      Norway: true,
      "Other (3)": true,
    });
  });
});
