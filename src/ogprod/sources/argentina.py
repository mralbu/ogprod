"""Argentina — Secretaría de Energía, production per well ("Capítulo IV").

Monthly production per well reported under Resolución 319/93 chapter IV,
published as yearly comma-separated CSV files (UTF-8, dot decimals) on the
national energy open-data portal. Oil and water are reported in m³, gas in
thousands of m³. A parallel dataset covers unconventional wells (Vaca Muerta).

Downloads: http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo
"""

from __future__ import annotations

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


@base.register
class Argentina(base.Source):
    name = "argentina"
    entity_type = EntityType.WELL
    docs_url = "http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo"

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        frame = pd.read_csv(raw)
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
