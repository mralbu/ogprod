# 0012. Cookieless analytics with GoatCounter

Status: accepted

## Context

We want to know whether the public explorer is used (visits, referrers, countries, which
views people open) without a consent banner, heavy scripts, or sharing visitor data with
an ad platform. Many visitors are in the EEA (e.g. Norway), where cookie-based analytics
such as Google Analytics require GDPR consent.

## Decision

- Use hosted GoatCounter: free for non-commercial use, open source, cookieless, and it
  stores no IP addresses, so no banner is needed. Cloudflare Web Analytics was rejected
  for its 6-month retention, sampling after 7 days, no custom events, and dropped
  query strings.
- The script loads only when `VITE_GOATCOUNTER_URL` is set at build time, from the
  repository variable `GOATCOUNTER_URL` in `deploy-site.yml`. Local dev, tests, and forks
  therefore never report to the production dashboard.
- GoatCounter's automatic page-load count is disabled (`no_onload`). The SPA reports a
  query-string-free virtual path per selection (`/brazil/field/BUZIOS`; see
  `selectionToPath`), debounced, so each deep link does not appear as a separate page.
- Events: `export/csv`, `export/png`, and `outbound/{huggingface,github,llms-txt,source-docs}`.

## Consequences

Ad-blockers that list `gc.zgo.at` under-count visits. Dashboard numbers are trends, not
exact totals. Entity IDs are public regulator identifiers, so virtual paths contain no
personal data.
