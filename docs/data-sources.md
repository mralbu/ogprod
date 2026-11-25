# Public Oil & Gas Production Data Sources

Research survey and technical evaluation of public oil & gas production, injection, and geospatial data portals worldwide, evaluated against the `ogprod` v2 architecture:

- **Layer 1 production (`store/production/`):** monthly calendar-day average rates in `float32` (`oil_prod`, `condensate_prod`, `ngl_prod`, `gas_prod`, `water_prod`, `water_inj`, `gas_inj`, `co2_inj`), wide Parquet keyed on `(source, entity_type, entity_id, field_id, facility_id, period)`.
- **Layer 1 geospatial (`store/geo/`):** Hilbert-sorted GeoParquet 1.1 (`EPSG:4326`) across three tables: `fields` (polygons), `facilities` (points — platforms/FPSOs/installations), and `wells` (points).
- **Distribution:** anonymous Hugging Face mirror (`mralbu/ogprod`), weekly GitHub Actions refresh (ADR 0002), and Layer 2 static web explorer (`ogprod export-web`).

Restored from v1 history (`395cf68:docs/data-sources.md`) and expanded with live portal verification, geospatial inventory, CRS/unit quirks, and licensing checks.

Last reviewed: November 2026.

---

## Summary Matrix

| Status | Country / Region | Suggested Slug | Publisher | Grain | Cadence / History | Streams¹ | Geo Layers² | Access & CI Fit |
|---|---|---|---|---|---|---|---|---|
| **Built (v2)** | Brazil | `brazil` | ANP | Well | Monthly, 1941–present | O, C, G, W, WI, GI, CO₂ | F, Fac (cap), W | Plain CSV + WFS; `gov.br` blocks Azure/GHA (ADR 0002) |
| **Built (v2)** | Norway | `norway` | Sodir | Field | Monthly, 1971–present | O, C, NGL, G, W | F, Fac, W | Single CSV + ArcGIS REST; CI-friendly |
| **Built (v2)** | Mexico | `mexico` | SENER / CNIH SIH | Field (+ well CSV) | Monthly, 1960–present (wells 1930+) | O, C, G, W | F, Fac, W | Bulk CSV/ZIP + Shapefile API; CI-friendly |
| **Built (v2)** | United Kingdom | `uk` | NSTA (*PPRS*) | Field (reporting unit) | Monthly, 1975–present | O, C, G, W, WI, GI | F (off+on), Fac, W (off+on) | Hosted ArcGIS FeatureServer (WGS84); CI-friendly |
| **Tier 1 (v1 built)** | Argentina | `argentina` | Secretaría de Energía (*Cap. IV*) | Well | Monthly, 2006–present (+ Vaca Muerta) | O, G, W | F (conc/yac), W | CKAN API + per-year CSV; CI-friendly |
| **Tier 1 (v1 spec'd)** | USA — Federal Offshore | `us-gom` / `us-ocs` | BSEE / BOEM (*OGOR-A*) | Well | Monthly, 1996–present (1947+ hist) | O, G, W | Leases/Units, Fac, W | Direct HTTPS ZIPs + BOEM SHP (NAD 27); CI-friendly |
| **Tier 1** | Netherlands | `netherlands` | NLOG (TNO) | Field / Well / Facility | Monthly, 2003–present | O, C, G, W, Inj | F, Fac, W | Datacenter exports + OGC WFS / SHP (ED50); CI-friendly |
| **Tier 1** | USA — Alaska | `us-ak` | AOGCC | Well | Monthly, all history (pre/post-2000) | O, NGL, G, W, Inj | Fac, W | Single ~30 MB `DataMinerAll.zip`, updated daily (NAD 27) |
| **Tier 1** | Colombia | `colombia` | ANH (`datos.gov.co`) | Field | Monthly, 2014–present | O, G | Field pts (+ ANH Geovisor) | Socrata API / CSV (CC BY-SA 4.0); quarterly updates |
| **Tier 2** | Denmark | `denmark` | Danish Energy Agency (ENS) | Field | Monthly 2018+ (yearly 1972+) | O, G, W, WI, GI | F, Fac, W | Excel spreadsheets + SHP/WFS (ED50/UTM32N) |
| **Tier 2** | Canada — Offshore NL | `canada-nl` | C-NLOER (ex-C-NLOPB) | Well & Field | Monthly, 1997–present | O, G, W | Licences, W (5 fields, 4 hubs) | HTML/tables + Data Hub SHP; small high-value offshore set |
| **Tier 2** | Italy | `italy` | MASE / UNMIG | Concession | Monthly, 2004–present | O (`kg`), C (`kg`), G | Conc, Fac, W | Single CSV (CC BY 4.0) + ArcGIS Online WebGIS |
| **Tier 2** | Canada — AB / SK / BC | `canada-ab`, `canada-sk`, `canada-bc` | Petrinex / AER / BCER | Well & Facility | Monthly (5-yr rolling on APD; 1962+ AER) | O, C, NGL, G, W, Inj | Fac, W (daily infra CSV) | Bulk CSV/XML; huge scale + rolling amendments; check AER licence |
| **Tier 3** | USA — Other States | `us-tx`, `us-nd`, `us-ca`, `us-co`, `us-nm`, … | RRC, DMR, CalGEM, ECMC, OCD, … | Well or Lease | Monthly | O, G, W (+ Inj in CA/AK/NM) | Varies (often per-county) | High effort: lease grain (TX), PDF-only history (ND), fragmented portals |
| **Tier 3** | Australia | `australia-qld`, `australia-wa`, … | QLD Open Data, WA DMIRS, NOPIMS | Field / Tenure | 6-monthly (QLD) / varies | O, C, LPG, G, CSG | Varies by state | State-split; non-monthly cadence in QLD; NOPIMS lacks prod tables |
| **Tier 3** | New Zealand | `new-zealand` | NZP&M / MBIE | Field / National | Quarterly national / annual field | O, C, G | Geodata (RealMe login) | Non-monthly; Imperva bot protection on MBIE |
| **Tier 3** | Peru | `peru` | Perupetro | Lot (*Lote*) | Monthly | O, NGL, G | Lot maps | PDF-first / irregular Excel workbooks |
| **Tier 3** | Germany | `germany` | LBEG (Lower Saxony) | Field | Annual | O, G | Map server | Annual granularity only |
| **Closed / Macro** | OPEC, Russia, India, Malaysia, Indonesia, Guyana, Angola | — | JODI / EIA / OPEC / IEA | Country (or gated) | Monthly / Annual | National totals | None / paywalled | Use EIA/JODI for `ogprod audit` macro validation, not Layer 1 |

¹ **Streams:** `O` crude oil, `C` condensate, `NGL` natural gas liquids, `G` natural gas, `W` produced water, `WI` water injection, `GI` gas injection, `CO₂` CO₂ injection, `Inj` multi-fluid injection.  
² **Geo Layers:** `F` field polygons, `Fac` facilities/platforms/installations (`cap` = capacities reported), `W` wells/boreholes, `Conc` concessions.

---

## Evaluation Criteria for v2 Connectors

Before adding a new connector to `ogprod`, evaluate it on five axes that matter specifically for v2:

1. **Schema fit (`src/ogprod/schema.py`):**
   - Monthly reporting fits `period` (`YYYY-MM-01`) directly; 6-monthly/quarterly/annual sources cannot be mixed into monthly charts without misrepresenting rates.
   - `EntityType` currently allows `well` and `field`. Concession-level (Italy) or lot-level (Peru) sources can map cleanly to `field` (with attribution in docs), whereas lease-level sources (Texas RRC oil) mix multiple wells per lease and would require either `EntityType.LEASE` or documented allocation.
   - Volumetric rates (`m³`, `Sm³`, `Nm³`, `bbl`, `Mcf`, `MMscf`) convert exactly via `units.py`; mass-reported crude (`kg` / `tonnes`, e.g., Italy UNMIG) requires either API/density metadata or a documented standard conversion assumption.
2. **Geospatial completeness (`geo/fields`, `geo/facilities`, `geo/wells`):**
   - The v2 map explorer shines when a source supplies polygons (`fields`) and/or `facilities` alongside production, and links production IDs cleanly to spatial IDs (like ANP's `field_id`/`facility_id` reconciliation in ADR 0005).
   - Non-WGS84 upstream CRSs (**NAD 27** for BSEE/BOEM and Alaska AOGCC; **ED50 / UTM** for NLOG and Denmark ENS) must be explicitly reprojected to `EPSG:4326` with datum shifts before `validate_geo`.
3. **Credential-free bulk access & CI reliability:**
   - Ideal sources expose stable HTTPS bulk files (CSV/ZIP) or open geospatial APIs (OGC WFS, ArcGIS FeatureServer, CKAN, Socrata) with no login, no CAPTCHA, and no cloud-IP blocks (unlike `gov.br`, see [ADR 0002](adr/0002-brazil-not-refreshed-in-ci.md)).
4. **Restatement & mirror footprint:**
   - Most regulators restate recent months (and some, like BSEE and Petrinex, restate years back). Small-to-medium archives (< 200 MB compressed Parquet) fit easily in the single public Hugging Face mirror (`mralbu/ogprod`) and weekly GitHub Actions jobs; massive onshore shale archives (Alberta Petrinex, Texas RRC) need careful partitioning and selective refresh.
5. **Licensing & mirror redistribution:**
   - Because `ogprod` publishes normalized Parquet/GeoParquet to the public Hugging Face mirror, redistribution terms matter (US federal/state public domain, CC BY 4.0, CC BY-SA 4.0, UK NSTA User Agreement, vs restrictive terms such as Alberta AER reproduction rules).

---

## Built-in Sources (v2 Baseline)

Already implemented in `src/ogprod/sources/` and documented in [data-architecture.md](data-architecture.md):

- **`brazil` (ANP):** 1941–present well-level oil, condensate, gas, water production + water/gas/CO₂ injection; field polygons (WFS + relinquished CSVs), platforms/FPSOs with oil & gas capacities (`lpo`), well points (`situacao-pocos`). Refreshed locally (not in GitHub Actions) due to `gov.br` blocking Azure IP ranges.
- **`norway` (Sodir):** 1971–present field-level oil, condensate, NGL, gas, water production (single ~2 MB CSV); field polygons, facilities, and wellbores via FactMaps ArcGIS REST. Refreshed weekly in CI.
- **`mexico` (SENER / ex-CNH SIH):** 1960–present field-level production (default, ~18 MB CSV) plus optional well-level parser (`PRODUCCION_POZOS.zip`, 1930–present); field polygons, installations, and regional well shapefiles via `mapa-hidrocarburos.energia.gob.mx`. Refreshed weekly in CI.
- **`uk` (NSTA):** 1975–present field/reporting-unit monthly oil, condensate, associated + dry gas, produced water, and water/gas injection via NSTA's hosted PPRS ArcGIS FeatureServer (offshore + onshore); offshore & onshore field polygons, offshore surface facilities, and offshore & onshore wellbore top holes (WGS84). Refreshed weekly in CI under the NSTA User Agreement (non-commercial reuse with attribution).

---

## Tier 1 — Highest Value-for-Effort Candidates

### 1. Argentina — Secretaría de Energía (*Capítulo IV*)

- **Suggested slug:** `argentina` (`AR`)
- **Status:** Working connector existed in v1 (`395cf68:src/ogprod/sources/argentina.py`); needs porting to v2 wide `float32` schema, `httpx2`, and geospatial layers.
- **Production:**
  - Monthly well-level production under *Resolución 319/93 Capítulo IV* (oil in `m³`, gas in `10³ m³`, water in `m³`), published as per-year CSVs on the CKAN portal (`datos.energia.gob.ar` / `datos.gob.ar`), refreshed frequently (~daily/monthly).
  - Includes a parallel **unconventional wells dataset** (*Producción de Pozos de Gas y Petróleo No Convencional*) covering **Vaca Muerta** shale, plus conventional history back to 2006.
  - **Known quirks (empirically verified in v1):**
    - CKAN lists lookalike `(DDJJ abiertas y cerradas)` resources alongside standard year CSVs, and since 2025 some years appear twice; match the exact year resource pattern and pick the newest `created` timestamp.
    - `idpozo` arrives float-typed when blank rows exist; normalize through integer strings.
    - Real yearly CSVs exceed 100 MB (use `low_memory=False` or PyArrow CSV reader).
- **Geospatial layers:**
  - `geo/wells`: `Listado de pozos cargados por empresas operadoras` CSV resource inside the same CKAN package (`coordenadax`/`coordenaday` decimal degrees, `idempresa`, `clasificacion`).
  - `geo/fields`: Secretaría de Energía publishes `Producción de hidrocarburos - Yacimientos` (field polygons) and `Concesiones de Explotación` (exploitation concession polygons, updated monthly through May 2026) on `datos.gob.ar` / `datos.energia.gob.ar`.
  - `geo/facilities`: Not part of the core Capítulo IV package (onshore-dominated; offshore Tierra del Fuego / Austral platforms would need separate verification).
- **Scale & CI:** Medium-large (tens of thousands of wells; yearly partitioning fits v2 cleanly). Plain HTTP/HTTPS CKAN API — check whether `datos.energia.gob.ar` supports HTTPS cleanly or needs the `datos.gob.ar` HTTPS mirror in CI.
- **Licence:** Argentine public open data (`datos.gob.ar` / Secretaría de Energía; dataset metadata often leaves the licence field unspecified — verify portal terms before mirroring).
- **Links:**
  - [Capítulo IV dataset (`datos.gob.ar`)](https://datos.gob.ar/dataset/energia-produccion-petroleo-gas-por-pozo-capitulo-iv)
  - [Yacimientos polygons (`datos.gob.ar`)](https://datos.gob.ar/dataset/energia-produccion-hidrocarburos---yacimientos)
  - [Concesiones de Explotación (`datos.gob.ar`)](https://datos.gob.ar/dataset/energia-produccion-hidrocarburos---concesiones-explotacion)

---

### 2. United Kingdom — NSTA (North Sea Transition Authority) *(Built in v2)*

- **Slug:** `uk` (`GB`) — **implemented in `src/ogprod/sources/uk.py`** (see [data-architecture.md](data-architecture.md#34-priority-4--united-kingdom-nsta)).
- **Why high priority:** Completes the UK North Sea + UK onshore alongside Norway (`norway`) with an equally rich offshore/onshore dataset and a clean ArcGIS Open Data Hub.
- **Production:**
  - Monthly field-level production & injection via **PPRS** (*Petroleum Production Reporting System*, PON 7), published on the NSTA Open Data Hub as `UKCS hydrocarbon field production reports PPRS points (WGS84)` — upgraded in late 2025/2026 to a hosted ArcGIS Feature Service with direct CSV download and REST query pagination.
  - Covers offshore oil, dry gas, condensate/NGL, produced water, and water/gas injection (unit types include *Oil Field Exporting to Pipeline*, *Oil Tanker to Loader*, *Dry Gas Fields*, and *Onshore Oil Field*), 1975–present (~3-month lag).
  - **Bonus emerging data:** NSTA Open Data Hub also hosts `UKCS offshore petroleum fields daily production (WGS84)` and `UKCS petroleum production data by wellbore (ED50)` (linked to daily production under the Energy Act 2016 / NDR). Start with monthly PPRS for v2 consistency.
- **Geospatial layers (all three available on ArcGIS Hub):**
  - `geo/fields`: `UKCS petroleum field determinations (WGS84)` (Polygon/MultiPolygon).
  - `geo/facilities`: `UKCS offshore infrastructure surface points` (active + removed surface installations & subsea structures, updated semi-annually in April/October; prefer GeoJSON/FeatureServer over Shapefile due to 254-char attribute truncation).
  - `geo/wells`: NSTA wellbore surface points (WGS84) via Open Data Hub.
- **Units & quirks:** Verify PPRS unit columns on live download (oil/water in `m³` / tonnes / `bpd`, gas in `kSm³` / `MMscf/d` depending on column). Some datasets are published in both WGS84 and ED50 — always pick the `(WGS84)` service.
- **Scale & CI:** Compact (a few hundred UKCS fields, ~150k monthly rows — similar in scale to Norway Sodir). Ideal for weekly GitHub Actions refresh.
- **Licence:** **NSTA User Agreement** (replaced OGA User Licence in April 2026; verify redistribution terms for the Hugging Face mirror).
- **Links:**
  - [NSTA Production Data](https://www.nstauthority.co.uk/data-and-insights/data/themes/production/)
  - [NSTA Open Data Hub (PPRS search)](https://open-data-ukcs-transition.hub.arcgis.com/search?categories=%252Fcategories%252Fproduction%2C%252Fcategories%252Fproduction%252Fpprs)
  - [NSTA Field Determinations (WGS84)](https://open-data-ukcs-transition.hub.arcgis.com/search?categories=%252Fcategories%252Ffields%2C%252Fcategories%252Ffields%252Fdetermination)
  - [NSTA Data FAQs & Change Log](https://www.nstauthority.co.uk/data-and-insights/about-nsta-data/improvements-and-change-log/)

---

### 3. USA — Federal Offshore (BSEE / BOEM *OGOR-A*)

- **Suggested slug:** `us-gom` (or `us-ocs` if Pacific OCS is included alongside the Gulf) (`US`)
- **Status:** Empirically verified v1 design spec exists at `395cf68:docs/superpowers/specs/2026-07-11-bsee-connector-design.md`; `"us-gom"` is already noted as an example sub-national slug in [data-architecture.md](data-architecture.md).
- **Production:**
  - Per-year bulk ASCII zips from **1996–present** (`https://www.data.bsee.gov/Production/Files/ogora{YYYY}delimit.zip`, plus rolling current-year `ogoradelimit.zip`, regenerated bi-monthly; ~100 MB total uncompressed). Historical pre-1996 archives back to 1947 also exist.
  - Fixed 19-column comma-delimited ASCII with **no header row**, quoted space/zero-padded fields: `MON_O_PROD_VOL` (`bbl`), `MON_G_PROD_VOL` (`Mcf`), `MON_WTR_PROD_VOL` (`bbl`), `API_WELL_NUMBER` (12-digit), `FIELD_NAME_CODE`, `AREA_BLOCK`, `SORT_NAME` (operator).
  - **Grain quirk:** OGOR-A reports per *completion/interval*; sum completions to `(API_WELL_NUMBER, period, field_id)` and drop ~40% all-zero status rows. Injection volumes appear in `INJECTION_VOLUME` (requires fluid-type check via `PRODUCT_CODE` / OGOR-B before mapping to `water_inj`/`gas_inj`).
  - **Pacific OCS:** Separate production download exists under BOEM/BSEE Pacific Production if full US OCS coverage is desired.
- **Geospatial layers:**
  - `geo/wells`: `https://www.data.bsee.gov/Well/Files/BoreholeRawData.zip` (~3.5 MB, regenerated daily, ~57k boreholes with `API_WELL_NUMBER`, `WELL_NAME`, `SURF_LONGITUDE`/`SURF_LATITUDE`, `BOREHOLE_STAT_CD`).
  - `geo/facilities`: BOEM `Platforms` shapefile/geodatabase on `data.boem.gov/Main/Mapping.aspx` (updated frequently; all offshore structures) plus BSEE Platform Structure ASCII tables (`PlatStrucRawData` / `water_depth`, install/removal dates).
  - `geo/fields`: BOEM does **not** publish geological field polygons — only administrative `Active Lease Polygons`, `Blocks`, and `Unit Polygons` on `data.boem.gov/Main/Mapping.aspx`. Either leave `geo/fields` empty/metadata-only (using `FIELD_NAME_CODE` on production) or dissolve leases/units by field code as an approximation.
- **CRS & upstream naming quirks:**
  - BOEM Gulf mapping files store coordinates in **NAD 27** (`EPSG:4267`) — reproject explicitly to `EPSG:4326`.
  - **Naming watch-item:** In 2025/2026 BOEM/BSEE UI and metadata renamed "Gulf of Mexico (GOM/GOMR)" to **"Gulf of America (GOA/GOAR)"**. Verify file paths and keep our slug/label choice deliberate.
- **Scale & CI:** ~100 MB raw archive, plain HTTPS GET, no auth or WAF. Historical zips are regenerated on restatements (absorbed cleanly by v2 upserts). Very CI-friendly.
- **Licence:** US federal government work (public domain) — ideal for the public Hugging Face mirror.
- **Links:**
  - [BSEE Data Center — OGOR-A](https://www.data.bsee.gov/Main/OGOR-A.aspx)
  - [BOEM Geographic Mapping Data (SHP / GDB)](https://www.data.boem.gov/Main/Mapping.aspx)

---

### 4. Netherlands — NLOG (TNO / Geological Survey of the Netherlands)

- **Suggested slug:** `netherlands` (`NL`)
- **Why high priority:** Adds both **onshore** (including historic Groningen) and **Dutch North Sea** production *and* injection, with **all three v2 geospatial layers** available via a standard OGC WFS and direct GeoJSON/Shapefile downloads.
- **Production & injection:**
  - Monthly figures reported under Mining Decree (*Mbb* art. 111.1/112.1) since **January 1, 2003** (pre-2003 not in public domain), published within ~8 weeks of month-end via the NLOG Datacenter (`nlog.nl/en/data`).
  - Covers gas (`Nm³` at 0 °C, 101.325 kPa — note the ~5.5% reference-temperature difference vs `Sm³` at 15 °C!), crude oil (`Sm³`), condensate (`Sm³`), produced water, and **reservoir injection** (per borehole, field, facility, or licence).
  - *Needs live probe before coding:* verify exact bulk download URL/schema on NLOG's datacenter download interface (CSV/XLSX/API) vs interactive query tool.
- **Geospatial layers (all three verified live at `nlog.nl/en/files-interactive-map`):**
  - OGC WFS endpoint: `https://www.gdngeoservices.nl/geoserver/nlog/ows` (plus direct ZIP Shapefile & GeoJSON downloads!).
  - `geo/fields`: `gdw_ng_field_utm` / `NLOG_Fields_ED50UTM31N.zip` (filter to hydrocarbon fields; layer also includes geothermal & storage).
  - `geo/facilities`: `GDW_NG_FACILITY_UTM` / `NLOG_Facility_ED50UTM31N.zip` (all mining installations/platforms; filter to hydrocarbons).
  - `geo/wells`: `gdw_ng_wll_all_utm` / `NLOG_Boreholes_ED50UTM31N.zip` (all boreholes).
- **CRS & geometry quirks:**
  - Original CRS is **ED50** (`EPSG:4230`) on WFS and **ED50 / UTM 31N** (`EPSG:23031`) on Shapefile/GeoJSON downloads — must reproject to `EPSG:4326`.
  - NLOG explicitly documents invalid geometries on the fields WFS layer requiring geometry repair (`make_valid()`, already standard in `ogprod`).
- **Scale & CI:** Small-to-medium (hundreds of fields, ~6k boreholes). Very CI-friendly.
- **Licence:** Dutch public subsurface register (TNO / Ministry of Climate Policy and Green Growth; attribution required — verify datacenter terms).
- **Links:**
  - [NLOG Production and Injection Data](https://www.nlog.nl/en/production-and-injection-data)
  - [NLOG Interactive Map Files (WFS / SHP / GeoJSON)](https://www.nlog.nl/en/files-interactive-map)

---

### 5. USA — Alaska (AOGCC)

- **Suggested slug:** `us-ak` (`US`)
- **Why high priority:** By far the cleanest, richest, and easiest US state source — covers Prudhoe Bay, Kuparuk, Alpine, Willow, and Cook Inlet in a single **~30 MB daily bulk ZIP**.
- **Production & injection:**
  - Direct bulk table downloads on `commerce.alaska.gov/web/aogcc/data.aspx`, updated **daily**:
    - `wellproductionpost2000.zip` & `wellproductionpre2000.zip` (full monthly well-level production history)
    - `wellinjection.zip` (well-level injection!)
    - `naturalgasliquid.zip` (NGL records!)
    - `DataMinerAll.zip` (~30 MB bundle of all tables)
  - Includes well, pool/field, operator, and facility linkage.
- **Geospatial layers:**
  - `geo/wells`: `wells.zip` + `wellhistory.zip` (14-digit API numbers, surface & bottomhole coordinates, status, dates).
  - `geo/facilities`: `facilities.zip` (production/injection facilities).
  - `geo/fields`: Pool statistics exist; check Alaska DNR Division of Oil & Gas GIS (`dog.dnr.alaska.gov`) for unit/field polygons.
- **CRS quirk:** AOGCC explicitly states coordinates are in **NAD 27** (`EPSG:4267`) — reproject to `EPSG:4326`.
- **Scale & CI:** ~30 MB total for all history + injection + facilities + wells; trivial for CI and HF mirror.
- **Licence:** State of Alaska public records (public domain with standard disclaimer).
- **Links:**
  - [AOGCC Public Data & Bulk Extracts](https://www.commerce.alaska.gov/web/aogcc/data.aspx)

---

### 6. Colombia — ANH (`datos.gov.co`)

- **Suggested slug:** `colombia` (`CO`)
- **Why high priority:** Clean, credential-free Socrata API on Colombia's national open-data portal with explicit **CC BY-SA 4.0** licensing, covering ~750k bbl/d of South American production (Llanos, Magdalena, Putumayo, offshore Caribbean gas).
- **Production:**
  - Two consolidated monthly field-level datasets covering **2014–2026**, updated quarterly (last verified update June 2, 2026):
    - **Crude oil (`fdvb-hsrf`, ~53.2k rows):** `vigencia`, `mes`, `departamento`, `municipio`, `operadora`, `contrato`, `tipo_contrato`, `campo`, `longitud`, `latitud`, `produccion_bls`, `geolocalizacion`.
    - **Gas (`5dux-bfvx`, ~71.3k rows):** same schema with `produccionkpc` (thousand cubic feet = `Mcf`).
  - Exportable directly as CSV, JSON, or OData via Socrata (`https://www.datos.gov.co/resource/<id>.csv?$limit=...`).
  - **Quirks verified on live schema:**
    - `produccion_bls` and `produccionkpc` are typed as **Text** (must strip formatting/parse numerically).
    - Missing coordinates are encoded as **`(0, 0)`** (`0 = Referencia no Suministrada`) — must replace `(0, 0)` with `NULL` geometry.
    - `MunicipiosNN` appears for unallocated municipal records; aggregate/deduplicate carefully if a field spans multiple municipalities in the same month!
    - Does not include produced water or injection.
- **Geospatial layers:**
  - Field **point** coordinates are embedded in the Socrata tables (`latitud`/`longitud`), not polygons.
  - Contract/area polygons and wells live in ANH's *Geovisor de Tierras* (`geovisor.anh.gov.co/tierras/`) — needs probing for programmatic WFS/ArcGIS export; otherwise Colombia starts with field metadata/points or centroid-only until polygons are wired up (note: `geo/fields` currently requires `Polygon`/`MultiPolygon`, so check whether to source polygons from ANH Geovisor or store `geometry = NULL` initially).
- **Scale & CI:** Tiny (~125k rows combined), fast Socrata download, very CI-friendly.
- **Licence:** **Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0)**.
- **Links:**
  - [Producción Fiscalizada Crudo Consolidada (`fdvb-hsrf`)](https://www.datos.gov.co/Minas-y-Energ-a/Produccion-Fiscalizada-Crudo-Consolidada/fdvb-hsrf)
  - [Producción Fiscalizada Gas Consolidada (`5dux-bfvx`)](https://www.datos.gov.co/Minas-y-Energ-a/Produccion-Fiscalizada-Gas-Consolidada/5dux-bfvx)
  - [ANH Geovisor de Tierras](https://geovisor.anh.gov.co/tierras/)

---

## Tier 2 — Regional Completers & Medium-Effort Sources

### 7. Denmark — Danish Energy Agency (ENS)

- **Suggested slug:** `denmark` (`DK`)
- **Why useful:** Small, clean offshore dataset (~15–20 fields, including Dan, Halfdan, Tyra) that completes the **4-country North Sea picture** (`norway`, `uk`, `netherlands`, `denmark`).
- **Production & injection:**
  - Monthly field-level spreadsheets (`SI Units` and `Oil Field Units`) from **January 2018–present** (verified through September 2026; flagged as preliminary estimates).
  - Single historical **yearly** workbook (`1972–2025`, updated September 2026) containing production, **injection**, flare, fuel, and export in SI units (`m³` oil, `Nm³` gas).
  - *Limitation:* Pre-2018 monthly data is not linked on the current page (yearly only before 2018); parsing Excel workbooks requires `openpyxl`.
- **Geospatial layers (verified live at `ens.dk`):**
  - Direct Shapefiles in **ED50 / UTM 32N (`EPSG:23032`)**: `field delineations`, `Offshore Installations`, `exploration and appraisal wells`, `licences`, `block subdivision`.
  - Live **WFS services** for `Offshore installations`.
  - Additional well/subsurface WFS via GEUS (`eng.geus.dk`).
- **Scale & CI:** Tiny (< 5 MB total); CI-friendly once Excel layout stability is confirmed.
- **Links:**
  - [ENS Monthly and Yearly Production](https://ens.dk/en/energy-sources/monthly-and-yearly-production)
  - [ENS Shape Files & WFS for Oil and Gas Maps](https://ens.dk/en/energy-sources/oil-and-gas-related-data/shape-files-oil-and-gas-maps)

---

### 8. Canada — Offshore Newfoundland & Labrador (C-NLOER, ex-C-NLOPB)

- **Suggested slug:** `canada-nl` (`CA`)
- **Why useful:** A compact, high-profile North Atlantic offshore dataset covering 5 producing fields and 4 major offshore hubs (**Hibernia GBS**, **Hebron GBS**, **Terra Nova FPSO**, **White Rose / North Amethyst FPSO**) — far lighter and simpler than full onshore Petrinex.
- **Production:**
  - Monthly **well-level** (`Production Summary by Well`) and **field-level** (`Monthly Production Summary`) tables covering **1997–2026** (Hibernia 1997+, Terra Nova 2002+, White Rose 2005+, North Amethyst 2010+, Hebron 2017+).
  - Units already align cleanly with v2: Oil (`m³`), Gas (`10³ m³`), Water (`m³`), with explicit operator amendment dates.
  - **Quirk:** Published on `cnloer.ca/information/statistics/` as embedded HTML tables / PDFs rather than a single bulk CSV — requires scraping/parsing per-field-year pages or verifying downloadable CSV/XLSX links on the page.
- **Geospatial layers:**
  - C-NLOER's map-based Data and Information Hub publishes **Well Shapefiles** and **Active Licence Shapefiles** (and `Schedule of Wells`). The 4 offshore production hubs (2 GBS + 2 FPSO) are static and well-documented.
- **Links:**
  - [C-NLOER Statistical Information](https://www.cnloer.ca/information/statistics/)
  - [C-NLOER Maps & Data Hub](https://www.cnloer.ca/information/maps/)

---

### 9. Italy — MASE / UNMIG

- **Suggested slug:** `italy` (`IT`)
- **Why useful:** Surprisingly clean European onshore + Mediterranean/Adriatic offshore source with a single CSV and explicit **CC BY 4.0** licence.
- **Production:**
  - Single CSV (`anni 2004–2026`, updated Oct 27, 2026 through Aug 2026 — ~2-month lag) covering natural gas (`Sm³`), crude oil (`kg`), and condensate/*gasolina* (`kg`) per production concession (*concessione di coltivazione*), onshore and offshore.
  - **Unit caveat:** Crude oil and condensate are reported by **mass (`kg`)**, not volume! Converting `kg` → `bbl` requires either per-concession API/density data or a clearly documented standard density factor (e.g., 1 tonne ≈ 7.33 bbl / specific gravity ~0.855), which introduces ~5–10% error on heavy vs light crudes (Val d'Agri vs Sicily).
- **Geospatial layers:**
  - **WebGIS UNMIG** (hosted on Esri ArcGIS Online) exposes:
    - `geo/fields`: `Concessioni di coltivazione` (concession polygons with operator, equity shares, dates).
    - `geo/facilities`: `Piattaforme marine` (WGS84 points with **build year, structure type, water depth, linked wells**) and onshore `Centrali di raccolta e trattamento`.
    - `geo/wells`: `Pozzi produttivi`.
  - *Needs live probe:* locate the underlying ArcGIS FeatureServer URLs from WebGIS UNMIG and check direct CSV/XML exports on `elenco-dei-titoli-minerari`.
- **Licence:** **Creative Commons Attribution 4.0 (CC BY 4.0)**.
- **Links:**
  - [UNMIG Produzione nazionale di idrocarburi (CSV)](https://unmig.mase.gov.it/ricerca-e-coltivazione-di-idrocarburi/produzione-nazionale-di-idrocarburi/)
  - [WebGIS UNMIG](https://unmig.mase.gov.it/webgis-unmig/)

---

### 10. Canada — Western Provinces (Petrinex: Alberta, Saskatchewan, BC)

- **Suggested slugs:** `canada-ab`, `canada-sk`, `canada-bc` (`CA`)
- **Why high value (but higher effort):** Petrinex is the joint volumetric-reporting backbone for Canada's three major onshore producing provinces (hundreds of thousands of wells + batteries/gas plants), covering **production AND injection** (oil, condensate, NGL, marketable gas, water, water/gas/CO₂ injection).
- **Production & infrastructure (verified on `petrinex.ca/public-data/apd/`):**
  - Portal moved from legacy `/PD/Pages/APD.aspx` to `https://www.petrinex.ca/public-data/apd/`.
  - **Infrastructure CSVs (updated daily):** `Well Infrastructure`, `Well Licence`, `Facility Infrastructure`, `Facility Operator History`, `Well to Facility Link`, `Business Associate`, `Field and Pool Codes`.
  - **Volumetric CSV/XML (monthly):**
    - `Conventional Volumetric Data` on the main APD page provides a rolling **5-year window** (2022–2026 as of Nov 2026); deeper history back to 1962 requires AER's separate historical archives / ST3.
    - `NGL and Marketable Gas Volumes` covers 2016–2026.
    - Oil sands / in-situ bitumen has separate AER statistical reports (ST53 / ST39) outside the conventional volumetric extract.
  - **Amendment behavior:** Live timestamps show Petrinex continuously regenerates files **4+ years back** (2022–2026 files were all updated in Oct/Nov 2026) — a major test for incremental refresh and mirror churn.
- **Geospatial layers:** Well & facility legal locations use Canada's **DLS/NTS** grid (`UWI`) in Petrinex; lat/lon shapefiles come from provincial regulator GIS portals (AER ST37 / ArcGIS for AB, BCER Open Data for BC, Saskatchewan Mining & Petroleum GeoAtlas for SK).
- **Scale & licensing watch-items:**
  - Massive row volume (multi-GB raw CSVs per year across facilities/wells); will dominate Hugging Face mirror size and DuckDB-WASM well drill-down unless scoped carefully.
  - **Licence:** APD explicitly links to the **AER Public Access Copyright and Permission to Reproduce** terms — must review redistribution rights before publishing to a public mirror.
- **Links:**
  - [Petrinex Public Data Hub](https://www.petrinex.ca/public-data/)
  - [Petrinex Alberta Public Data (APD)](https://www.petrinex.ca/public-data/apd/)
  - [AER ST3 Statistical Report](https://www.aer.ca/data-and-performance-reports/statistical-reports/st3)

---

## Tier 3 — High-Effort, Fragmented, or Lower-Cadence Sources

### 11. United States — Onshore State Regulators

The US has **no unified national well-level database** (only state aggregates via EIA). Each state has a different agency, identifier format, download mechanism, and reporting grain:

- **California — CalGEM (*WellSTAR*):** Well-level monthly **production & injection** (oil, gas, water, steam/water/gas injection) back to 1977 via WellSTAR Public Downloads, plus clean **CC BY** GIS layers on `data.conservation.ca.gov` (`All Wells`, `Oil and Gas Field Administrative Boundaries`, `WellSTAR Facilities`). Probably the second-cleanest US state after Alaska.
- **North Dakota — DMR Oil & Gas Division (Bakken):** Monthly per-well production (~45-day lag). **Important 2026 caveat:** the official `mprindex.asp` page notes that historical Excel links were removed because amendments diverge from the official searchable PDFs; bulk tabular access now requires either scraping/parsing PDFs, third-party mirrors, or paid/subscription DMR Basic Services.
- **Texas — Railroad Commission (RRC):** Highest onshore US volume (Permian + Eagle Ford + Haynesville), but **highest engineering effort**:
  - Oil is reported at the **lease** level (multiple wells per lease), while gas is reported per well — does not fit `EntityType ∈ {well, field}` without adding `EntityType.LEASE` or lease-to-well allocation.
  - Production Data Query (PDQ) CSV dump is refreshed on the last Saturday of each month, while district ledgers are still distributed in **EBCDIC** across 14 district files.
  - Well GIS shapefiles are distributed **one county at a time across 254 counties**.
- **Colorado — ECMC (ex-COGCC):** Monthly well-level production downloads and GIS shapefiles exist via COGIS/ECMC, though several agency datasets remain PDF-heavy or inconsistently structured.
- **New Mexico — EMNRD OCD (Permian / San Juan):** Well locations and permitting data live on OCD Hub (`ocd-hub-nm-emnrd.hub.arcgis.com`) and the Water Data Act API, though several legacy ArcGIS Hub production dataset cards currently return `404` and require using the OCD Permitting search portal.
- **Other states:** Oklahoma (OCC well data + Oklahoma Tax Commission production totals), Wyoming (WOGCC), Pennsylvania (DEP — Marcellus shale), Louisiana (SONRIS), Utah, Kansas, Montana. See the [USGS directory of state well-data links](https://www.usgs.gov/core-research-center/links-state-well-data).

### 12. Australia — State Portals + Geoscience Australia (NOPIMS)

- **National (NOPIMS):** Geoscience Australia's [NOPIMS](https://www.ga.gov.au/nopims) covers offshore wells, completion reports, logs, and seismic surveys — **not** monthly production time series.
- **Queensland (`data.qld.gov.au`):** Publishes clean **6-monthly** CSV/XLSX production & reserve statistics (coal seam gas, crude oil, condensate, LPG, natural gas) per tenure/field — good open data, but semi-annual cadence does not align with v2's monthly schema.
- **Western Australia (DEMIRS / WAPIMS):** Major offshore LNG/oil province (North West Shelf, Carnarvon, Browse), but `WAPIMS` is primarily a well/report archive; monthly field-level bulk CSV downloads are limited/fragmented compared to North Sea portals. South Australia (PEPS) and Victoria maintain separate state systems.

### 13. New Zealand — NZP&M / MBIE

- Quarterly national production and **annual** field-level production/reserves profiles on MBIE's petroleum statistics pages.
- Well catalogue (including water depth) requires a free **RealMe login**, and MBIE web pages sit behind Imperva bot protection (poor CI fit).

### 14. Peru — Perupetro

- Publishes monthly *Producción Fiscalizada* for crude oil, NGL, and natural gas by contract lot (*Lote*, e.g. Camisea Lote 88/56, Talara, offshore Z-2B) on `perupetro.com.pe`.
- **Friction:** Distributed primarily as monthly **PDF reports** (exported from internal Excel sheets) under opaque IBM WebSphere WCM URLs rather than stable CSV/API endpoints.

### 15. Germany — LBEG (Lower Saxony)

- Publishes field-level oil and gas production in the annual *Erdöl und Erdgas in der Bundesrepublik Deutschland* report (**annual granularity only**, small volumes).

---

## Watchlist, Closed Sources & Macro Benchmarks

### Investigated & Currently Not Recommended

| Country | Publisher | Status & Reason |
|---|---|---|
| **Guyana** | Ministry of Natural Resources | High-interest offshore FPSO basin (Stabroek: Liza, Payara, Prosperity), but public per-FPSO tables have been irregular/stale and lack a stable machine-readable API. Monitor for an open-data release. |
| **Angola** | ANPG | Block-level monthly HTML summaries exist (2020+), but field/well data is login-gated. |
| **Indonesia** | SKK Migas | Portal behind Cloudflare/WAF; public reporting is contractor-level PDFs and annual EITI dashboards. |
| **Malaysia** | PETRONAS MyData | Field/well production sits behind a commercial paywall. |
| **India** | DGH NDR / PPAC | NDR well/production data is registration-gated and paid; PPAC publishes national/state PDF aggregates. |
| **OPEC & Russia** | Saudi Aramco, ADNOC, NIOC, PDVSA, NNPC, Rosstat, … | No public well- or field-level monthly production datasets; only country-level aggregates exist. |

### Country-Level Aggregates (Validation Only — Not Layer 1)

Useful for `ogprod audit` national total cross-checks rather than well/field ingestion:

- **US EIA** — state and national monthly crude & gas production (Form EIA-914) via a free [REST API v2](https://www.eia.gov/opendata/) (requires free API key).
- **JODI** (Joint Organisations Data Initiative) — monthly country-level oil & gas balances for ~100 countries.
- **OPEC** (Monthly Oil Market Report / Annual Statistical Bulletin) and **IEA** — country-level summaries.

---

## Architectural Decisions Before Adding Source #4+

Before implementing the next wave of connectors, decide on a few small cross-cutting conventions (record any schema changes in `docs/adr/`):

1. **Sub-national slugs & country rollups:** Adding `us-gom`, `us-ak`, `canada-nl`, or `canada-ab` means multiple `source` values share one `country_code` (`US` or `CA`). Verify whether `export_web.py` (`country_monthly` rollup) and `site/` source switchers group by `source` or `country_code`.
2. **Partial-geo sources:** Some strong sources lack field polygons (e.g. `us-gom` has leases/units not geological field polygons; `us-ak` has wells & facilities; `colombia` has field points in Socrata). `validate_geo` already allows `geometry = NULL` metadata rows — confirm how `export_web.py` and the map pane should represent point-only or polygon-less sources.
3. **CRS reprojection helper in `geo.py`:** BSEE/BOEM and Alaska AOGCC use **NAD 27** (`EPSG:4267`), while NLOG and Denmark ENS use **ED50** (`EPSG:4230` / `EPSG:23031` / `EPSG:23032`). Centralizing a tested `to_wgs84(gdf)` helper prevents silent datum offsets of 50–200 m.
4. **Mass-to-volume and reference-condition policies:**
   - **Gas reference conditions:** NLOG and ENS report `Nm³` (0 °C) vs `Sm³` (15 °C) in Sodir/ANP (~5.5% difference). Decide whether to convert `Nm³ → Sm³` in `units.py` or document the standard-condition nuance.
   - **Mass-reported crude:** Italy UNMIG reports `kg`; decide whether `ogprod` accepts assumed-density conversion or requires volumetric reporting.
5. **Mirror redistribution check:** Confirm licence compatibility before enabling `scripts/publish_mirror.py` for any new source (public domain / CC BY / CC BY-SA are clear; verify NSTA User Agreement, Argentine portal terms, and AER copyright rules).

### Recommended Expansion Waves

- **Option A — Complete the North Sea (`uk`, `netherlands`, `denmark`):** All three are compact, CI-friendly, and provide full field/facility/well spatial layers that pair directly with `norway` on the map — instantly making `ogprod` a complete public North Sea explorer (with injection data in UK, NL, and DK).
- **Option B — Expand the Americas (`argentina`, `us-gom`, `us-ak`, `colombia`):** Highest production volume and well-level granularity (`argentina` + `us-gom` + `us-ak` are all well-level; `argentina` was already built in v1 and `us-gom` is already spec'd), covering Vaca Muerta, the US Gulf, Alaska (with injection & NGL), and Colombia alongside Brazil and Mexico.
- **Option C — Mixed highest-ROI pair (`uk` + `us-gom` or `argentina`):** Adds a major new basin on each side of the Atlantic while testing both an ArcGIS Hub field-level source (`uk`) and a well-level source (`us-gom` or `argentina`) on v2's mirror & static site.
