import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DashboardPane } from "./components/DashboardPane";
import { MapPane } from "./components/MapPane";
import { TopBar } from "./components/TopBar";
import { selectionToPath, trackPageview } from "./lib/analytics";
import {
  loadFacilityMonthlyShard,
  loadFieldMonthlyShard,
  loadInitialBundle,
} from "./lib/dataClient";
import { queryWellSeriesFromMirror, resolveWellQueryPlan } from "./lib/duckdbWell";
import { parseUrlState, serializeUrlState } from "./lib/urlState";
import type {
  ChartSeriesKey,
  EntitySelection,
  FacilitySummary,
  FieldSummary,
  MonthlyPoint,
  SearchEntry,
  SiteMeta,
  SourceFilter,
  UnitSystem,
} from "./types/data";

const PAGEVIEW_DEBOUNCE_MS = 1500;

export function App() {
  const initial = useMemo(() => parseUrlState(window.location.search), []);
  const [activeSource, setActiveSource] = useState<SourceFilter>(initial.source);
  const [selection, setSelection] = useState<EntitySelection>(initial.selection);
  const [activeProducts, setActiveProducts] = useState<ChartSeriesKey[]>(initial.products);
  const [unitSystem, setUnitSystem] = useState<UnitSystem>(initial.units);

  const [meta, setMeta] = useState<SiteMeta | null>(null);
  const [countryMonthly, setCountryMonthly] = useState<Record<string, MonthlyPoint[]>>({});
  const [basinMonthly, setBasinMonthly] = useState<Record<string, Record<string, MonthlyPoint[]>>>(
    {},
  );
  const [fieldsSummary, setFieldsSummary] = useState<FieldSummary[]>([]);
  const [facilitiesSummary, setFacilitiesSummary] = useState<FacilitySummary[]>([]);
  const [bootError, setBootError] = useState<string | null>(null);

  const [series, setSeries] = useState<MonthlyPoint[]>([]);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [wellPartitionsQueried, setWellPartitionsQueried] = useState<number | null>(null);
  const [wellNoSeriesReason, setWellNoSeriesReason] = useState<
    "field_level_source" | "no_well_history" | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    void loadInitialBundle()
      .then((bundle) => {
        if (cancelled) return;
        setMeta(bundle.meta);
        setCountryMonthly(bundle.countryMonthly);
        setBasinMonthly(bundle.basinMonthly);
        setFieldsSummary(bundle.fieldsSummary);
        setFacilitiesSummary(bundle.facilitiesSummary);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setBootError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const qs = serializeUrlState({
      source: activeSource,
      selection,
      products: activeProducts,
      units: unitSystem,
    });
    const nextUrl = `${window.location.pathname}${qs}`;
    if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      window.history.replaceState(null, "", nextUrl);
    }
  }, [activeSource, selection, activeProducts, unitSystem]);

  const pagePath = selectionToPath(selection, activeSource);
  useEffect(() => {
    // Debounced so rapid clicking through the map counts only where the user settles.
    const timer = window.setTimeout(() => trackPageview(pagePath), PAGEVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [pagePath]);

  useEffect(() => {
    if (!meta) return;
    let cancelled = false;
    setSeriesError(null);
    setWellPartitionsQueried(null);
    setWellNoSeriesReason(null);

    if (selection.kind === "overview") {
      setSeries(activeSource === null ? [] : (countryMonthly[activeSource] ?? []));
      setLoadingSeries(false);
      return;
    }
    if (selection.kind === "basin") {
      setSeries(basinMonthly[selection.source]?.[selection.basin] ?? []);
      setLoadingSeries(false);
      return;
    }
    if (selection.kind === "field") {
      setLoadingSeries(true);
      void loadFieldMonthlyShard(selection.source)
        .then((shard) => {
          if (cancelled) return;
          setSeries(shard[selection.id] ?? []);
          setLoadingSeries(false);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setSeriesError(err instanceof Error ? err.message : String(err));
          setLoadingSeries(false);
        });
      return () => {
        cancelled = true;
      };
    }
    if (selection.kind === "facility") {
      setLoadingSeries(true);
      void loadFacilityMonthlyShard(selection.source)
        .then((shard) => {
          if (cancelled) return;
          setSeries(shard[selection.id] ?? []);
          setLoadingSeries(false);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setSeriesError(err instanceof Error ? err.message : String(err));
          setLoadingSeries(false);
        });
      return () => {
        cancelled = true;
      };
    }
    if (selection.kind === "well") {
      const srcMeta = meta.sources.find((s) => s.id === selection.source);
      const plan = resolveWellQueryPlan({
        sourceEntityType: srcMeta?.entity_type,
        firstYear: selection.firstYear,
        lastYear: selection.lastYear,
      });
      if (!plan.canQuery) {
        setSeries([]);
        setWellNoSeriesReason(plan.reason);
        setLoadingSeries(false);
        return;
      }
      setLoadingSeries(true);
      void queryWellSeriesFromMirror({
        mirrorBaseUrl: meta.mirror.base_url,
        source: selection.source,
        wellId: selection.id,
        firstYear: plan.firstYear,
        lastYear: plan.lastYear,
        availableYears: srcMeta?.years,
      })
        .then((res) => {
          if (cancelled) return;
          setSeries(res.rows);
          setWellPartitionsQueried(res.partitionsQueried);
          if (res.rows.length === 0) {
            setWellNoSeriesReason("no_well_history");
          }
          setLoadingSeries(false);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setSeriesError(err instanceof Error ? err.message : String(err));
          setLoadingSeries(false);
        });
      return () => {
        cancelled = true;
      };
    }
  }, [meta, activeSource, selection, countryMonthly, basinMonthly]);

  const handleSelectSource = useCallback((source: SourceFilter) => {
    setActiveSource(source);
    setSelection({ kind: "overview" });
    if (source === null) {
      setActiveProducts((prev) => [prev[0] ?? "oil_prod"]);
    }
  }, []);

  const handleSelectEntity = useCallback((next: EntitySelection) => {
    setSelection(next);
    if (next.kind !== "overview") {
      setActiveSource(next.source);
    }
  }, []);

  const handleSelectSearchEntry = useCallback((entry: SearchEntry) => {
    setActiveSource(entry.s);
    if (entry.t === "field") {
      setSelection({
        kind: "field",
        source: entry.s,
        id: entry.id,
        name: entry.n,
        lon: entry.lon,
        lat: entry.lat,
      });
    } else if (entry.t === "facility") {
      setSelection({
        kind: "facility",
        source: entry.s,
        id: entry.id,
        name: entry.n,
        lon: entry.lon,
        lat: entry.lat,
      });
    } else {
      setSelection({
        kind: "well",
        source: entry.s,
        id: entry.id,
        name: entry.n,
        fieldName: entry.f,
        basin: entry.b,
        firstYear: entry.fy,
        lastYear: entry.ly,
        lon: entry.lon,
        lat: entry.lat,
      });
    }
  }, []);

  const isAllSourcesOverview = activeSource === null && selection.kind === "overview";

  const handleToggleProduct = useCallback(
    (col: ChartSeriesKey) => {
      if (isAllSourcesOverview) {
        setActiveProducts([col]);
        return;
      }
      setActiveProducts((prev) => {
        if (prev.includes(col)) {
          return prev.length > 1 ? prev.filter((c) => c !== col) : prev;
        }
        return [...prev, col];
      });
    },
    [isAllSourcesOverview],
  );

  if (bootError) {
    return (
      <main className="flex h-screen flex-col items-center justify-center bg-[#f4f5f7] p-6 text-center">
        <h1 className="text-lg font-semibold text-slate-900">ogprod data bundle not found</h1>
        <p className="mt-2 max-w-md text-xs text-slate-600">{bootError}</p>
        <p className="mt-3 rounded bg-slate-200/70 px-3 py-1.5 font-mono text-xs text-slate-800">
          uv run ogprod export-web --sync-mirror --out-dir site/public/data
        </p>
      </main>
    );
  }

  if (!meta) {
    return (
      <main className="flex h-screen items-center justify-center bg-[#f4f5f7] text-xs text-slate-500">
        <Loader2 className="mr-2 h-4 w-4 animate-spin text-cyan-700" />
        Loading ogprod explorer…
      </main>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#f4f5f7]">
      <TopBar
        sources={meta.sources}
        activeSource={activeSource}
        onSelectSource={handleSelectSource}
        unitSystem={unitSystem}
        onChangeUnits={setUnitSystem}
        onSelectSearchEntry={handleSelectSearchEntry}
      />

      <main className="grid flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1.15fr)_minmax(420px,0.95fr)] lg:overflow-hidden">
        <MapPane
          meta={meta}
          activeSource={activeSource}
          selection={selection}
          fieldsSummary={fieldsSummary}
          facilitiesSummary={facilitiesSummary}
          onSelectEntity={handleSelectEntity}
        />

        <DashboardPane
          meta={meta}
          activeSource={activeSource}
          selection={selection}
          series={series}
          countryMonthly={countryMonthly}
          loadingSeries={loadingSeries}
          seriesError={seriesError}
          wellPartitionsQueried={wellPartitionsQueried}
          wellNoSeriesReason={wellNoSeriesReason}
          activeProducts={activeProducts}
          onToggleProduct={handleToggleProduct}
          unitSystem={unitSystem}
          fieldsSummary={fieldsSummary}
          facilitiesSummary={facilitiesSummary}
          basinMonthly={basinMonthly}
          onSelectEntity={handleSelectEntity}
        />
      </main>
    </div>
  );
}
export default App;
