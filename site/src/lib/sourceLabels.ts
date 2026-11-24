import type { SourceMeta } from "../types/data";

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
