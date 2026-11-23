import { describe, expect, it } from "vitest";
import { DEFAULT_PRODUCTS, DEFAULT_UNITS, parseUrlState, serializeUrlState } from "../lib/urlState";

describe("URL query-param state sync", () => {
  it("defaults cleanly for empty search string", () => {
    expect(parseUrlState("")).toEqual({
      source: "all",
      selection: { kind: "overview" },
      products: DEFAULT_PRODUCTS,
      units: DEFAULT_UNITS,
    });
    expect(
      serializeUrlState({
        source: "all",
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

  it("ignores invalid product names and unknown unit systems", () => {
    const parsed = parseUrlState("?source=norway&products=oil_prod,bogus&units=invalid");
    expect(parsed.source).toBe("norway");
    expect(parsed.products).toEqual(["oil_prod"]);
    expect(parsed.units).toBe(DEFAULT_UNITS);
  });
});
