import { Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { loadSearchIndex } from "../lib/dataClient";
import { formatCompactNumber, UNIT_SYSTEMS } from "../lib/units";
import type { SearchEntry, SourceFilter, SourceMeta, UnitSystem } from "../types/data";

interface TopBarProps {
  sources: SourceMeta[];
  activeSource: SourceFilter;
  onSelectSource: (source: SourceFilter) => void;
  unitSystem: UnitSystem;
  onChangeUnits: (units: UnitSystem) => void;
  onSelectSearchEntry: (entry: SearchEntry) => void;
}

const BADGE_LABELS: Record<SearchEntry["t"], string> = {
  field: "FIELD",
  facility: "FPSO / FAC",
  well: "WELL",
};

const BADGE_CLASSES: Record<SearchEntry["t"], string> = {
  field: "bg-amber-50 text-amber-800 border-amber-200",
  facility: "bg-sky-50 text-sky-800 border-sky-200",
  well: "bg-slate-100 text-slate-700 border-slate-200",
};

export function TopBar({
  sources,
  activeSource,
  onSelectSource,
  unitSystem,
  onChangeUnits,
  onSelectSearchEntry,
}: TopBarProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState<SearchEntry[] | null>(null);
  const [loadingIndex, setLoadingIndex] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const ensureIndex = () => {
    if (index !== null || loadingIndex) return;
    setLoadingIndex(true);
    void loadSearchIndex().then((entries) => {
      setIndex(entries);
      setLoadingIndex(false);
    });
  };

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const results = useMemo(() => {
    if (!index) return [];
    const q = query.trim().toLowerCase();
    const filtered = index.filter((item) => {
      if (activeSource !== null && item.s !== activeSource) return false;
      if (!q) return true;
      return (
        item.n.toLowerCase().includes(q) ||
        item.id.toLowerCase().includes(q) ||
        (item.f && item.f.toLowerCase().includes(q)) ||
        (item.b && item.b.toLowerCase().includes(q))
      );
    });
    return filtered.slice(0, 14);
  }, [index, query, activeSource]);

  return (
    <header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-2.5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-900 text-xs font-bold tracking-tight text-white">
            OG
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-slate-900 leading-none">
              ogprod
            </h1>
            <p className="mt-0.5 text-[11px] text-slate-500 leading-none">
              Public Oil &amp; Gas Explorer
            </p>
          </div>
        </div>

        <nav
          aria-label="Data sources"
          className="flex items-center rounded-lg border border-slate-200 bg-slate-50 p-0.5"
        >
          <button
            id="source-btn-all"
            type="button"
            onClick={() => onSelectSource(null)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              activeSource === null
                ? "bg-white text-slate-900 shadow-xs"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            All
          </button>
          {sources.map((src) => (
            <button
              id={`source-btn-${src.id}`}
              key={src.id}
              type="button"
              onClick={() => onSelectSource(src.id)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                activeSource === src.id
                  ? "bg-white text-slate-900 shadow-xs"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <span>{src.country}</span>
              <span className="ml-1 text-[10px] font-normal text-slate-400">
                {src.country_code}
              </span>
            </button>
          ))}
        </nav>
      </div>

      <div className="flex flex-1 items-center justify-end gap-3 sm:flex-initial">
        <div ref={boxRef} className="relative w-full max-w-xs sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            id="global-search-input"
            type="text"
            value={query}
            onFocus={() => {
              ensureIndex();
              setOpen(true);
            }}
            onChange={(e) => {
              ensureIndex();
              setQuery(e.target.value);
              setOpen(true);
            }}
            placeholder="Search fields, FPSOs, wells…"
            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-7 text-xs text-slate-900 placeholder:text-slate-400 focus:border-cyan-700 focus:bg-white focus:outline-none"
          />
          {query && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}

          {open && (
            <div className="absolute left-0 right-0 top-full z-40 mt-1.5 max-h-80 overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
              {loadingIndex ? (
                <div className="px-3 py-2.5 text-xs text-slate-500">Loading search index…</div>
              ) : results.length === 0 ? (
                <div className="px-3 py-2.5 text-xs text-slate-500">No matching entities.</div>
              ) : (
                results.map((entry) => (
                  <button
                    key={`${entry.t}:${entry.s}:${entry.id}`}
                    type="button"
                    onClick={() => {
                      onSelectSearchEntry(entry);
                      setOpen(false);
                      setQuery(entry.n);
                    }}
                    className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-slate-50"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`inline-block shrink-0 rounded border px-1 py-0.5 text-[9px] font-semibold tracking-wider ${BADGE_CLASSES[entry.t]}`}
                        >
                          {BADGE_LABELS[entry.t]}
                        </span>
                        <span className="truncate text-xs font-medium text-slate-900">
                          {entry.n}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-slate-500">
                        {entry.s.toUpperCase()}
                        {entry.b ? ` · ${entry.b}` : ""}
                        {entry.f && entry.t !== "field" ? ` · ${entry.f}` : ""}
                      </div>
                    </div>
                    {entry.lo !== undefined && entry.lo > 0 && (
                      <span className="font-tabular shrink-0 text-[11px] text-slate-600">
                        {formatCompactNumber(entry.lo, 1)} bbl/d
                      </span>
                    )}
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          <span className="hidden md:inline">Units</span>
          <select
            id="unit-system-select"
            value={unitSystem}
            onChange={(e) => onChangeUnits(e.target.value as UnitSystem)}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 focus:border-cyan-700 focus:outline-none"
          >
            {UNIT_SYSTEMS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
