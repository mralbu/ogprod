import { describe, expect, it } from "vitest";
import { buildComparisonCsv, buildSeriesCsv } from "../lib/csvExport";
import { buildSourceComparisonTable } from "../lib/timeSeriesOption";
import type { SourceMeta } from "../types/data";

describe("CSV export", () => {
  it("exports converted units and optional FPSO utilization", () => {
    const csv = buildSeriesCsv(
      [
        { p: "2026-01-01", oil_prod: 100_000, gas_prod: 1_000, oil_util: 0.8 },
        { p: "2026-02-01", oil_prod: 90_000 },
      ],
      ["oil_prod", "gas_prod"],
      "oilfield_mmcf",
      { includeUtilization: true },
    );
    const lines = csv.trim().split("\n");
    expect(lines[0]).toBe("period,Oil (bbl/d),Gas (MMcf/d),Oil Utilization (%)");
    expect(lines[1]).toBe("2026-01-01,100000.00,35.31,80.00");
    expect(lines[2]).toBe("2026-02-01,90000.00,,");
  });

  it("exports derived GOR and Water Cut columns in active units", () => {
    const csv = buildSeriesCsv(
      [
        { p: "2026-01-01", oil_prod: 10_000, gas_prod: 300, water_prod: 10_000 },
        { p: "2026-02-01", oil_prod: 10_000 },
      ],
      ["oil_prod", "gor", "water_cut"],
      "oilfield_mmcf",
    );
    const lines = csv.trim().split("\n");
    expect(lines[0]).toBe("period,Oil (bbl/d),GOR (scf/bbl),WCUT (%)");
    expect(lines[1]).toBe("2026-01-01,10000.00,1059.44,50.00");
    expect(lines[2]).toBe("2026-02-01,10000.00,,");
  });

  it("exports per-source columns with empty cells for lagging months", () => {
    const table = buildSourceComparisonTable({
      countryMonthly: {
        brazil: [
          { p: "2026-01-01", oil_prod: 3_000_000 },
          { p: "2026-02-01", oil_prod: 3_100_000 },
        ],
        uk: [{ p: "2026-01-01", oil_prod: 600_000 }],
      },
      sources: [
        { id: "brazil", country: "Brazil" },
        { id: "uk", country: "United Kingdom" },
      ] as unknown as SourceMeta[],
      product: "oil_prod",
      unitSystem: "oilfield_e3m3",
    });
    const lines = buildComparisonCsv(table).trim().split("\n");
    expect(lines[0]).toBe("period,Brazil (bbl/d),United Kingdom (bbl/d)");
    expect(lines[1]).toBe("2026-01-01,3000000.00,600000.00");
    expect(lines[2]).toBe("2026-02-01,3100000.00,");
  });
});
