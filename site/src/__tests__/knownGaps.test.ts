import { describe, expect, it } from "vitest";
import { selectApplicableGaps } from "../lib/knownGaps";
import type { MonthlyPoint, SourceMeta } from "../types/data";

function makeBrazilSource(resolvedInCountry = true): SourceMeta {
  return {
    id: "brazil",
    country: "Brazil",
    country_code: "BR",
    publisher: "ANP",
    docs_url: "https://www.gov.br/anp",
    licence: "Public open data",
    entity_type: "well",
    bbox: null,
    years: [2003, 2004, 2005, 2025],
    first_period: "2003-01-01",
    last_period: "2025-12-01",
    updated_at: null,
    counts: { production_rows: 10, producing_entities: 2, fields: 1, facilities: 1, wells: 2 },
    latest_oil_bpd: 1000,
    latest_gas_e3m3d: 500,
    available_products: ["oil_prod", "gas_prod"],
    known_gaps: [
      {
        column: "gas_prod",
        start: "2003-07-01",
        end: "2005-06-01",
        description: "only ~600 of ~8,300 producing wells report gas",
        resolved_in_country_rollup: resolvedInCountry,
      },
    ],
  };
}

const OVERLAPPING_SERIES: MonthlyPoint[] = [
  { p: "2003-06-01", oil_prod: 100, gas_prod: 50 },
  { p: "2004-02-01", oil_prod: 105 },
  { p: "2005-07-01", oil_prod: 110, gas_prod: 55 },
];

const POST_GAP_SERIES: MonthlyPoint[] = [
  { p: "2018-04-01", oil_prod: 50_000, gas_prod: 2_000 },
  { p: "2025-12-01", oil_prod: 90_000, gas_prod: 3_500 },
];

const PRE_GAP_SERIES: MonthlyPoint[] = [
  { p: "1990-01-01", oil_prod: 1000, gas_prod: 200 },
  { p: "2003-06-01", oil_prod: 800, gas_prod: 150 },
];

describe("selectApplicableGaps", () => {
  it("hides country-resolved gaps on overview views", () => {
    const gaps = selectApplicableGaps({
      sourcesMeta: [makeBrazilSource(true)],
      selectionKind: "overview",
      visibleProducts: ["oil_prod", "gas_prod"],
      series: OVERLAPPING_SERIES,
    });
    expect(gaps).toEqual([]);
  });

  it("shows unresolved gaps on overview views when series overlaps", () => {
    const gaps = selectApplicableGaps({
      sourcesMeta: [makeBrazilSource(false)],
      selectionKind: "overview",
      visibleProducts: ["oil_prod", "gas_prod"],
      series: OVERLAPPING_SERIES,
    });
    expect(gaps).toHaveLength(1);
    expect(gaps[0].sourceLabel).toBe("Brazil (ANP)");
  });

  it("shows gaps on sub-national views only when series overlaps the gap window", () => {
    const basinGaps = selectApplicableGaps({
      sourcesMeta: [makeBrazilSource(true)],
      selectionKind: "basin",
      visibleProducts: ["oil_prod", "gas_prod"],
      series: OVERLAPPING_SERIES,
    });
    expect(basinGaps).toHaveLength(1);
    expect(basinGaps[0].sourceId).toBe("brazil");
    expect(basinGaps[0].sourceLabel).toBe("Brazil (ANP)");

    expect(
      selectApplicableGaps({
        sourcesMeta: [makeBrazilSource(true)],
        selectionKind: "field",
        visibleProducts: ["oil_prod", "gas_prod"],
        series: POST_GAP_SERIES,
      }),
    ).toEqual([]);

    expect(
      selectApplicableGaps({
        sourcesMeta: [makeBrazilSource(true)],
        selectionKind: "well",
        visibleProducts: ["oil_prod", "gas_prod"],
        series: PRE_GAP_SERIES,
      }),
    ).toEqual([]);
  });

  it("hides gaps when the affected product is toggled off or series is empty", () => {
    expect(
      selectApplicableGaps({
        sourcesMeta: [makeBrazilSource(true)],
        selectionKind: "basin",
        visibleProducts: ["oil_prod"],
        series: OVERLAPPING_SERIES,
      }),
    ).toEqual([]);

    expect(
      selectApplicableGaps({
        sourcesMeta: [makeBrazilSource(true)],
        selectionKind: "basin",
        visibleProducts: ["oil_prod", "gas_prod"],
        series: [],
      }),
    ).toEqual([]);
  });

  it("surfaces gaps affecting dependencies of derived ratios like GOR", () => {
    const gorGaps = selectApplicableGaps({
      sourcesMeta: [makeBrazilSource(true)],
      selectionKind: "basin",
      visibleProducts: ["oil_prod", "gor"],
      series: OVERLAPPING_SERIES,
    });
    expect(gorGaps).toHaveLength(1);
    expect(gorGaps[0].column).toBe("gas_prod");

    expect(
      selectApplicableGaps({
        sourcesMeta: [makeBrazilSource(true)],
        selectionKind: "basin",
        visibleProducts: ["oil_prod", "water_cut"],
        series: OVERLAPPING_SERIES,
      }),
    ).toEqual([]);
  });
});
