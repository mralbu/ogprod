import type {
  ChartSeriesKey,
  DerivedSeriesKey,
  MonthlyPoint,
  RateColumn,
  UnitSystem,
} from "../types/data";
import { isDerivedSeries } from "../types/data";

/** Exact ISO conversion constants matching src/ogprod/units.py. */
export const M3_PER_BBL = 0.158987294928;
export const M3_PER_SCF = 0.028316846592;
/** 1 e3m3 (1,000 m³) in scf. */
export const SCF_PER_E3M3 = 1_000 / M3_PER_SCF;
/** 1 e3m3 (1,000 m³) in MMcf (1,000,000 scf). */
export const MMCF_PER_E3M3 = SCF_PER_E3M3 / 1_000_000;

export type AxisGroup = "liquid" | "gas" | "gor" | "water_cut";

export interface ProductSpec {
  column: RateColumn;
  label: string;
  shortLabel: string;
  phase: "liquid" | "gas";
  category: "production" | "injection";
  color: string;
}

export interface DerivedSeriesSpec {
  key: DerivedSeriesKey;
  label: string;
  shortLabel: string;
  axisGroup: "gor" | "water_cut";
  category: "ratio";
  color: string;
  dependsOn: readonly RateColumn[];
}

export interface ChartSeriesSpec {
  key: ChartSeriesKey;
  label: string;
  shortLabel: string;
  axisGroup: AxisGroup;
  category: "production" | "injection" | "ratio";
  color: string;
  dependsOn?: readonly RateColumn[];
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

export const DERIVED_SPECS: Record<DerivedSeriesKey, DerivedSeriesSpec> = {
  gor: {
    key: "gor",
    label: "Gas–Oil Ratio (GOR)",
    shortLabel: "GOR",
    axisGroup: "gor",
    category: "ratio",
    color: "#7c3aed",
    dependsOn: ["oil_prod", "gas_prod"],
  },
  water_cut: {
    key: "water_cut",
    label: "Water Cut",
    shortLabel: "WCUT",
    axisGroup: "water_cut",
    category: "ratio",
    color: "#db2777",
    dependsOn: ["oil_prod", "water_prod"],
  },
};

export function getSeriesSpec(key: ChartSeriesKey): ChartSeriesSpec {
  if (isDerivedSeries(key)) {
    return DERIVED_SPECS[key];
  }
  const spec = PRODUCT_SPECS[key];
  return {
    key: spec.column,
    label: spec.label,
    shortLabel: spec.shortLabel,
    axisGroup: spec.phase,
    category: spec.category,
    color: spec.color,
  };
}

export interface UnitSystemMeta {
  id: UnitSystem;
  label: string;
  liquidUnit: string;
  gasUnit: string;
  gorUnit: string;
}

export const UNIT_SYSTEMS: readonly UnitSystemMeta[] = [
  {
    id: "oilfield_e3m3",
    label: "bbl/d & 10³ m³/d",
    liquidUnit: "bbl/d",
    gasUnit: "10³ m³/d",
    gorUnit: "m³/m³",
  },
  {
    id: "oilfield_mmcf",
    label: "bbl/d & MMcf/d",
    liquidUnit: "bbl/d",
    gasUnit: "MMcf/d",
    gorUnit: "scf/bbl",
  },
  {
    id: "metric_m3",
    label: "m³/d",
    liquidUnit: "m³/d",
    gasUnit: "10³ m³/d",
    gorUnit: "m³/m³",
  },
] as const;

export function unitLabelFor(column: ChartSeriesKey, unitSystem: UnitSystem): string {
  const meta = UNIT_SYSTEMS.find((u) => u.id === unitSystem) ?? UNIT_SYSTEMS[0];
  if (column === "gor") {
    return meta.gorUnit;
  }
  if (column === "water_cut") {
    return "%";
  }
  return PRODUCT_SPECS[column].phase === "liquid" ? meta.liquidUnit : meta.gasUnit;
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

/**
 * Compute Gas–Oil Ratio from canonical daily rates (`oil_prod` in bbl/d, `gas_prod` in e3m3/d).
 * Returns `scf/bbl` under `oilfield_mmcf` and `m³/m³` under `oilfield_e3m3` and `metric_m3`.
 */
export function computeGor(
  oilBpd: number | null | undefined,
  gasE3m3d: number | null | undefined,
  unitSystem: UnitSystem,
): number | null {
  if (
    oilBpd === null ||
    oilBpd === undefined ||
    !Number.isFinite(oilBpd) ||
    oilBpd <= 0 ||
    gasE3m3d === null ||
    gasE3m3d === undefined ||
    !Number.isFinite(gasE3m3d) ||
    gasE3m3d < 0
  ) {
    return null;
  }
  if (unitSystem === "oilfield_mmcf") {
    return (gasE3m3d * SCF_PER_E3M3) / oilBpd;
  }
  return (gasE3m3d * 1_000) / (oilBpd * M3_PER_BBL);
}

/**
 * Compute Water Cut as a percentage (`0..100`) from canonical daily rates (`bbl/d`):
 * `water / (oil + water) * 100`. Returns `null` when either rate is missing or total liquid is 0.
 */
export function computeWaterCut(
  oilBpd: number | null | undefined,
  waterBpd: number | null | undefined,
): number | null {
  if (
    oilBpd === null ||
    oilBpd === undefined ||
    !Number.isFinite(oilBpd) ||
    oilBpd < 0 ||
    waterBpd === null ||
    waterBpd === undefined ||
    !Number.isFinite(waterBpd) ||
    waterBpd < 0
  ) {
    return null;
  }
  const totalLiquid = oilBpd + waterBpd;
  if (totalLiquid <= 0) {
    return null;
  }
  return (waterBpd / totalLiquid) * 100;
}

/** Resolve any chart series value (converted rate or derived ratio) for one monthly row. */
export function computeSeriesValue(
  row: MonthlyPoint,
  key: ChartSeriesKey,
  unitSystem: UnitSystem,
): number | null {
  if (key === "gor") {
    return computeGor(row.oil_prod, row.gas_prod, unitSystem);
  }
  if (key === "water_cut") {
    return computeWaterCut(row.oil_prod, row.water_prod);
  }
  return convertRate(row[key], key, unitSystem);
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
