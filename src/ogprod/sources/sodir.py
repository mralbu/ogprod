"""Norway — Sodir (Norwegian Offshore Directorate) monthly production per field.

Sodir's FactPages publish saleable production per field and month back to 1971
as a single comma-separated CSV table (``field_production_monthly``): oil, NGL
and condensate in millions of Sm³, gas in billions of Sm³. The field is
identified by its stable NPDID and its name.

Downloads: https://factpages.sodir.no/ (Field → Table view → Production →
Saleable → Monthly, "Download data"); see also
https://www.sodir.no/en/facts/data-and-analyses/open-data/

Field entities (ring-fence polygons) come from FactMaps' ArcGIS DataService
instead, queried directly as GeoJSON — see ``_AREA_URL`` below.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

from ogprod import units
from ogprod.schema import EntityType, Product, month_start
from ogprod.sources import base

_VOLUME_COLUMNS: dict[str, tuple[Product, str]] = {
    "prfPrdOilNetMillSm3": (Product.OIL, "e6m3"),
    "prfPrdGasNetBillSm3": (Product.GAS, "e9m3"),
    "prfPrdNGLNetMillSm3": (Product.NGL, "e6m3"),
    "prfPrdCondensateNetMillSm3": (Product.CONDENSATE, "e6m3"),
    "prfPrdProducedWaterInFieldMillSm3": (Product.WATER, "e6m3"),
}

_REQUIRED = ("prfInformationCarrier", "prfYear", "prfMonth")

# FactPages report-server export of the full table (1971→today). The report
# server requires an IpAddress parameter but accepts any value.
_CSV_URL = (
    "https://factpages.sodir.no/public?/Factpages/external/tableview/field_production_monthly"
    "&rs:Command=Render&rc:Toolbar=false&rc:Parameters=f&rs:Format=CSV&Top100=false"
    "&IpAddress=1.2.3.4&CultureCode=en"
)

# FactMaps field-outline ("ring fence") polygons. There is no public zipped
# shapefile download (the documented `/downloads/shape/fldArea.zip` 404s);
# the working source is FactMaps' ArcGIS DataService, layer 7100 ("field"),
# queried as GeoJSON in EPSG:4326. Attribute names come straight off that
# service (see https://factmaps.sodir.no/api/rest/services/DataService/Data/
# MapServer/7100), but older docs describe a DBF-style shapefile with 10-char
# truncated names, so aliases cover both forms; extend this table if FactMaps
# drifts (see docs/design.md).
_AREA_URL = (
    "https://factmaps.sodir.no/api/rest/services/DataService/Data/MapServer/7100/query"
    "?where=1%3D1&outFields=*&f=geojson"
)


_ENTITY_ALIASES = {
    "fieldname": "entity_name",
    "fldname": "entity_name",
    "idfield": "entity_id",
    "npdidfield": "entity_id",
    "fldnpdidfield": "entity_id",
    "fldnpdidfi": "entity_id",
    "oplongname": "operator",
    "opername": "operator",
    "fldcurrentoperator": "operator",
    "cmplongname": "operator",
    "main_area": "region",
    "fldmainarea": "region",
    "status": "status",
    "fldstatus": "status",
    "fldcurrentactivitysatus": "status",
}


@base.register
class Sodir(base.Source):
    name = "sodir"
    entity_type = EntityType.FIELD
    docs_url = "https://factpages.sodir.no/"

    def fetch(self, dest_dir: str | Path, *, client: httpx.Client | None = None) -> list[Path]:
        """Download the full ``field_production_monthly`` table (one CSV)."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            return [base.download(http, _CSV_URL, dest_dir / "field_production_monthly.csv")]

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        frame = pd.read_csv(raw)
        missing = [column for column in _REQUIRED if column not in frame.columns]
        if missing:
            raise ValueError(f"not a Sodir field_production_monthly file; missing {missing}")

        period = month_start(frame["prfYear"], frame["prfMonth"])
        name = frame["prfInformationCarrier"].astype("string")
        # NPDIDs arrive float-typed when the column has nulls; base.build_frame
        # normalizes them to clean integer strings.
        entity_id = (
            frame["prfNpdidInformationCarrier"]
            if "prfNpdidInformationCarrier" in frame.columns
            else name
        )

        volumes = {
            product: pd.to_numeric(frame[column], errors="coerce") * units.factor(unit)
            for column, (product, unit) in _VOLUME_COLUMNS.items()
            if column in frame.columns
        }

        return base.build_frame(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=entity_id,
            entity_name=name,
            period=period,
            volumes_m3=volumes,
            field=name,
        )

    def fetch_entities(
        self, dest_dir: str | Path, *, client: httpx.Client | None = None
    ) -> list[Path]:
        """Download FactMaps field outlines (ring fences) as GeoJSON."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            return [base.download(http, _AREA_URL, dest_dir / "field.geojson")]

    def parse_entities(self, raw: base.RawFile) -> gpd.GeoDataFrame:
        frame = gpd.read_file(raw)
        columns = {column.lower(): column for column in frame.columns}
        roles: dict[str, str] = {}
        for alias, role in _ENTITY_ALIASES.items():
            if alias in columns and role not in roles:
                roles[role] = columns[alias]
        if "entity_name" not in roles or "entity_id" not in roles:
            raise ValueError(f"not a Sodir field-area file; columns found: {sorted(frame.columns)}")

        # Repair and dissolve only the features that carry geometry.
        # FactMaps polygons occasionally self-intersect (invalid rings);
        # repair them so dissolve's union doesn't degrade into a
        # GeometryCollection. Features without an NPDID are dropped here
        # (dissolve's groupby skips null keys; build_entities enforces the
        # same policy) — they can't join anything.
        id_column = roles["entity_id"]
        has_geometry = ~(frame.geometry.isna() | frame.geometry.is_empty)
        geometric = frame[has_geometry]
        geometric = geometric.set_geometry(geometric.geometry.make_valid())
        # One geometry per field: FactMaps splits fields into several polygons.
        dissolved = geometric.dissolve(by=id_column, aggfunc="first").reset_index()

        # Fields with no geometry at all (e.g. newly approved, not yet
        # delineated) keep a null-geometry row so they still join for
        # metadata — the schema's invariant is that missing geometry is a
        # row, not an error.
        bare = frame[~has_geometry & frame[id_column].notna()]
        bare = bare[~bare[id_column].isin(dissolved[id_column])]
        bare = bare.drop_duplicates(subset=[id_column]).assign(geometry=None)
        if len(bare):
            dissolved = pd.concat([dissolved, bare], ignore_index=True)
        name = dissolved[roles["entity_name"]].astype("string")

        def optional(role: str) -> pd.Series | None:
            return dissolved[roles[role]] if role in roles else None

        return base.build_entities(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=dissolved[roles["entity_id"]],
            entity_name=name,
            geometry=dissolved.geometry,
            field=name,
            region=optional("region"),
            operator=optional("operator"),
            status=optional("status"),
            crs=dissolved.crs,
        )
