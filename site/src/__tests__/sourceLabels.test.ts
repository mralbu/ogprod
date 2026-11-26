import { describe, expect, it } from "vitest";
import {
  formatCountryName,
  formatOverviewSubtitle,
  formatSourceDisplayName,
} from "../lib/sourceLabels";
import type { SourceMeta } from "../types/data";

const SOURCES = [
  { id: "brazil", country: "Brazil" },
  { id: "norway", country: "Norway" },
  { id: "mexico", country: "Mexico" },
  { id: "us-gom", country: "United States" },
] as unknown as SourceMeta[];

describe("formatCountryName", () => {
  it("resolves known source slugs from SiteMeta and qualifies sub-national sources", () => {
    expect(formatCountryName("brazil", SOURCES)).toBe("Brazil");
    expect(formatCountryName("norway", SOURCES)).toBe("Norway");
    expect(formatCountryName("mexico", SOURCES)).toBe("Mexico");
    expect(formatCountryName("us-gom", SOURCES)).toBe("US (GoM)");
    expect(
      formatCountryName("us-gom", [{ id: "us-gom", country: "US (GoM)" } as SourceMeta]),
    ).toBe("US (GoM)");
  });

  it("falls back to Title Case for unknown slugs and handles blanks", () => {
    expect(formatCountryName("argentina", SOURCES)).toBe("Argentina");
    expect(formatCountryName("guyana_offshore")).toBe("Guyana Offshore");
    expect(formatCountryName("")).toBe("");
    expect(formatCountryName(null)).toBe("");
    expect(formatCountryName(undefined)).toBe("");
  });
});

describe("formatSourceDisplayName", () => {
  it("always qualifies sub-national slugs even when only one region is present", () => {
    const singleUs = [
      { id: "brazil", country: "Brazil" },
      { id: "us-gom", country: "United States" },
    ];
    expect(formatSourceDisplayName(singleUs[0], singleUs)).toBe("Brazil");
    expect(formatSourceDisplayName(singleUs[1], singleUs)).toBe("US (GoM)");

    const multi = [
      { id: "brazil", country: "Brazil" },
      { id: "us-gom", country: "US (GoM)" },
      { id: "us-ak", country: "United States" },
      { id: "canada-nl", country: "Canada" },
    ];
    expect(formatSourceDisplayName(multi[1], multi)).toBe("US (GoM)");
    expect(formatSourceDisplayName(multi[2], multi)).toBe("US (AK)");
    expect(formatSourceDisplayName(multi[3], multi)).toBe("Canada (NL)");
  });
});

describe("formatOverviewSubtitle", () => {
  it("shows regulator count and year span for multi-source overview", () => {
    expect(
      formatOverviewSubtitle([
        { publisher: "ANP", first_period: "1941-01-01", last_period: "2026-02-01" },
        { publisher: "Sodir", first_period: "1971-06-01", last_period: "2026-09-01" },
        { publisher: "BSEE / BOEM (OGOR-A)", first_period: "1996-01-01", last_period: "2026-09-01" },
      ]),
    ).toBe("3 public regulators · 1941–2026");
  });

  it("shows publisher and year span for a single source", () => {
    expect(
      formatOverviewSubtitle([
        { publisher: "BSEE / BOEM (OGOR-A)", first_period: "1996-01-01", last_period: "2026-09-01" },
      ]),
    ).toBe("BSEE / BOEM (OGOR-A) · 1996–2026");
  });

  it("handles empty input and missing dates", () => {
    expect(formatOverviewSubtitle([])).toBe("");
    expect(
      formatOverviewSubtitle([{ publisher: "ANH", first_period: null, last_period: null }]),
    ).toBe("ANH");
  });
});
