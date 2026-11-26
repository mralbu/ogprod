import type { SourceMeta } from "../types/data";

/** Maximum source count before TopBar switches from pill buttons to a compact dropdown. */
export const SOURCE_PILL_MAX = 6;

/**
 * Resolve a source slug (e.g. "brazil", "mexico", "us-gom") to its normalized
 * display country name from SiteMeta, falling back to Title Case if absent.
 */
export function formatCountryName(
  sourceId: string | null | undefined,
  sources?: readonly SourceMeta[],
): string {
  const trimmed = (sourceId ?? "").trim();
  if (!trimmed) return "";
  const match = sources?.find((s) => s.id === trimmed);
  if (match?.country) return match.country;
  return trimmed
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Format a source's label for switchers and chart legends, disambiguating sub-national
 * slugs (e.g. "us-gom" and "us-ak") when multiple sources share the same country name.
 */
export function formatSourceDisplayName(
  source: Pick<SourceMeta, "id" | "country">,
  allSources?: readonly Pick<SourceMeta, "id" | "country">[],
): string {
  const base = source.country?.trim() || formatCountryName(source.id);
  if (!allSources) return base;
  const duplicates = allSources.filter((s) => (s.country?.trim() || s.id) === base).length > 1;
  if (!duplicates) return base;
  const parts = source.id.split(/[-_\s]+/).filter(Boolean);
  const qualifier = parts.length > 1 ? parts.slice(1).join(" ").toUpperCase() : source.id.toUpperCase();
  return `${base} (${qualifier})`;
}

function extractYear(period: string | null | undefined): number | null {
  if (!period) return null;
  const year = Number.parseInt(period.slice(0, 4), 10);
  return Number.isFinite(year) ? year : null;
}

/**
 * Format a compact overview subtitle:
 * - Multiple sources: "7 public regulators · 1941–2026"
 * - Single source: "BSEE / BOEM (OGOR-A) · 1996–2026"
 */
export function formatOverviewSubtitle(
  sources: readonly Pick<SourceMeta, "publisher" | "first_period" | "last_period">[],
): string {
  if (sources.length === 0) return "";
  const years = sources
    .flatMap((s) => [extractYear(s.first_period), extractYear(s.last_period)])
    .filter((y): y is number => y !== null);
  const span =
    years.length > 0
      ? (() => {
          const minY = Math.min(...years);
          const maxY = Math.max(...years);
          return minY === maxY ? `${minY}` : `${minY}–${maxY}`;
        })()
      : "";
  const lead =
    sources.length === 1 ? sources[0].publisher : `${sources.length} public regulators`;
  return span ? `${lead} · ${span}` : lead;
}
