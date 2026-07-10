# Public oil & gas production data sources

Research survey of countries and organizations that publish oil and gas production
data, organized by granularity (well-level vs. field-level). This is the source
inventory for the `ogprod` package: each entry is a candidate data connector.

Last reviewed: July 2026.

## Summary

| Country / region | Organization | Granularity | Cadence | Access |
|---|---|---|---|---|
| Brazil | ANP | Well | Monthly | CSV downloads, open data portal, CDP query app |
| Argentina | Secretaría de Energía | Well | Monthly (updated daily) | CSV / OData on datos.energia.gob.ar |
| USA (federal offshore) | BSEE / BOEM | Well & lease | Monthly | Bulk ASCII/delimited files (OGOR-A) |
| USA (states) | TX RRC, ND DMR, CO ECMC, OK OCC, NM OCD, AK AOGCC, … | Well & lease | Monthly | Per-state query systems and bulk downloads |
| Canada (AB, SK, BC) | Petrinex / AER | Well & facility | Monthly | CSV/XML bulk downloads |
| Mexico | CNH (SIH/CNIH) | Well & field | Monthly | Dashboards, open data portal |
| Norway | Sodir (ex-NPD) | Field (well metadata public) | Monthly | FactPages web/API, full-table downloads |
| UK | NSTA | Field (well data via NDR) | Monthly | PPRS open data, ArcGIS/CKAN datasets |
| Netherlands | NLOG (TNO/Ministry) | Field / borehole | Monthly & yearly | Datacenter downloads, interactive map |
| Denmark | Danish Energy Agency (ENS) | Field | Monthly & yearly | Spreadsheet downloads |
| Colombia | ANH | Field | Monthly | Open data portal, statistics pages |
| Australia | Geoscience Australia (NOPIMS), Queensland Gov | Well data packs; field/basin production | Varies / 6-monthly | NOPIMS portal, data.qld.gov.au CSVs |
| New Zealand | NZP&M / MBIE | Field | Quarterly/annual | Statistics pages, geodata catalogue |
| World (aggregates) | US EIA, JODI, OPEC, IEA | Country/state | Monthly/annual | EIA REST API (free key), bulk files |

## Well-level sources (highest granularity)

### Brazil — ANP (Agência Nacional do Petróleo, Gás Natural e Biocombustíveis)

The reference you already know. ANP publishes monthly production per well since
2010, including volumes (oil, gas, water), field, basin, state, operator, API
gravity. Monthly publication comes as three files: onshore (terra), offshore
(mar), and pré-sal. Roughly a two-month reporting lag.

