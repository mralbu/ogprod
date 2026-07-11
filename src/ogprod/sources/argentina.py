"""Argentina — Secretaría de Energía, production per well ("Capítulo IV").

Monthly production per well reported under Resolución 319/93 chapter IV,
published as yearly comma-separated CSV files (UTF-8, dot decimals) on the
national energy open-data portal. Oil and water are reported in m³, gas in
thousands of m³. A parallel dataset covers unconventional wells (Vaca Muerta).

Downloads: http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import pandas as pd

from ogprod import units
from ogprod.schema import EntityType, Product, month_start
from ogprod.sources import base

_VOLUME_COLUMNS: dict[str, tuple[Product, str]] = {
    "prod_pet": (Product.OIL, "m3"),
    "prod_gas": (Product.GAS, "e3m3"),
    "prod_agua": (Product.WATER, "m3"),
}

_REQUIRED = ("idpozo", "anio", "mes")

# The portal is a stock CKAN instance; package_show lists one CSV resource per
# year (e.g. "Producción de Pozos de Gas y Petróleo – 2025").
_PACKAGE_SHOW_URL = "http://datos.energia.gob.ar/api/3/action/package_show"
_DATASET_ID = "produccion-de-petroleo-y-gas-por-pozo"

# The wells listing lives as one more CSV resource inside the *same* CKAN
# dataset as production (there is no separate
# "listado-de-pozos-cargados-por-empresas-operadoras" package on the live
# portal -- that id 404s). Its resource name matches this fragment. Unlike
# the production file it has no ``geojson`` column: coordinates are plain
# decimal degrees in ``coordenadax``/``coordenaday`` (lon/lat), and it
# reports the operator only as a short code (``idempresa``, e.g. "YPF") with
# no ``empresa``/``tipoestado`` columns; ``clasificacion`` is the closest
# analog to a well status (e.g. "EXPLOTACION", "EXPLORACION").
_WELLS_RESOURCE_NAME = "Listado de pozos cargados por empresas operadoras"
_ENTITY_REQUIRED = ("idpozo", "coordenadax", "coordenaday")


@base.register
class Argentina(base.Source):
    name = "argentina"
    entity_type = EntityType.WELL
    docs_url = "http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo"

    def fetch(
        self,
        dest_dir: str | Path,
        *,
        year: int | None = None,
        client: httpx.Client | None = None,
        **kwargs: Any,
    ) -> list[Path]:
        """Download the per-year Capítulo IV CSV for ``year``."""
        if year is None:
            raise TypeError("argentina fetch requires a year, e.g. fetch(dest, year=2024)")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            response = http.get(_PACKAGE_SHOW_URL, params={"id": _DATASET_ID})
            response.raise_for_status()
            resources = [
                resource
                for resource in response.json()["result"]["resources"]
                if resource.get("format") == "CSV"
            ]
            matches = [r for r in resources if str(year) in (r.get("name") or "")]
            if not matches:
                years = sorted(
                    match.group()
                    for resource in resources
                    if (match := re.search(r"\b(19|20)\d\d\b", resource.get("name") or ""))
                )
                raise ValueError(f"no Capítulo IV CSV for {year}; years available: {years}")
            url = matches[0]["url"]
            dest = dest_dir / httpx.URL(url).path.rsplit("/", 1)[-1]
            return [base.download(http, url, dest)]

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        # low_memory=False: real files are >100 MB and chunked type inference
        # emits DtypeWarning on sparse text columns.
        frame = pd.read_csv(raw, low_memory=False)
        missing = [column for column in _REQUIRED if column not in frame.columns]
        if missing:
            raise ValueError(f"not a Capítulo IV well production file; missing {missing}")

        period = month_start(frame["anio"], frame["mes"])
        entity_id = frame["idpozo"].astype("string")
        entity_name = frame["sigla"] if "sigla" in frame.columns else entity_id

        volumes = {
            product: pd.to_numeric(frame[column], errors="coerce").map(
                lambda v, u=unit: units.to_m3(v, u)
            )
            for column, (product, unit) in _VOLUME_COLUMNS.items()
            if column in frame.columns
        }

        def optional(column: str) -> pd.Series | None:
            return frame[column] if column in frame.columns else None

        return base.build_frame(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            period=period,
            volumes_m3=volumes,
            field=optional("areayacimiento"),
            basin=optional("cuenca"),
            region=optional("provincia"),
            operator=optional("empresa"),
        )

    def fetch_entities(
        self, dest_dir: str | Path, *, client: httpx.Client | None = None, **kwargs: Any
    ) -> list[Path]:
        """Download the wells listing CSV (coordinates for every reported well)."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            response = http.get(_PACKAGE_SHOW_URL, params={"id": _DATASET_ID})
            response.raise_for_status()
            resources = [
                resource
                for resource in response.json()["result"]["resources"]
                if resource.get("format") == "CSV"
                and _WELLS_RESOURCE_NAME in (resource.get("name") or "")
            ]
            if not resources:
                raise ValueError(
                    f"no {_WELLS_RESOURCE_NAME!r} CSV resource in CKAN dataset {_DATASET_ID!r}"
                )
            url = resources[0]["url"]
            dest = dest_dir / httpx.URL(url).path.rsplit("/", 1)[-1]
            return [base.download(http, url, dest)]

    def parse_entities(self, raw: base.RawFile) -> gpd.GeoDataFrame:
        frame = pd.read_csv(raw, low_memory=False)
        missing = [column for column in _ENTITY_REQUIRED if column not in frame.columns]
        if missing:
            raise ValueError(f"not an Argentina listado de pozos file; missing {missing}")

        x = pd.to_numeric(frame["coordenadax"], errors="coerce")
        y = pd.to_numeric(frame["coordenaday"], errors="coerce")
        points = gpd.GeoSeries(gpd.points_from_xy(x, y), crs="EPSG:4326")
        geometry = points.where(~(x.isna() | y.isna()), None)
        entity_id = frame["idpozo"].astype("string")

        def optional(column: str) -> pd.Series | None:
            return frame[column] if column in frame.columns else None

        entity_name = frame["sigla"] if "sigla" in frame.columns else entity_id
        return base.build_entities(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            geometry=geometry,
            field=optional("areayacimiento"),
            basin=optional("cuenca"),
            region=optional("provincia"),
            operator=optional("idempresa"),
            status=optional("clasificacion"),
        )
