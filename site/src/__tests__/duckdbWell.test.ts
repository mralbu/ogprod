import { describe, expect, it } from "vitest";
import { buildWellPartitionUrls, buildWellQuerySql } from "../lib/duckdbWell";

describe("lazy DuckDB-WASM well query pruning", () => {
  it("requests only partitions between firstYear and lastYear", () => {
    const urls = buildWellPartitionUrls(
      "https://huggingface.co/datasets/mralbu/ogprod/resolve/main/",
      "brazil",
      2023,
      2025,
      [2020, 2021, 2022, 2023, 2025, 2026],
    );
    expect(urls).toEqual([
      "https://huggingface.co/datasets/mralbu/ogprod/resolve/main/production/source=brazil/year=2023/data.parquet",
      "https://huggingface.co/datasets/mralbu/ogprod/resolve/main/production/source=brazil/year=2025/data.parquet",
    ]);
  });

  it("returns empty array when year bounds are inverted", () => {
    expect(buildWellPartitionUrls("https://example.com", "brazil", 2026, 2020)).toEqual([]);
  });

  it("escapes single quotes in well IDs when constructing SQL", () => {
    const sql = buildWellQuerySql(
      ["https://example.com/production/source=brazil/year=2026/data.parquet"],
      "7-O'NEIL-1-RJS",
    );
    expect(sql).toContain("WHERE entity_id = '7-O''NEIL-1-RJS'");
    expect(sql).toContain("read_parquet(['https://example.com/production/source=brazil/year=2026/data.parquet'])");
  });
});
