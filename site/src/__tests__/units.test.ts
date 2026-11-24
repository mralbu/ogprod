import { describe, expect, it } from "vitest";
import {
  convertRate,
  formatCompactNumber,
  formatPercent,
  M3_PER_BBL,
  MMCF_PER_E3M3,
  UNIT_SYSTEMS,
  unitLabelFor,
} from "../lib/units";

describe("unit conversions", () => {
  it("preserves canonical rates under oilfield_e3m3", () => {
    expect(convertRate(100_000, "oil_prod", "oilfield_e3m3")).toBe(100_000);
    expect(convertRate(1_000, "gas_prod", "oilfield_e3m3")).toBe(1_000);
    expect(unitLabelFor("oil_prod", "oilfield_e3m3")).toBe("bbl/d");
    expect(unitLabelFor("gas_prod", "oilfield_e3m3")).toBe("10³ m³/d");
  });

  it("renders human-readable 10³ m³/d labels instead of raw e3m3", () => {
    const defaultSystem = UNIT_SYSTEMS.find((u) => u.id === "oilfield_e3m3");
    expect(defaultSystem?.label).toBe("bbl/d & 10³ m³/d");
    expect(unitLabelFor("gas_prod", "metric_m3")).toBe("10³ m³/d");
    for (const system of UNIT_SYSTEMS) {
      expect(system.label).not.toContain("e3m3");
      expect(system.liquidUnit).not.toContain("e3m3");
      expect(system.gasUnit).not.toContain("e3m3");
    }
  });

  it("converts gas from e3m3/d to MMcf/d accurately under oilfield_mmcf", () => {
    const mmcfd = convertRate(1_000, "gas_prod", "oilfield_mmcf");
    expect(mmcfd).toBeCloseTo(1_000 * MMCF_PER_E3M3, 6);
    expect(mmcfd).toBeCloseTo(35.314667, 4);
    expect(convertRate(50_000, "oil_prod", "oilfield_mmcf")).toBe(50_000);
    expect(unitLabelFor("gas_inj", "oilfield_mmcf")).toBe("MMcf/d");
  });

  it("converts liquids from bbl/d to m3/d under metric_m3", () => {
    const m3d = convertRate(10_000, "oil_prod", "metric_m3");
    expect(m3d).toBeCloseTo(10_000 * M3_PER_BBL, 6);
    expect(unitLabelFor("water_inj", "metric_m3")).toBe("m³/d");
    expect(convertRate(null, "oil_prod", "metric_m3")).toBeNull();
  });

  it("formats compact numbers and percentages cleanly", () => {
    expect(formatCompactNumber(3_450_000)).toBe("3.5M");
    expect(formatCompactNumber(125_400)).toBe("125.4k");
    expect(formatCompactNumber(null)).toBe("—");
    expect(formatPercent(0.8234)).toBe("82.3%");
    expect(formatPercent(undefined)).toBe("—");
  });
});
