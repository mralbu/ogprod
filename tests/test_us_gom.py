"""Tests for the US Federal Offshore Gulf of Mexico (BSEE / BOEM OGOR-A) connector."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import httpx2
import numpy as np
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st

from ogprod import audit, export_web, schema, sources, store, units
from ogprod.sources import us_gom

DATA_DIR = Path(__file__).parent / "data"

_SAMPLE_OGORA_HTML = """
<html><body>
<a href="/Production/Files/ogora1947-1979delimit.zip">1947-1979</a>
<a href="/Production/Files/ogora1990-1995delimit.zip">1990-1995</a>
<a href="/Production/Files/ogora1996delimit.zip">1996</a>
<a href="/Production/Files/ogora2023delimit.zip">2023</a>
<a href="/Production/Files/ogora2024delimit.zip">2024</a>
<a href="/Production/Files/ogora2025delimit.zip">2025</a>
<a href="/Production/Files/ogoradelimit.zip">Current</a>
</body></html>
"""

_SAMPLE_MASTDATA = "\n".join(
    [
        '"MC807","G07962","MC","  806","874807","SHELL OFFSHORE INC.","MAY-1992","","",""',
        '"MC807","G07963","MC","  807","874807","SHELL OFFSHORE INC.","APR-1992","","",""',
        (
            '"GC743","G15607","GC","  743","846743",'
            '"BP EXPLORATION & PRODUCTION INC.","JUL-1995","","",""'
        ),
        '"WD030","00016","WD","   31","815030","COX OPERATING LLC","JAN-1970","MAR-2020","T",""',
    ]
)

_SAMPLE_APPENDC = "\n".join(
    [
        '"MARS-URSA","MC807"',
        '"ATLANTIS","GC743"',
    ]
)

_SAMPLE_PLATSTRUC = "\n".join(
    [
        (
            "AREA_CODE,BLOCK_NUMBER,FIELD_NAME_CODE,STRUCTURE_NAME,STRUCTURE_NUMBER,"
            "STRUC_TYPE_CODE,BUS_ASC_NAME,COMPLEX_ID_NUM,MAJ_STRUC_FLAG,INSTALL_DATE,"
            "REMOVAL_DATE,WATER_DEPTH,LATITUDE,LONGITUDE,NAD_YEAR_CD"
        ),
        (
            "MC,807,MC807,A (Mars),1,TLP,Shell Offshore Inc.,24001,Y,"
            "5/15/1996,,2940,28.1665,-89.2231,27"
        ),
        (
            "WD,31,WD030,B,1,FIXED,Cox Operating LLC,10155,N,"
            "6/1/1980,9/24/2000,120,29.0500,-89.5500,27"
        ),
        "GC,743,WILD,C,1,SPAR,BP Exploration,25002,Y,3/10/2007,,7070,,,27",
    ]
)

_SAMPLE_BOREHOLES = "\n".join(
    [
        (
            "API_WELL_NUMBER,WELL_NAME,WELL_NAME_SUFFIX,BOTM_AREA_CODE,COMPANY_NAME,"
            "WELL_SPUD_DATE,TOTAL_DEPTH_DATE,BOREHOLE_STAT_CD,SURF_LATITUDE,"
            "SURF_LONGITUDE,REGION_CODE"
        ),
        (
            "608174010000,A001,ST00BP00,MC,Shell Offshore Inc.,"
            "1/15/1996,4/20/1996,COM,28.1665,-89.2231,G"
        ),
        "608114020000,B003,,GC,BP Exploration,5/1/2006,9/10/2006,COM,27.1950,-90.0270,G",
        "560112000100,P001,,SM,Pacific Operator,1/1/1985,3/1/1985,PA,34.4000,-120.1000,P",
    ]
)


def _write_sample_blocks_zip(dest: Path) -> Path:
    gdf = gpd.GeoDataFrame(
        {
            "AREA_CODE": ["MC", "MC", "GC"],
            "BLOCK_NUMB": ["806", "807", "743"],
        },
        geometry=[
            shapely.box(-89.30, 28.12, -89.25, 28.17),
            shapely.box(-89.25, 28.12, -89.20, 28.17),
            shapely.box(-90.06, 27.16, -90.01, 27.21),
        ],
        crs="EPSG:4267",
    )
    shp_dir = dest.parent / "blk_clip"
    shp_dir.mkdir(exist_ok=True)
    shp_path = shp_dir / "blk_clip.shp"
    gdf.to_file(shp_path)
    with zipfile.ZipFile(dest, "w") as zf:
        for member in shp_dir.iterdir():
            zf.write(member, arcname=f"blk_clip/{member.name}")
    return dest


def _zip_bytes(member_name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, text.encode("latin1"))
    return buf.getvalue()


def test_connector_registered() -> None:
    assert "us-gom" in sources.available()
    conn = sources.get("us-gom")
    assert conn.name == "us-gom"
    assert conn.country == "US (GoM)"
    assert conn.country_code == "US"
    assert conn.entity_type is schema.EntityType.WELL
    assert conn.web_wells is True
    assert conn.search_all_wells is False


def test_discover_production_filters() -> None:
    transport = httpx2.MockTransport(
        lambda req: httpx2.Response(200, text=_SAMPLE_OGORA_HTML, request=req)
    )
    conn = sources.get("us-gom")
    with httpx2.Client(transport=transport) as client:
        all_files = conn.discover_production(client=client)
        assert [f.key for f in all_files] == [
            "ogora_1996",
            "ogora_2023",
            "ogora_2024",
            "ogora_2025",
            "ogora_current",
        ]
        only_2024 = conn.discover_production(client=client, year=2024)
        assert [f.key for f in only_2024] == ["ogora_2024"]
        recent_files = conn.discover_production(client=client, recent=True)
        assert [f.key for f in recent_files] == ["ogora_2024", "ogora_2025", "ogora_current"]
        assert conn.discover_production(client=client, environment="onshore") == []


def test_parse_production_sample_and_zip(tmp_path: Path) -> None:
    sample_path = DATA_DIR / "bsee_ogora_sample.txt"
    table = us_gom.parse_production(sample_path)
    df = table.to_pandas().sort_values(["entity_id", "period"]).reset_index(drop=True)

    # Zero-volume and injection-only rows are dropped; two completions on MC807 are summed.
    assert len(df) == 2

    atl = df.loc[df["entity_id"] == "608114020000"].iloc[0]
    assert atl["source"] == "us-gom"
    assert atl["entity_type"] == "well"
    assert atl["field_id"] == "GC743"
    assert atl["region"] == "Green Canyon"
    assert atl["environment"] == "offshore"
    assert atl["period"] == date(2024, 1, 1)
    assert atl["oil_prod"] == pytest.approx(31_000.0 / 31.0, rel=1e-5)
    assert atl["gas_prod"] == pytest.approx(
        62_000.0 * units.volume_factor("mcf", "e3m3") / 31.0, rel=1e-5
    )
    assert atl["water_prod"] == pytest.approx(0.0)

    mars = df.loc[df["entity_id"] == "608174010000"].iloc[0]
    assert mars["field_id"] == "MC807"
    assert mars["region"] == "Mississippi Canyon"
    assert mars["period"] == date(2024, 2, 1)
    # 2024 is a leap year (29 days): (58,000 + 29,000) / 29 = 3,000 bbl/d.
    assert mars["oil_prod"] == pytest.approx(3_000.0, rel=1e-5)
    assert mars["water_prod"] == pytest.approx((5_800.0 + 2_900.0) / 29.0, rel=1e-5)

    # Also verify ZIP archive without .zip extension (as stored in temp download dir) and BinaryIO.
    zip_path = tmp_path / "ogora_2024"
    zip_bytes = _zip_bytes("ogora2024delimit.txt", sample_path.read_text(encoding="latin1"))
    zip_path.write_bytes(zip_bytes)
    assert us_gom.parse_production(zip_path).num_rows == 2
    assert us_gom.parse_production(io.BytesIO(zip_bytes)).num_rows == 2


def test_parse_production_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="not a BSEE OGOR-A file"):
        us_gom.parse_production(io.BytesIO(b"a,b,c\n1,2,3\n"))
    bad_date = ",".join(f'"{i}"' for i in range(19)).encode("latin1")
    with pytest.raises(ValueError, match="PRODUCTION_DATE"):
        us_gom.parse_production(io.BytesIO(bad_date))


@given(
    year=st.integers(min_value=1996, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    oil_a=st.integers(min_value=1, max_value=500_000),
    oil_b=st.integers(min_value=0, max_value=500_000),
    gas_a=st.integers(min_value=0, max_value=2_000_000),
    water_a=st.integers(min_value=0, max_value=500_000),
)
def test_ogora_rate_conversion_property(
    year: int, month: int, oil_a: int, oil_b: int, gas_a: int, water_a: int
) -> None:
    ym = f"{year:04d}{month:02d}"
    row_a = (
        f'"G07963"," A001","{ym}","28","O","{oil_a}","{gas_a}","{water_a}",'
        '"608174010000","09","MC  807","00689","SHELL","MC807","0","S01","19960701","",""'
    )
    row_b = (
        f'"G07963"," A001D","{ym}","28","O","{oil_b}","0","0",'
        '"608174010000","09","MC  807","00689","SHELL","MC807","0","S02","19960701","",""'
    )
    tbl = us_gom.parse_production(io.BytesIO(f"{row_a}\n{row_b}\n".encode("latin1")))
    assert tbl.num_rows == 1
    days = units.days_in_month(year, month)
    expected_oil = np.float32((oil_a + oil_b) / days)
    expected_gas = np.float32(gas_a * units.volume_factor("mcf", "e3m3") / days)
    expected_water = np.float32(water_a / days)
    assert tbl["oil_prod"][0].as_py() == pytest.approx(float(expected_oil), rel=1e-5)
    assert tbl["gas_prod"][0].as_py() == pytest.approx(float(expected_gas), rel=1e-5)
    assert tbl["water_prod"][0].as_py() == pytest.approx(float(expected_water), rel=1e-5)


def test_parse_fields_dissolves_blocks_and_keeps_unmatched(tmp_path: Path) -> None:
    blk_zip = _write_sample_blocks_zip(tmp_path / "blk_clip.zip")
    mast_zip = tmp_path / "mastdatadelimit.zip"
    mast_zip.write_bytes(_zip_bytes("mastdatadelimit.txt", _SAMPLE_MASTDATA))
    appc_zip = tmp_path / "appendcdelimit.zip"
    appc_zip.write_bytes(_zip_bytes("appendcdelimit.txt", _SAMPLE_APPENDC))

    fields = us_gom.parse_fields([blk_zip, mast_zip, appc_zip])
    assert fields.crs is not None
    assert fields.crs.to_epsg() == 4326
    assert len(fields) == 3

    mars = fields.loc[fields["field_id"] == "MC807"].iloc[0]
    assert mars["field_name"] == "Mars-Ursa (MC807)"
    assert mars["region"] == "Mississippi Canyon"
    assert mars["status"] == "Active"
    assert mars["discovery_date"] == "1992-04-01"
    assert mars["geometry"] is not None
    assert mars["area_km2"] > 0

    # WD030 is not in our sample blocks shapefile -> preserved as geometry=NULL metadata row.
    wd = fields.loc[fields["field_id"] == "WD030"].iloc[0]
    assert wd["field_name"] == "WD030"
    assert wd["status"] == "Expired"
    assert wd["geometry"] is None


def test_parse_facilities_and_wells(tmp_path: Path) -> None:
    plat_zip = tmp_path / "PlatStrucRawData.zip"
    plat_zip.write_bytes(
        _zip_bytes("PlatStrucRawData/mv_platstruc_structures.txt", _SAMPLE_PLATSTRUC)
    )
    facs = us_gom.parse_facilities([plat_zip])
    assert facs.crs is not None
    assert facs.crs.to_epsg() == 4326
    assert len(facs) == 3

    mars_tlp = facs.loc[facs["facility_id"] == "24001-1"].iloc[0]
    assert mars_tlp["facility_name"] == "MC 807 A (Mars)"
    assert mars_tlp["facility_type"] == "TLP"
    assert mars_tlp["status"] == "Active"
    assert mars_tlp["start_year"] == 1996
    assert mars_tlp["water_depth_m"] == pytest.approx(2940 * 0.3048, rel=1e-4)
    assert mars_tlp["geometry"] is not None

    removed = facs.loc[facs["facility_id"] == "10155-1"].iloc[0]
    assert removed["status"] == "Removed"

    no_coords = facs.loc[facs["facility_id"] == "25002-1"].iloc[0]
    assert no_coords["geometry"] is None

    bh_zip = tmp_path / "BoreholeRawData.zip"
    bh_zip.write_bytes(_zip_bytes("BoreholeRawData/mv_boreholes_all.txt", _SAMPLE_BOREHOLES))
    wells = us_gom.parse_wells([bh_zip])
    assert wells.crs is not None
    assert wells.crs.to_epsg() == 4326
    # Pacific well (REGION_CODE == "P") is excluded.
    assert len(wells) == 2
    w_mars = wells.loc[wells["well_id"] == "608174010000"].iloc[0]
    assert w_mars["well_name"] == "A001 ST00BP00"
    assert w_mars["region"] == "Mississippi Canyon"
    assert w_mars["status"] == "Completed"
    assert w_mars["spud_date"] == "1996-01-15"
    assert w_mars["completion_date"] == "1996-04-20"


def test_end_to_end_ingest_audit_and_export_web(tmp_path: Path) -> None:
    sample_ogora = (DATA_DIR / "bsee_ogora_sample.txt").read_text(encoding="latin1")
    ogora_zip = _zip_bytes("ogora2024delimit.txt", sample_ogora)
    blk_zip_path = _write_sample_blocks_zip(tmp_path / "src_blk_clip.zip")
    blk_zip_bytes = blk_zip_path.read_bytes()
    mast_zip = _zip_bytes("mastdatadelimit.txt", _SAMPLE_MASTDATA)
    appc_zip = _zip_bytes("appendcdelimit.txt", _SAMPLE_APPENDC)
    plat_zip = _zip_bytes("PlatStrucRawData/mv_platstruc_structures.txt", _SAMPLE_PLATSTRUC)
    bh_zip = _zip_bytes("BoreholeRawData/mv_boreholes_all.txt", _SAMPLE_BOREHOLES)

    routes: dict[str, bytes | str] = {
        "/Main/OGOR-A.aspx": _SAMPLE_OGORA_HTML,
        "/Production/Files/ogora2024delimit.zip": ogora_zip,
        "/Mapping/Files/blk_clip.zip": blk_zip_bytes,
        "/FieldReserves/Files/mastdatadelimit.zip": mast_zip,
        "/FieldReserves/Files/appendcdelimit.zip": appc_zip,
        "/Platform/Files/PlatStrucRawData.zip": plat_zip,
        "/Well/Files/BoreholeRawData.zip": bh_zip,
    }

    def handler(req: httpx2.Request) -> httpx2.Response:
        payload = routes[req.url.path]
        if isinstance(payload, str):
            return httpx2.Response(200, text=payload, request=req)
        return httpx2.Response(200, content=payload, request=req)

    data_root = tmp_path / "data"
    web_out = tmp_path / "web"
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        db = store.Store(data_root)
        summary = db.ingest("us-gom", origin="publisher", year=2024, geo_layers=True, client=client)

    assert summary.production_rows == 2
    assert summary.geo_counts == {"fields": 3, "facilities": 3, "wells": 2}

    report = audit.audit_store(root=data_root, source="us-gom")
    errors = [f for f in report.findings if f.severity == "error"]
    assert errors == []

    exported = export_web.export_web(data_dir=data_root, out_dir=web_out, skip_tiles=True)
    assert exported.sources == ("us-gom",)
    assert (web_out / "meta.json").exists()
    assert (web_out / "rollups" / "country_monthly.parquet").exists()
