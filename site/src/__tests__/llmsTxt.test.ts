import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { llmsTxtUrl } from "../lib/dataClient";

describe("llms.txt site discovery", () => {
  it("resolves llms.txt under the configured base URL", () => {
    expect(llmsTxtUrl()).toMatch(/\/llms\.txt$/);
  });

  it("exposes an alternate link and noscript data guide in index.html", () => {
    const html = fs.readFileSync(path.resolve(import.meta.dirname, "../../index.html"), "utf-8");
    expect(html).toContain('rel="alternate"');
    expect(html).toContain('href="./llms.txt"');
    expect(html).toContain("<noscript>");
    expect(html).toContain('href="./data/meta.json"');
  });
});