- [Dados de E&P](https://www.gov.br/anp/pt-br/assuntos/exploracao-e-producao-de-oleo-e-gas/dados-de-e-p) and [dados estatísticos](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-estatisticos)
- [Produção por poço on dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/producao-de-petroleo-e-gas-natural-por-poco)
- [CDP — Consulta Produção por Poço](https://cdp.anp.gov.br/ords/r/cdp_apex/consulta-dados-publicos-cdp/consulta-produ%C3%A7%C3%A3o-por-po%C3%A7o) (data from 2023 onward)

### Argentina — Secretaría de Energía ("Capítulo IV")

Monthly production per well (oil m³, gas thousands of m³, water m³) with field,
concession, and province, published as open data and refreshed frequently.
Includes a dedicated unconventional-well dataset (Vaca Muerta shale). One of the
best-structured national datasets — plain CSV per year plus OData.

- [Producción de petróleo y gas por pozo (Capítulo IV)](http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo)
- [Mirror on datos.gob.ar](https://datos.gob.ar/dataset/energia-produccion-petroleo-gas-por-pozo-capitulo-iv/archivo/energia_cb5c0f04-7835-45cd-b982-3e25ca7d7751)

### United States — federal offshore (BSEE/BOEM)

OGOR-A well production for the Gulf of Mexico and Pacific OCS, 1947–present
(bulk delimited files for 1996–current, historical archives before that).
Production by lease, well API number, and operator.

- [BSEE Data Center — OGOR-A](https://www.data.bsee.gov/Main/OGOR-A.aspx)
- [BSEE production information](https://www.data.bsee.gov/Main/Production.aspx)
- [Catalog entry on data.gov](https://catalog.data.gov/dataset/bsee-data-center-production-information)

### United States — state regulators (onshore)

The US has no single national well-level source; each state's oil & gas
regulator publishes its own. The major ones:

- **Texas — Railroad Commission (RRC)**: [Production Data Query (PDQ)](https://webapps2.rrc.texas.gov/EWA/ewaPdqMain.do) and [production data page](https://www.rrc.texas.gov/oil-and-gas/research-and-statistics/production-data/). Note Texas reports by lease (oil) / well (gas), not uniformly per well.
- **North Dakota — DMR Oil & Gas Division**: [monthly production report index](https://www.dmr.nd.gov/oilgas/mprindex.asp) (Bakken; per-well monthly files).
- **Colorado — ECMC**: [monthly well production downloads](https://ecmc.colorado.gov/data-maps/downloadable-data-documents/prod-well-download) and the [COGIS database](https://ecmc.colorado.gov/data-maps/cogis-database).
- **Oklahoma — Corporation Commission**: [Well Data Finder](https://oklahoma.gov/occ/divisions/oil-gas/database-search-imaged-documents/occ-well-data-finder.html) (production totals come from the Oklahoma Tax Commission).
- **New Mexico — OCD**: [well and production data](https://www.emnrd.nm.gov/ocd/) (Permian).
- **Alaska — AOGCC**: [Data Miner / data extracts](https://www.commerce.alaska.gov/web/aogcc/data.aspx) with production and injection records.
- Others: California (CalGEM), Wyoming (WOGCC), Pennsylvania (DEP), Louisiana (SONRIS), Utah, Kansas, Montana. USGS keeps a [directory of state well-data links](https://www.usgs.gov/core-research-center/links-state-well-data).

### Canada — Petrinex (Alberta, Saskatchewan, British Columbia)

Petrinex is the joint volumetric-reporting system for the three producing
provinces and publishes public data files: monthly well infrastructure and
volumetric (production/injection) data in CSV/XML. The all-Alberta conventional
file covers 1962–present. The AER layers statistical reports (ST3, ST53, etc.)
on top; BC Energy Regulator and Saskatchewan use Petrinex too.

- [Petrinex public data](https://www.petrinex.ca/PD/Pages/APD.aspx)
- [AER well production data](https://www1.aer.ca/productcatalogue/514.html) and [ST3](https://www.aer.ca/data-and-performance-reports/statistical-reports/st3)
- [Petrinex at BC Energy Regulator](https://www.bc-er.ca/energy-professionals/online-systems/petrinex/)

### Mexico — CNH (Comisión Nacional de Hidrocarburos)

The Sistema de Información de Hidrocarburos (SIH) exposes production statistics
down to well level, plus a national hydrocarbons map and open-data exports.

- [SIH](https://sih.hidrocarburos.gob.mx/) and [CNIH production](https://produccion.hidrocarburos.gob.mx/)
- [CNH on datos.gob.mx](https://datos.gob.mx/busca/organization/cnh)

## Field-level sources

### Norway — Sodir (Norwegian Offshore Directorate, ex-NPD)

FactPages is the gold standard for structured offshore E&P data: monthly and
yearly production per field (oil/gas/NGL/condensate/water) back to 1971, plus
complete wellbore, field, discovery, and licence registries — everything
downloadable as full tables (CSV/Excel) and synchronized daily from Sodir's
databases. Wellbore *metadata* is fully public; production volumes are
published at field level.

- [FactPages](https://factpages.sodir.no/) and [open data](https://www.sodir.no/en/facts/data-and-analyses/open-data/)

### United Kingdom — NSTA (North Sea Transition Authority)

Field-level monthly production via the Petroleum Production Reporting System
(PPRS), published through the NSTA open-data portal (ArcGIS), data.gov.uk, and
dashboards. Well-level daily production exists (reported under the Energy Act
2016 and released via the National Data Repository, with cessation-of-production
well data released publicly).

- [NSTA production data theme](https://www.nstauthority.co.uk/data-and-insights/data/themes/production/)
- [NSTA open data — production](https://editor.ogauthority.co.uk/data-centre/nsta-open-data/production/)
- [Field production (PPRS) on data.gov.uk](https://www.data.gov.uk/dataset/abb8fa96-dc3b-4b2d-a4ee-4c999ce18993/nsta-field-production-pprs-wgs843) and [UKCS daily production data](https://www.data.gov.uk/dataset/a7920e70-3441-43ec-ae77-bc1bc3a4d250/ukcs-daily-production-data9)

### Netherlands — NLOG

NLOG (run by TNO for the Ministry of Economic Affairs) publishes production and
injection data per field/facility, borehole registries, and full map layers for
the Netherlands and Dutch continental shelf. Well logs and cores become public
after a five-year confidentiality period.

- [Production and injection data](https://www.nlog.nl/en/production-and-injection-data) and [data center](https://www.nlog.nl/en/data)

### Denmark — Danish Energy Agency (ENS)

Monthly and yearly production per field for all Danish North Sea fields, based
on operator reports, plus the annual "Denmark's Oil and Gas Production" report.

- [Monthly and yearly production](https://ens.dk/en/energy-sources/monthly-and-yearly-production)

### Colombia — ANH (Agencia Nacional de Hidrocarburos)

Field-level oil and gas production statistics and royalties data, published on
the ANH site and Colombia's open-data portal (datos.gov.co).

- [Estadísticas de producción](https://www.anh.gov.co/es/operaciones-y-regal%C3%ADas/sistemas-integrados-operaciones/estad%C3%ADsticas-de-producci%C3%B3n/) and [datos y estadísticas](https://www.anh.gov.co/es/operaciones-y-regal%C3%ADas/datos-y-estadisticas/)

### Australia — NOPIMS + state portals

NOPIMS (Geoscience Australia) is the national offshore well/survey data system —
strong on well completion reports, logs, and seismic rather than monthly
production tables. Production statistics come from state portals; Queensland
publishes 6-monthly production per field/tenure (including coal seam gas) as
CSVs on its open-data portal. South Australia, WA, and Victoria have their own
systems (e.g. SA's PEPS).

- [NOPIMS](https://www.ga.gov.au/nopims)
- [Queensland petroleum & gas production statistics](https://www.data.qld.gov.au/dataset/petroleum-gas-production-and-reserve-statistics)

### New Zealand — NZP&M / MBIE

Quarterly/annual field-level petroleum production statistics and reserves data,
plus a free geodata catalogue of exploration data.

- [Petroleum statistics](https://www.nzpam.govt.nz/nz-industry/nz-petroleum/petroleum-statistics) and [petroleum reserves data](https://www.mbie.govt.nz/building-and-energy/energy-and-natural-resources/energy-statistics-and-modelling/energy-statistics/petroleum-reserves-data)

### Germany — LBEG (Lower Saxony)

LBEG publishes the annual "Erdöl und Erdgas in der Bundesrepublik Deutschland"
report with production per field (annual granularity only). Small industry but
long history.

## Country/state aggregates (no well or field detail)

Useful for validation and totals, not for well/field modeling:

- **US EIA** — state and national monthly production, with a free
  [REST API](https://www.eia.gov/opendata/) (registration key) and the
  [monthly crude oil and natural gas production series](https://www.eia.gov/petroleum/production/) (Form EIA-914).
- **JODI** (Joint Organisations Data Initiative) — monthly country-level oil and
  gas data for ~100 countries.
- **OPEC** (Annual Statistical Bulletin, Monthly Oil Market Report) and
  **IEA** — country-level.

Most OPEC members (Saudi Arabia, Iran, Iraq, Kuwait, UAE, Venezuela, Nigeria…)
and Russia do **not** publish well- or field-level production; country totals
via JODI/OPEC are effectively the only public data there.

## Implications for the `ogprod` package design

1. **Two canonical entities, one time-series shape.** Every source reduces to
   `(entity_id, entity_type ∈ {well, field}, period, product, volume, unit)` plus
   an entity registry (location, operator, basin, field↔well hierarchy). Design
   the core schema around that and make each source a connector that maps into it.
2. **Priority connectors by data quality/ease:** ANP (well, CSV), Argentina
   Capítulo IV (well, CSV/OData), Sodir FactPages (field, clean API-like tables),
   NSTA (field, ArcGIS/CSV), BSEE OGOR-A (well, bulk delimited), Petrinex (well,
   CSV), NLOG and ENS (field, spreadsheets). US states are higher effort — each
   has a different portal, format, and reporting basis (Texas reports oil by
   lease, not well).
3. **Units vary by jurisdiction:** m³ (Brazil, Argentina, Norway uses Sm³/scm),
   barrels/Mcf (US, Canada partially), Nm³ (Netherlands). Unit normalization
   must be first-class.
4. **Identifiers vary:** ANP well codes, US API numbers, Petrinex UWIs, Sodir
   NPDID. Keep source-native IDs and add a namespaced canonical ID.
5. **Reporting lag and revisions:** most sources lag 1–3 months and restate
   prior months (Petrinex ships amendments; Texas revises heavily). Ingestion
   should be idempotent upserts keyed on (entity, period), not append-only.
