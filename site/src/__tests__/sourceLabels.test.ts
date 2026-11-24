import { describe, expect, it } from "vitest";
import { formatCountryName } from "../lib/sourceLabels";
import type { SourceMeta } from "../types/data";

const SOURCES = [
  { id: "brazil", country: "Brazil" },
  { id: "norway", country: "Norway" },
  { id: "mexico", country: "Mexico" },
  { id: "us-gom", country: "United States (GoM)" },
] as unknown as SourceMeta[];

describe("formatCountryName", () => {
  it("resolves known source slugs from SiteMeta", () => {
    expect(formatCountryName("brazil", SOURCES)).toBe("Brazil");
    expect(formatCountryName("norway", SOURCES)).toBe("Norway");
    expect(formatCountryName("mexico", SOURCES)).toBe("Mexico");
    expect(formatCountryName("us-gom", SOURCES)).toBe("United States (GoM)");
  });

  it("falls back to Title Case for unknown slugs and handles blanks", () => {
    expect(formatCountryName("argentina", SOURCES)).toBe("Argentina");
    expect(formatCountryName("guyana_offshore")).toBe("Guyana Offshore");
    expect(formatCountryName("")).toBe("");
    expect(formatCountryName(null)).toBe("");
    expect(formatCountryName(undefined)).toBe("");
  });
});
