import type { EntitySelection, SourceFilter } from "../types/data";

// GoatCounter (cookieless, no consent banner). See docs/adr/0012-cookieless-analytics-goatcounter.md.
const SCRIPT_SRC = "https://gc.zgo.at/count.js";

interface GoatCounterHit {
  path: string;
  title?: string;
  event?: boolean;
}

interface GoatCounterGlobal {
  count: (vars: GoatCounterHit) => void;
}

declare global {
  interface Window {
    goatcounter?: GoatCounterGlobal;
  }
}

let enabled = false;
let pending: GoatCounterHit[] = [];

function segment(value: string): string {
  return encodeURIComponent(value.trim() || "-");
}

/** Query-string-free virtual path for the explorer state, e.g. `/ANP/field/BUZIOS`. */
export function selectionToPath(selection: EntitySelection, source: SourceFilter): string {
  switch (selection.kind) {
    case "overview":
      return source ? `/${segment(source)}` : "/";
    case "basin":
      return `/${segment(selection.source)}/basin/${segment(selection.basin)}`;
    case "field":
    case "facility":
    case "well":
      return `/${segment(selection.source)}/${selection.kind}/${segment(selection.id)}`;
  }
}

const OUTBOUND_HOSTS: [RegExp, string][] = [
  [/(^|\.)huggingface\.co$/, "huggingface"],
  [/(^|\.)github\.com$/, "github"],
];

/** Event name for a clicked link, or null when the click is not worth tracking. */
export function outboundEventName(href: string, siteOrigin: string): string | null {
  let url: URL;
  try {
    url = new URL(href, siteOrigin);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.origin === new URL(siteOrigin).origin) {
    return url.pathname.endsWith("/llms.txt") ? "outbound/llms-txt" : null;
  }
  for (const [pattern, name] of OUTBOUND_HOSTS) {
    if (pattern.test(url.hostname)) return `outbound/${name}`;
  }
  return "outbound/source-docs";
}

function send(hit: GoatCounterHit): void {
  if (!enabled) return;
  if (window.goatcounter?.count) {
    window.goatcounter.count(hit);
  } else {
    pending.push(hit);
  }
}

export function trackPageview(path: string): void {
  send({ path });
}

export function trackEvent(name: string): void {
  send({ path: name, title: name, event: true });
}

function onDocumentClick(e: MouseEvent): void {
  const target = e.target instanceof Element ? e.target.closest("a[href]") : null;
  if (!(target instanceof HTMLAnchorElement)) return;
  const name = outboundEventName(target.href, window.location.origin);
  if (name) trackEvent(name);
}

/**
 * Load GoatCounter when `endpoint` (the `/count` URL) is set; a no-op otherwise, so local
 * dev, tests and forks never report to the production dashboard.
 */
export function initAnalytics(endpoint: string | undefined): void {
  if (enabled || !endpoint) return;
  enabled = true;
  const script = document.createElement("script");
  script.async = true;
  script.src = SCRIPT_SRC;
  script.dataset.goatcounter = endpoint;
  // The SPA reports clean virtual paths itself instead of the raw `?entity=…` URL.
  script.dataset.goatcounterSettings = JSON.stringify({ no_onload: true });
  script.addEventListener("load", () => {
    const queued = pending;
    pending = [];
    queued.forEach(send);
  });
  document.head.appendChild(script);
  document.addEventListener("click", onDocumentClick, { capture: true });
}
