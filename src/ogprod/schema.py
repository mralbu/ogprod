"""Canonical schemas and validators for ogprod v2.

Production and injection data live in a single wide PyArrow schema with
``float32`` calendar-day average rates (``bbl/d`` for liquids, ``e6m3/d`` =
SI ``Mm³/d`` for gases) and unit metadata on every rate field. Spatial entities
are separated into three typed GeoParquet 1.1 tables: ``fields`` (polygons),
``facilities`` (points — platforms/FPSOs), and ``wells`` (points).
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import pyarrow as pa

from ogprod.units import GAS_RATE_UNIT, LIQUID_RATE_UNIT


class SchemaError(ValueError):
    """Raised when a production table or GeoDataFrame violates the canonical schema."""


class EntityType(enum.StrEnum):
    """Reporting granularity of a production row."""

    WELL = "well"
    FIELD = "field"


class GeoKind(enum.StrEnum):
    """The three canonical geospatial entity tables."""

    FIELDS = "fields"
    FACILITIES = "facilities"
    WELLS = "wells"


#: CRS enforced across every GeoParquet table.
GEO_CRS = "EPSG:4326"

#: Canonical rate columns and their storage units (written into Parquet field metadata).
RATE_UNITS: dict[str, str] = {
    "oil_prod": LIQUID_RATE_UNIT,
    "condensate_prod": LIQUID_RATE_UNIT,
    "ngl_prod": LIQUID_RATE_UNIT,
    "gas_prod": GAS_RATE_UNIT,
    "water_prod": LIQUID_RATE_UNIT,
    "water_inj": LIQUID_RATE_UNIT,
    "gas_inj": GAS_RATE_UNIT,
    "co2_inj": GAS_RATE_UNIT,
}

RATE_COLUMNS: tuple[str, ...] = tuple(RATE_UNITS)

PRODUCTION_KEY: tuple[str, ...] = (
    "source",
    "entity_type",
    "entity_id",
    "field_id",
    "facility_id",
    "period",
)

PRODUCTION_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("source", pa.string(), nullable=False),
        pa.field("entity_type", pa.string(), nullable=False),
        pa.field("entity_id", pa.string(), nullable=False),
        pa.field("entity_name", pa.string(), nullable=False),
        pa.field("field_id", pa.string(), nullable=False),
        pa.field("field_name", pa.string(), nullable=True),
        pa.field("facility_id", pa.string(), nullable=False),
        pa.field("facility_name", pa.string(), nullable=True),
        pa.field("basin", pa.string(), nullable=True),
        pa.field("region", pa.string(), nullable=True),
        pa.field("environment", pa.string(), nullable=True),
        pa.field("operator", pa.string(), nullable=True),
        pa.field("period", pa.date32(), nullable=False),
        *[
            pa.field(name, pa.float32(), nullable=True, metadata={b"unit": unit.encode()})
            for name, unit in RATE_UNITS.items()
        ],
    ]
)

PRODUCTION_COLUMNS: tuple[str, ...] = tuple(PRODUCTION_SCHEMA.names)


@dataclass(frozen=True)
class GeoTableSpec:
    """Column layout and invariants for one canonical geospatial table."""

    kind: GeoKind
    id_column: str
    columns: tuple[str, ...]
    float32_columns: tuple[str, ...]
    int32_columns: tuple[str, ...]
    geometry_types: frozenset[str]


GEO_SPECS: dict[GeoKind, GeoTableSpec] = {
    GeoKind.FIELDS: GeoTableSpec(
        kind=GeoKind.FIELDS,
        id_column="field_id",
        columns=(
            "source",
            "field_id",
            "field_code",
            "field_name",
            "basin",
            "region",
            "environment",
            "operator",
            "status",
            "primary_fluid",
            "discovery_date",
            "production_start_date",
            "water_depth_m",
            "area_km2",
            "geometry",
        ),
        float32_columns=("water_depth_m", "area_km2"),
        int32_columns=(),
        geometry_types=frozenset({"Polygon", "MultiPolygon"}),
    ),
    GeoKind.FACILITIES: GeoTableSpec(
        kind=GeoKind.FACILITIES,
        id_column="facility_id",
        columns=(
            "source",
            "facility_id",
            "facility_code",
            "facility_name",
            "facility_type",
            "fields",
            "basin",
            "operator",
            "status",
            "start_year",
            "water_depth_m",
            "oil_capacity_bpd",
            "gas_capacity_mm3d",
            "export_system",
            "geometry",
        ),
        float32_columns=("water_depth_m", "oil_capacity_bpd", "gas_capacity_mm3d"),
        int32_columns=("start_year",),
        geometry_types=frozenset({"Point"}),
    ),
    GeoKind.WELLS: GeoTableSpec(
        kind=GeoKind.WELLS,
        id_column="well_id",
        columns=(
            "source",
            "well_id",
            "well_name",
            "field_id",
            "field_name",
            "basin",
            "region",
            "environment",
            "operator",
            "status",
            "lift_method",
            "spud_date",
            "completion_date",
            "production_start",
            "production_end",
            "geometry",
        ),
        float32_columns=(),
        int32_columns=(),
        geometry_types=frozenset({"Point"}),
    ),
}

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_key(value: str | None) -> str:
    """Normalize a free-text identifier into an ASCII uppercase join key.

    Strips accents, collapses whitespace, and uppercases so names like
    ``"Búzios"`` and ``"BUZIOS"`` join reliably. Returns ``""`` for null/blank.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return _WHITESPACE_RE.sub(" ", text).strip().upper()


def empty_production() -> pa.Table:
    """Return an empty PyArrow Table conforming to :data:`PRODUCTION_SCHEMA`."""
    arrays = [pa.array([], type=field.type) for field in PRODUCTION_SCHEMA]
    return pa.Table.from_arrays(arrays, schema=PRODUCTION_SCHEMA)


