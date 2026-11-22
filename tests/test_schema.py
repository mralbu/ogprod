"""Tests for canonical production and geospatial schemas."""

from __future__ import annotations

from datetime import date

import geopandas as gpd
import pyarrow as pa
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st

from ogprod import schema


@given(text=st.text(max_size=80))
def test_normalize_key_is_idempotent_and_ascii(text: str) -> None:
    norm = schema.normalize_key(text)
    assert norm == schema.normalize_key(norm)
    assert norm.isascii()
    assert norm == norm.upper()
    assert "  " not in norm


def test_normalize_key_accents_and_none() -> None:
    assert schema.normalize_key("Búzios  Sul") == "BUZIOS SUL"
    assert schema.normalize_key("  FPSO  Almirante Tamandaré ") == "FPSO ALMIRANTE TAMANDARE"
    assert schema.normalize_key(None) == ""


def _valid_production_table(**overrides: object) -> pa.Table:
    cols: dict[str, object] = {
        "source": ["brazil"],
        "entity_type": ["well"],
        "entity_id": ["7-TPI-1-RJS"],
        "entity_name": ["7-TPI-1-RJS"],
        "field_id": ["TUPI"],
        "field_name": ["TUPI"],
        "facility_id": ["FPSO CIDADE DE ANGRA DOS REIS"],
        "facility_name": ["FPSO CIDADE DE ANGRA DOS REIS"],
        "basin": ["Santos"],
        "region": ["Rio de Janeiro"],
        "environment": ["offshore"],
        "operator": ["Petrobras"],
        "period": pa.array([date(2026, 1, 1)], type=pa.date32()),
        "oil_prod": pa.array([25000.0], type=pa.float32()),
        "condensate_prod": pa.array([0.0], type=pa.float32()),
        "ngl_prod": pa.array([None], type=pa.float32()),
        "gas_prod": pa.array([1.25], type=pa.float32()),
        "water_prod": pa.array([1500.0], type=pa.float32()),
        "water_inj": pa.array([0.0], type=pa.float32()),
        "gas_inj": pa.array([0.5], type=pa.float32()),
        "co2_inj": pa.array([0.2], type=pa.float32()),
    }
    cols.update(overrides)
    return pa.table(cols)


def test_validate_production_preserves_float32_and_unit_metadata() -> None:
    table = schema.validate_production(_valid_production_table())
    for col, expected_unit in schema.RATE_UNITS.items():
        field = table.schema.field(col)
        assert field.type == pa.float32()
        assert field.metadata is not None
        assert field.metadata[b"unit"] == expected_unit.encode()


def test_validate_production_rejects_bad_day_duplicates_and_float64() -> None:
    with pytest.raises(schema.SchemaError, match="first day"):
        schema.validate_production(
            _valid_production_table(period=pa.array([date(2026, 1, 15)], type=pa.date32()))
        )

    with pytest.raises(schema.SchemaError, match="float"):
        schema.validate_production(
            _valid_production_table(oil_prod=pa.array([25000.0], type=pa.float64()))
        )

    one = _valid_production_table()
    dup = pa.concat_tables([one, one])
    with pytest.raises(schema.SchemaError, match="duplicate"):
        schema.validate_production(dup)


def test_validate_geo_rejects_wrong_geometry_crs_and_bounds() -> None:
    bad_geom = schema.empty_geo(schema.GeoKind.WELLS)
    bad_geom.loc[0, ["source", "well_id", "well_name"]] = ["brazil", "W1", "W1"]
    bad_geom.loc[0, "geometry"] = shapely.box(-42, -24, -41, -23)
    with pytest.raises(schema.SchemaError, match="geometry must be"):
        schema.validate_geo(bad_geom, schema.GeoKind.WELLS)

    out_of_bounds = schema.empty_geo(schema.GeoKind.FACILITIES)
    out_of_bounds.loc[0, ["source", "facility_id", "facility_name"]] = ["brazil", "F1", "F1"]
    out_of_bounds.loc[0, "geometry"] = shapely.Point(500000.0, 7500000.0)
    with pytest.raises(schema.SchemaError, match="outside lon/lat bounds"):
        schema.validate_geo(out_of_bounds, schema.GeoKind.FACILITIES)

    wrong_crs = gpd.GeoDataFrame(
        {"source": ["brazil"], "well_id": ["W1"], "well_name": ["W1"]},
        geometry=[shapely.Point(-42.0, -24.0)],
        crs="EPSG:3857",
    )
    for col in schema.GEO_SPECS[schema.GeoKind.WELLS].columns:
        if col not in wrong_crs.columns:
            wrong_crs[col] = None
    with pytest.raises(schema.SchemaError, match="EPSG:4326"):
        schema.validate_geo(
            wrong_crs[list(schema.GEO_SPECS[schema.GeoKind.WELLS].columns)], schema.GeoKind.WELLS
        )
