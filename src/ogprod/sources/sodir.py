"""Norway — Sodir (Norwegian Offshore Directorate) monthly production per field.

Sodir's FactPages publish saleable production per field and month back to 1971
as a single comma-separated CSV table (``field_production_monthly``): oil, NGL
and condensate in millions of Sm³, gas in billions of Sm³. The field is
identified by its stable NPDID and its name.

Downloads: https://factpages.sodir.no/ (Field → Table view → Production →
Saleable → Monthly, "Download data"); see also
https://www.sodir.no/en/facts/data-and-analyses/open-data/
"""

from __future__ import annotations

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


@base.register
class Sodir(base.Source):
    name = "sodir"
    entity_type = EntityType.FIELD
    docs_url = "https://factpages.sodir.no/"

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        frame = pd.read_csv(raw)
        missing = [column for column in _REQUIRED if column not in frame.columns]
        if missing:
            raise ValueError(f"not a Sodir field_production_monthly file; missing {missing}")

        period = month_start(frame["prfYear"], frame["prfMonth"])
        name = frame["prfInformationCarrier"].astype("string")
        entity_id = (
            frame["prfNpdidInformationCarrier"].astype("string")
            if "prfNpdidInformationCarrier" in frame.columns
            else name
        )

        volumes = {
            product: pd.to_numeric(frame[column], errors="coerce").map(
                lambda v, u=unit: units.to_m3(v, u)
            )
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