def empty_geo(kind: GeoKind) -> gpd.GeoDataFrame:
    """Return an empty GeoDataFrame conforming to the spec for ``kind``."""
    spec = GEO_SPECS[kind]
    data: dict[str, pd.Series] = {}
    for col in spec.columns:
        if col == "geometry":
            continue
        if col in spec.float32_columns:
            data[col] = pd.Series(dtype="float32")
        elif col in spec.int32_columns:
            data[col] = pd.Series(dtype="Int32")
        else:
            data[col] = pd.Series(dtype="string")
    geometry = gpd.GeoSeries([], name="geometry", crs=GEO_CRS)
    return gpd.GeoDataFrame(pd.DataFrame(data), geometry=geometry)[list(spec.columns)]


def _check_production_columns(table: pa.Table) -> None:
    missing = [name for name in PRODUCTION_COLUMNS if name not in table.column_names]
    if missing:
        raise SchemaError(f"missing production columns: {missing}")
    extra = [name for name in table.column_names if name not in PRODUCTION_COLUMNS]
    if extra:
        raise SchemaError(f"unexpected production columns: {extra}")


def _check_production_types_and_nulls(table: pa.Table) -> None:
    for field in PRODUCTION_SCHEMA:
        actual = table.schema.field(field.name)
        if actual.type != field.type:
            raise SchemaError(
                f"column {field.name!r} must have type {field.type}, got {actual.type}"
            )
        if not field.nullable and table.column(field.name).null_count > 0:
            raise SchemaError(f"column {field.name!r} must not contain nulls")


def _check_production_values(table: pa.Table) -> None:
    allowed_types = {e.value for e in EntityType}
    seen_types = set(table.column("entity_type").unique().to_pylist())
    bad_types = seen_types - allowed_types
    if bad_types:
        raise SchemaError(f"invalid entity_type values: {sorted(bad_types)}")

    if any(d.day != 1 for d in table.column("period").to_pylist()):
        raise SchemaError("'period' values must be the first day of the month (day == 1)")

    # Enforce key uniqueness within a single validated table.
    key_frame = table.select(list(PRODUCTION_KEY)).to_pandas()
    duplicated = key_frame.duplicated(keep=False)
    if duplicated.any():
        sample = key_frame.loc[duplicated].head(5).astype(str).to_dict("records")
        raise SchemaError(f"duplicate {PRODUCTION_KEY} rows: {sample}")


def validate_production(table: pa.Table) -> pa.Table:
    """Validate a production/injection Arrow table against :data:`PRODUCTION_SCHEMA`.

    Returns the table cast to the exact canonical column order and field metadata.
    """
    _check_production_columns(table)
    ordered = table.select(list(PRODUCTION_COLUMNS))
    _check_production_types_and_nulls(ordered)
    if ordered.num_rows > 0:
        _check_production_values(ordered)
    return ordered.cast(PRODUCTION_SCHEMA)


def _check_geo_columns_and_crs(gdf: gpd.GeoDataFrame, spec: GeoTableSpec) -> None:
    if not isinstance(gdf, gpd.GeoDataFrame):
        raise SchemaError("spatial table must be a geopandas.GeoDataFrame")
    missing = [col for col in spec.columns if col not in gdf.columns]
    if missing:
        raise SchemaError(f"missing {spec.kind.value} columns: {missing}")
    extra = [col for col in gdf.columns if col not in spec.columns]
    if extra:
        raise SchemaError(f"unexpected {spec.kind.value} columns: {extra}")
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        raise SchemaError(f"{spec.kind.value} must be in {GEO_CRS}, got {gdf.crs}")


def _check_geo_keys_and_geometries(gdf: gpd.GeoDataFrame, spec: GeoTableSpec) -> None:
    if gdf["source"].isna().any():
        raise SchemaError(f"{spec.kind.value}: 'source' must not contain nulls")
    if gdf[spec.id_column].isna().any() or (gdf[spec.id_column].astype(str) == "").any():
        raise SchemaError(f"{spec.kind.value}: {spec.id_column!r} must not be null or empty")

    dupes = gdf.duplicated(subset=["source", spec.id_column])
    if dupes.any():
        sample = gdf.loc[dupes, spec.id_column].astype(str).head(5).tolist()
        raise SchemaError(f"duplicate (source, {spec.id_column}) in {spec.kind.value}: {sample}")

    non_null = gdf.geometry.dropna()
    if len(non_null) == 0:
        return
    bad_geoms = set(non_null.geom_type.unique()) - spec.geometry_types
    if bad_geoms:
        raise SchemaError(
            f"{spec.kind.value} geometry must be {sorted(spec.geometry_types)}, "
            f"got {sorted(bad_geoms)}"
        )
    bounds = non_null.bounds
    out_of_range = (
        (bounds["minx"] < -180.0)
        | (bounds["maxx"] > 180.0)
        | (bounds["miny"] < -90.0)
        | (bounds["maxy"] > 90.0)
    )
    if out_of_range.any():
        bad_ids = gdf.loc[non_null.index[out_of_range], spec.id_column].astype(str).head(5).tolist()
        raise SchemaError(
            f"{spec.kind.value} geometry outside lon/lat bounds (projected coords?): {bad_ids}"
        )


def validate_geo(gdf: gpd.GeoDataFrame, kind: GeoKind) -> gpd.GeoDataFrame:
    """Validate a spatial GeoDataFrame for ``kind`` and return canonical column order."""
    spec = GEO_SPECS[kind]
    _check_geo_columns_and_crs(gdf, spec)
    ordered = gdf[list(spec.columns)]
    if len(ordered) > 0:
        _check_geo_keys_and_geometries(ordered, spec)
    return ordered
