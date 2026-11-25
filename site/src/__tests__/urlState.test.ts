import { describe, expect, it } from "vitest";
import { DEFAULT_PRODUCTS, DEFAULT_UNITS, parseUrlState, serializeUrlState } from "../lib/urlState";

describe("URL query-param state sync", () => {
  it("defaults cleanly for empty search string and legacy source=all", () => {
    expect(parseUrlState("")).toEqual({
      source: null,
      selection: { kind: "overview" },
      products: DEFAULT_PRODUCTS,
      units: DEFAULT_UNITS,
    });
    expect(parseUrlState("?source=all").source).toBeNull();
    expect(
      serializeUrlState({
        source: null,
        selection: { kind: "overview" },
        products: DEFAULT_PRODUCTS,
        units: DEFAULT_UNITS,
      }),
    ).toBe("");
  });

  it("round-trips field, facility, well, products, and units", () => {
    const state = {
      source: "brazil",
      selection: { kind: "facility" as const, source: "brazil", id: "FPSO ALMIRANTE BARROSO" },
      products: ["oil_prod" as const, "water_inj" as const, "co2_inj" as const],
      units: "oilfield_mmcf" as const,
    };
    const qs = serializeUrlState(state);
    expect(qs).toContain("source=brazil");
    expect(qs).toContain("units=oilfield_mmcf");
    expect(parseUrlState(qs)).toEqual(state);
  });

  it("round-trips derived gor and water_cut series in products param", () => {
    const state = {
      source: "norway",
      selection: { kind: "field" as const, source: "norway", id: "JOHAN SVERDRUP" },
      products: ["oil_prod" as const, "gor" as const, "water_cut" as const],
      units: "oilfield_e3m3" as const,
    };
    const qs = serializeUrlState(state);
    expect(qs).toContain("products=oil_prod%2Cgor%2Cwater_cut");
    expect(parseUrlState(qs)).toEqual(state);
  });

  it("ignores invalid product names and unknown unit systems", () => {
    const parsed = parseUrlState("?source=norway&products=oil_prod,bogus&units=invalid");
    expect(parsed.source).toBe("norway");
    expect(parsed.products).toEqual(["oil_prod"]);
    expect(parsed.units).toBe(DEFAULT_UNITS);
  });
});
