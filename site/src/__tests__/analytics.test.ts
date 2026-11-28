import { describe, expect, it } from "vitest";
import { outboundEventName, selectionToPath } from "../lib/analytics";
import type { EntitySelection } from "../types/data";

const SITE = "https://mralbu.github.io/ogprod/";

describe("selectionToPath", () => {
  const cases: [EntitySelection, string | null, string][] = [
    [{ kind: "overview" }, null, "/"],
    [{ kind: "overview" }, "brazil", "/brazil"],
    [{ kind: "basin", source: "brazil", basin: "Santos" }, "brazil", "/brazil/basin/Santos"],
    [{ kind: "field", source: "brazil", id: "BÚZIOS" }, null, "/brazil/field/B%C3%9AZIOS"],
    [{ kind: "facility", source: "norway", id: "A/B?c" }, null, "/norway/facility/A%2FB%3Fc"],
    [{ kind: "well", source: "mexico", id: "  " }, null, "/mexico/well/-"],
  ];

  it.each(cases)("maps %o (source=%s) to %s", (selection, source, expected) => {
    expect(selectionToPath(selection, source)).toBe(expected);
  });

  it("never leaks a query string, fragment, or extra path segment", () => {
    const nasty = ["a?b=c", "x#y", "p/q", "&=%", "../.."];
    for (const id of nasty) {
      for (const kind of ["field", "facility", "well"] as const) {
        const path = selectionToPath({ kind, source: id, id }, null);
        expect(path).not.toMatch(/[?#&]/);
        expect(path.split("/")).toHaveLength(4);
      }
      const basin = selectionToPath({ kind: "basin", source: id, basin: id }, null);
      expect(basin).not.toMatch(/[?#&]/);
      expect(basin.split("/")).toHaveLength(4);
    }
  });
});

describe("outboundEventName", () => {
  it.each([
    ["https://huggingface.co/datasets/mralbu/ogprod", "outbound/huggingface"],
    ["https://github.com/mralbu/ogprod", "outbound/github"],
    ["https://mralbu.github.io/ogprod/llms.txt", "outbound/llms-txt"],
    ["./llms.txt", "outbound/llms-txt"],
    ["https://www.gov.br/anp", "outbound/source-docs"],
    ["https://www.sodir.no/en/", "outbound/source-docs"],
  ])("classifies %s as %s", (href, expected) => {
    expect(outboundEventName(href, SITE)).toBe(expected);
  });

  it.each([
    "https://mralbu.github.io/ogprod/?entity=field:brazil/X",
    "https://mralbu.github.io/ogprod/data/meta.json",
    "#",
    "mailto:someone@example.com",
    "javascript:void(0)",
  ])("ignores %s", (href) => {
    expect(outboundEventName(href, SITE)).toBeNull();
  });

  it("does not treat look-alike hosts as GitHub or Hugging Face", () => {
    expect(outboundEventName("https://notgithub.com/x", SITE)).toBe("outbound/source-docs");
    expect(outboundEventName("https://huggingface.co.evil.io/x", SITE)).toBe(
      "outbound/source-docs",
    );
  });
});
