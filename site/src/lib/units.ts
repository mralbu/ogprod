import type { RateColumn, UnitSystem } from "../types/data";

/** Exact ISO conversion constants matching src/ogprod/units.py. */
export const M3_PER_BBL = 0.158987294928;
export const M3_PER_SCF = 0.028316846592;
/** 1 e3m3 (1,000 m³) in MMcf (1,000,000 scf). */
export const MMCF_PER_E3M3 = 1_000 / (1_000_000 * M3_PER_SCF);

export interface ProductSpec {
  column: RateColumn;
  label: string;
  shortLabel: string;
  phase: "liquid" | "gas";
  category: "production" | "injection";
  color: string;
}

export const PRODUCT_SPECS: Record<RateColumn, ProductSpec> = {
  oil_prod: {
    column: "oil_prod",
    label: "Crude Oil",
    shortLabel: "Oil",
    phase: "liquid",
    category: "production",
    color: "#c26d0a",
  },
  gas_prod: {
    column: "gas_prod",
    label: "Natural Gas",
    shortLabel: "Gas",
    phase: "gas",
    category: "production",
    color: "#0284c7",
  },
  water_prod: {
    column: "water_prod",
    label: "Produced Water",
    shortLabel: "Water",
    phase: "liquid",
    category: "production",
    color: "#0d9488",
  },
  condensate_prod: {
    column: "condensate_prod",
    label: "Condensate",
    shortLabel: "Cond",
    phase: "liquid",
    category: "production",
    color: "#d97706",
  },
  ngl_prod: {
    column: "ngl_prod",
    label: "NGL",
    shortLabel: "NGL",
    phase: "liquid",
    category: "production",
    color: "#65a30d",
  },
  water_inj: {
    column: "water_inj",
    label: "Water Injection",
    shortLabel: "Water Inj",
    phase: "liquid",
    category: "injection",
    color: "#0369a1",
  },
  gas_inj: {
    column: "gas_inj",
    label: "Gas Injection",
    shortLabel: "Gas Inj",
    phase: "gas",
    category: "injection",
    color: "#ea580c",
  },
  co2_inj: {
    column: "co2_inj",
    label: "CO₂ Injection",
    shortLabel: "CO₂ Inj",
    phase: "gas",
    category: "injection",
    color: "#475569",
  },
};

export interface UnitSystemMeta {
  id: UnitSystem;
  label: string;
  liquidUnit: string;
  gasUnit: string;
}

export const UNIT_SYSTEMS: readonly UnitSystemMeta[] = [
  {
    id: "oilfield_e3m3",
    label: "bbl/d & 10³ m³/d",
    liquidUnit: "bbl/d",
    gasUnit: "10³ m³/d",
  },
  {
    id: "oilfield_mmcf",
    label: "bbl/d & MMcf/d",
    liquidUnit: "bbl/d",
    gasUnit: "MMcf/d",
  },
  {
    id: "metric_m3",
    label: "m³/d",
    liquidUnit: "m³/d",
    gasUnit: "10³ m³/d",
  },
] as const;

export function unitLabelFor(column: RateColumn, unitSystem: UnitSystem): string {
  const phase = PRODUCT_SPECS[column].phase;
  const meta = UNIT_SYSTEMS.find((u) => u.id === unitSystem) ?? UNIT_SYSTEMS[0];
  return phase === "liquid" ? meta.liquidUnit : meta.gasUnit;
}

/** Convert a canonical value (liquids in bbl/d, gases in e3m3/d) to the target unit system. */
export function convertRate(
  value: number | null | undefined,
  column: RateColumn,
  unitSystem: UnitSystem,
): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null;
  }
  const phase = PRODUCT_SPECS[column].phase;
  if (phase === "liquid") {
    return unitSystem === "metric_m3" ? value * M3_PER_BBL : value;
  }
  if (unitSystem === "oilfield_mmcf") {
    return value * MMCF_PER_E3M3;
  }
  return value;
}

export function formatCompactNumber(value: number | null | undefined, decimals = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(decimals)}M`;
  }
  if (abs >= 1_000) {
    return `${(value / 1_000).toFixed(decimals)}k`;
  }
  return value.toFixed(decimals);
}

export function formatPercent(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) {
    return "—";
  }
  return `${(ratio * 100).toFixed(1)}%`;
}
