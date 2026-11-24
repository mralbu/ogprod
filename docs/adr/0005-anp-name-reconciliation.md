# 0005. Reconcile ANP facility and field names

Status: accepted

## Context

ANP's production CSVs, the LPO platform list and the field WFS spell the same entities
differently and cover different sets of them.

## Decision

- **Facilities**: the LPO list is canonical. Pre-2022 production rows lose their trailing
  hull code (`PETROBRAS 66 (P-66)` → `PETROBRAS 66`), and known spelling drifts are
  mapped by hand in `brazil._FACILITY_ALIASES`.
- **LPO gas capacity** is published in `Mil m³/d`, which is already `e3m3/d`: it is stored
  without conversion.
- **Fields**: WFS polygons are the base. Missing metadata (region, environment, operator,
  start date) is backfilled from `extracao-campo*.csv`, and fields absent from the WFS
  (e.g. relinquished ones) are appended as metadata-only rows.
- **Field name canonicalization** (applied consistently across `production`, `geo/fields`,
  `geo/wells`, and `geo/facilities.fields`):
  - **Transfer-of-Rights Surplus (`*_ECO`)**: rule-based stripping of `_ECO`
    (`BUZIOS_ECO` → `BUZIOS`, `ATAPU_ECO` → `ATAPU`, `SEPIA_ECO` → `SEPIA`,
    `ITAPU_ECO` → `ITAPU`), which share identical WFS polygons (`IoU = 1.0`).
  - **Renames & spelling drifts** (`_FIELD_RENAME_ALIASES`): e.g. `LULA` → `TUPI`,
    `SUL DE LULA` → `SUL DE TUPI`, `ANC_LULA` → `ANC_TUPI` (then → `TUPI`),
    `ANC_BRAVA` → `ESPADIM`, `BELA VISTA_` → `BELA VISTA`, `DIAS D'AVILA` → `DIAS DAVILA`,
    and legacy `(LICITADO)` suffixes (`JACUMIRIM`, `FAZENDA GAMELEIRA`).
  - **Open acreage (`AnC_*`, `_FIELD_SPLIT_ALIASES`)**: uncontracted Union/PPSA equity
    slices without WFS polygons (`ANC_MERO` → `MERO`, `ANC_TUPI` → `TUPI`,
    `ANC_NORTE_ATAPU` → `ATAPU`, `ANC_TARTARUGA VERDE` → `TARTARUGA VERDE`,
    `ANC_JUBARTE_{NORDESTE,SUDOESTE}` → `JUBARTE`, `ANC_FORNO` → `ALBACORA`,
    `ANC_ARGONAUTA_ME1` → `ARGONAUTA`).
  - **Retired offshore annexations**: Parque das Baleias (`BALEIA AZUL`, `BALEIA FRANCA`,
    `CACHALOTE`, `PIRAMBU` → `JUBARTE`), `SURURU` → `BERBIGAO`, `SERRA`/`ARATUM` → `MACAU`,
    `PIRACABA` → `BAUNA`, `TARTARUGA MESTICA` → `TARTARUGA VERDE`, and `NORTE DE PESCADA` →
    `DENTAO`. Collapsing WFS rows preserves the canonical field's metadata, takes the
    earliest dates, unions geometries, and adds `area_km2` only for non-overlapping polygons.
    `DOM JOAO MAR` is intentionally left separate because ANP splits the same wells across
    onshore (`pt`) and offshore (`pm`) CSVs in 2022–2023.
  - **Pre-2022 basin homonyms** (`_FIELD_BASIN_DISAMBIGUATION`): disambiguated by basin
    after duplicate merging (`FAZENDA BELEM` → `CE`/`BA`, `BREJINHO` → `RN`/`BA`,
    Sergipe 1976 `MERO` → `MERO SE`, Espírito Santo `FAZENDA BOA ESPERANCA` → `ES`).
  - **Active adjacent AIP concessions** with distinct WFS polygons (`SUL DE TUPI`,
    `OESTE DE ATAPU`, `SEPIA LESTE`, `TAMBUATA`, `BACALHAU NORTE`, `SAPINHOA` compass
    blocks, etc.) remain separate concessions.

## Consequences

`_FACILITY_ALIASES` and `_FIELD_*` tables grow as new ANP drifts or annexations appear;
join-coverage warnings from `ogprod audit` are how they surface. Changing these mappings
requires a fresh local publisher ingest of Brazil (see ADR 0002).
