# ANP historical production (1941–2004) via the Wayback Machine — design

Extend the `anp` connector so it can fetch and parse ANP's **pre-2005**
well-level monthly production. ANP no longer publishes these files, but the
Internet Archive keeps byte-identical copies. No schema or store changes.

All findings below were checked empirically on 2026-10-07: the live ANP page,
Wayback CDX queries, a raw Wayback download, the live `producao-por-poco-2005.zip`,
and a local archive of the original files from 2019 (`../anp-dados-abertos/Dados`).

## Background

- From roughly 2018 to 2019, ANP published per-well production for **1941–2019**
  as plain CSVs at `http://www.anp.gov.br/images/dadosabertos/producao/producao-{terra,mar}-<span>.csv`.
  Onshore files are multi-year up to 1988, half-yearly from 1989 to 2015, then
  quarterly. Offshore files are multi-year.
- Today the [gov.br page](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/producao-de-petroleo-e-gas-natural-por-poco)
  links only yearly zips for **2005–2020** and monthly zips for 2021–2023, which
  the connector already fetches. Every old URL returns 404, including guesses
  under the current gov.br path. **Nothing before 2005 is served by ANP any more.**
- The Wayback Machine captured all 91 files in that folder (plus
  `metadados-producao-oleo-gas.pdf`) on **2018-08-27**, and again roughly every
  two months until late 2019. For the 50 files that only cover months before
  2005 (251 MB, 2.22 M rows, 13,777 wells), the 2018-08-27 capture is
  **byte-identical** to the local archive (Wayback SHA-1 digest = local SHA-1).
- Raw download works without the Wayback toolbar or URL rewriting:
  `https://web.archive.org/web/<timestamp>id_/http://www.anp.gov.br:80/images/dadosabertos/producao/<file>`
  (the `id_` suffix serves the original bytes). Verified with
  `producao-terra-1941-1966.csv`: HTTP 200, 4.87 MB, SHA-1 matches.
- The CDX API rate-limits quickly (it returned HTTP 429 after a few calls). So
  the manifest is frozen into the code; we never look it up at runtime.

## Decisions (proposed — need user sign-off)

1. **Same source, not a new one.** The data is ANP's, `entity_id` is the same
   ANP well name (verified below), and the periods don't overlap the current
   files. So it lives in `sources/anp.py` as a fetch path for older years plus
   a parse branch for the old format. Production and entities keep joining as
   they do today.
2. **Only pre-2005 files.** From 2005 on, the current zips are authoritative,
   and they carry restatements made after 2019. The archived 2005+ files are
   never fetched. This also means the two formats never overlap, so there are
   no double-counted months.
3. **Pinned capture plus checksum.** Each file is fetched from one fixed Wayback
   timestamp, and its SHA-1 is checked against a manifest frozen in the code. If
   the checksum doesn't match, the download is deleted and the fetch raises.
   This keeps the fetch reproducible and immune to later captures of the same URL.
4. **Injection columns are dropped**, as they are for every other source. The
   canonical table has no injection product (see open question 2).

## fetch

- `fetch("anp", dest, year=Y)` with `Y < 2005` resolves Y to the one manifest
  file per environment (terra, mar) whose span covers Y, downloads it from
  Wayback, verifies it, and writes it flat into `dest` under its original
  filename. `month=` is accepted and narrows nothing, because files are
  multi-month; the docstring says so. Onshore 1989–2004 is split into two
  half-year files, so a year can mean 3 files: both onshore halves plus the
  offshore file.
- `fetch("anp", dest)` keeps its current meaning (2005–2023), so existing
  users don't download an extra 251 MB without asking for it. Getting the full
  history is an explicit opt-in: `fetch("anp", dest, historical=True)` fetches
  all 50 files into `dest/historical/`. The CLI exposes this as
  `just fetch anp --historical`.
- The manifest is a module-level tuple `_HISTORICAL_FILES` of
  `(filename, first_period, last_period, wayback_timestamp, sha1_base32, size)`.
  It is generated once from the CDX listing plus local hashes (appendix A),
  and the module docstring records where it came from. **Spans come from
  each file's contents, not its name.** `producao-terra-1970-1974.csv`
  actually starts at 1971-01, and 1970 is entirely in
  `producao-terra-1967-1970.csv`. The manifest generator reads min/max
  `Mês/Ano` for each file.
- Politeness and robustness: download sequentially with a descriptive
  `User-Agent`. Retry HTTP 429/503 with backoff (honour `Retry-After`, 3
  attempts). After a download, also compare the byte count with the manifest,
  so a truncated body is caught before the hash check.
- Mirror fallback: `_HISTORICAL_MIRROR` (default `None`) points to a Zenodo
  record with the same bytes. When it's set, the mirror is tried after Wayback
  fails, and the same checksums apply. See the
  [Zenodo mirror spec](2026-10-07-anp-historical-zenodo-mirror-design.md).
- Injectable `httpx.Client` as for every other fetch.

## parse

### Detecting the old format

`parse` branches on the first line. The old format has a UTF-8 BOM, is
**comma-delimited**, and has exactly these 21 header names:

`Ano, Mês/Ano, Estado, Bacia, Campo, Poço, Ambiente, Instalação`, then
`Produção de Óleo (m³)`, `Produção de Condensado (m³)`,
`Produção de Gás Associado (Mm³)`, `Produção de Gás Não Associado (Mm³)`,
`Produção de Água (m³)`, then 8 `Injeção de …` columns.

Decimals are commas inside quoted fields (`"624,251"`), and empty cells are
null. Today, an old file is rejected with "no well name column" because the
reader assumes semicolons. Detect the old format with
`first_line.count(",") > first_line.count(";")` and `"mes/ano"` among the
normalized headers. Read it with `sep=",", decimal=","`. Nothing else in ANP's
corpus has that header, so detection is unambiguous.

### Columns

| Column | Use |
|---|---|
| `Mês/Ano` (`MM/YYYY`) | `period` (month start) |
| `Poço` | `entity_id` / `entity_name` (already ANP-style names, e.g. `1-C-1-BA`; no padded operator codes in any pre-2005 file) |
| `Campo` | `field` |
| `Bacia` | `basin` |
| `Estado` | `region`, and also used for dedup (below) |
| `Instalação` | aggregation input only (`Não Informado` for almost all onshore rows) |
| `Ambiente`, `Ano` | ignored |
| Óleo (m³) | oil, **monthly m³** (not a daily rate; checked against 2005 below) |
| Condensado (m³) | condensate, m³ |
| Gás Associado + Gás Não Associado (Mm³) | gas, **e3m3**: the two columns are summed into one gas row, as the current files do with their total-gas column |
| Água (m³) | water, m³ |
| `Injeção de …` | ignored |

`operator` is null, because these files don't carry it. Volume headers go
through the existing `_PRODUCT_PREFIXES`/`_UNIT_ALIASES` after stripping the
leading `produção de `. `Mm³` already maps to `e3m3`, so `units.py` needs no
changes.

### Known defects

Both defects, and how they're handled, are specified in the
[ANP data-quality spec](2026-10-07-anp-data-quality-design.md). That spec also
covers the same defect in the current 2005 and 2019-05 zips:

- **Repeated well-months** (about 204k rows). These come from a state
  cross-join on field names that exist in two states, plus a few installation
  splits in `producao-mar-2001-2004.csv`. They are merged per product,
  preferring the row whose `Estado` matches the well's state code. They are
  not dropped and not summed. In 2003–2004 the copies split oil and gas
  between them, so dropping a copy would lose gas.
- **Gas gap 2003-07 … 2005-06.** Only about 600 of about 8,300 wells report
  gas. This is flagged through `KNOWN_GAPS` and a warning, never imputed.

Oil + condensate after the merge: 1960 4.67 Mm³, 1970 9.54, 1980 10.53,
1990 36.59, 2000 71.25, 2004 85.99 Mm³ (≈1.48 Mbbl/d). Left in, the repeated
rows inflate totals by 0.2–1.3%.

### Joining up with the 2005+ data (verified on 2005, where both exist)

- Well names: 10,925 of the 10,975 wells in the archived 2005 files exactly
  match the `Nome Poço ANP` column of the live 2005 zip (99.5%). The 50
  unmatched (e.g. `7-AG-352P-BA`, `6-CRT-7-RJS`) are wells renamed or
  re-suffixed later. Wells active across 2004/2005 therefore keep one
  `entity_id`, apart from a small tail we accept and record in
  follow-ups.md.
- Volumes: 2005 oil in the archived files is 94.73 Mm³, against 94.55 Mm³ from
  the live zip after converting bbl/d to monthly m³ and removing its duplicate
  rows. The ratio is 0.998, which confirms that the old volumes are monthly
  m³, not daily rates. ANP's own 2018 column dictionary
  (`metadados-producao-oleo-gas.pdf`, Wayback 20181101232314) confirms it:
  volumes are monthly m³, and gas is in thousand m³.

## Tests

- Fixtures `tests/data/anp_historical_terra.csv` and `anp_historical_mar.csv`:
  a few real rows each, copied byte-for-byte including the BOM and quoted
  comma decimals. They must include a state cross-join pair (identical and
  split volumes), a 2003 rounding pair and an installation-split pair.
- `parse`: column mapping, monthly m³ volumes, gas = associated + non-associated.
  Merge behaviour is tested as in the data-quality spec.
- Rejection test: a current-format file is not detected as the old format, and
  the reverse.
- `fetch` via `MockTransport`: the year → file(s) mapping (1966 → one terra
  file + one mar file; 1995 → two terra halves + one mar file), the exact
  Wayback `id_` URL requested, a checksum mismatch raises and leaves no file
  behind, a 429 is retried, and `year >= 2005` still goes to the gov.br path.
- `test_live_smoke.py`: send a `HEAD` request (or a range GET of the first
  bytes) for the smallest manifest file through Wayback, so we notice if the
  archive stops serving it. Don't download all 251 MB weekly.

## Docs

- `docs/downloading.md`: document the `anp` historical fetch, the Wayback
  source, and the fact that `fetch("anp", dest)` without opting in excludes
  history.
- `docs/design.md`: per-connector notes covering the old format, the dedup
  rules, and the manifest.
- `docs/follow-ups.md`: the watch item "Wayback availability", and the ~0.5%
  of wells renamed across 2004/2005.
- `sources/anp.py` module docstring: the dead original endpoint, the Wayback
  endpoint, and the capture timestamp.

## Redistribution

Licence analysis, the Zenodo vs. Kaggle comparison, and the mirror design are
in the [Zenodo mirror spec](2026-10-07-anp-historical-zenodo-mirror-design.md).
In short: federal open-data rules (Decreto 8.777/2016, Resolução CGINDA
2/2017) allow redistribution with attribution. Zenodo is the mirror because
`fetch` can download from it anonymously. Kaggle is optional, for visibility
only.

## Open questions

1. Is `historical=True` the right opt-in, or should `fetch("anp", dest)` with
   no arguments mean "everything, 1941–2023"?
2. Injection volumes, which also exist in Sodir and BSEE: a future canonical
   `injection` table? Out of scope here.

## Appendix A — manifest (50 files, 251 MB)

Wayback URL = `https://web.archive.org/web/<timestamp>id_/http://www.anp.gov.br:80/images/dadosabertos/producao/<file>`.
SHA-1 is base32-encoded, as Wayback's CDX `digest` field reports it.

| File | Wayback timestamp | SHA-1 (base32) | Bytes |
|---|---|---|---|
| `producao-mar-1941-1979.csv` | 20180827204558 | `BSJONFL2ITZEOL5PBQMG4XVUPS5D4TPH` | 6535771 |
| `producao-mar-1980-1988.csv` | 20180827205813 | `IBDICOCGJTKPWHQRRRGIYEUFCTS4V4Z7` | 6750751 |
| `producao-mar-1989-1993.csv` | 20180827202314 | `37SV63Y5OR63ZAF7NW62CXU7WXBQMFHA` | 5236713 |
| `producao-mar-1994-1997.csv` | 20180827205518 | `KSI53FYVNOE4MRLXMEQMGBCHQ7XJLE5G` | 4735777 |
| `producao-mar-1998-2000.csv` | 20180827205525 | `X4YTHKPPKYT4OHWRVRYEWQDUQBCBDKPW` | 3182984 |
| `producao-mar-2001-2004.csv` | 20180827205124 | `ORLGN3VOU6GHKTKCV7ZQQ5CNCQR25IOX` | 4654630 |
| `producao-terra-1941-1966.csv` | 20180827205606 | `W2MZL4EBAOMQQ3AXNC7AJNHCETX6N7OT` | 4872449 |
| `producao-terra-1967-1970.csv` | 20180827205129 | `3EOQ23FFWCRB47ODGFPL2B5ZBIFF5OZE` | 3856664 |
| `producao-terra-1970-1974.csv` | 20180827210214 | `EYVJFX4ERSVEGR6WRN6CZHZAG42UN2ZO` | 4877268 |
| `producao-terra-1975-1977.csv` | 20180827205618 | `T4RI4RTUGYSJ6GFFII7KRW4LRANHOA5B` | 4180246 |
| `producao-terra-1978-1980.csv` | 20180827205140 | `2TNNV2RUOPYTYWAIE5M6O7XF6N76373P` | 5335931 |
| `producao-terra-1981-1982.csv` | 20180827205639 | `NTWARULLJAB72LWHQ3VWZFPUDLDTVVSS` | 5310138 |
| `producao-terra-1983.csv` | 20180827210218 | `RXDX5RYKU23AC6PTFO6UWDD4A754R4WW` | 3959490 |
| `producao-terra-1984.csv` | 20180827205819 | `MNRKPYJNRFFQBEODK2ER7ZDPAYZKYMUF` | 4641642 |
| `producao-terra-1985.csv` | 20180827204611 | `4QAXJGS2IMC55UJ56OJ6ZWGNMZEWGXEV` | 5437144 |
| `producao-terra-1986.csv` | 20180827210225 | `YSDLUBPNBD56IZB4UMCAZQYBB256DQQ3` | 5990159 |
| `producao-terra-1987.csv` | 20180827205825 | `VZZY54X76W2ITTFXSDH3YPGODZJEDKKY` | 6688115 |
| `producao-terra-1988.csv` | 20180827204835 | `S3FS3ALEEI6LC3LMRKAGWR24LZD7NDWL` | 7317510 |
| `producao-terra-1989-1sem.csv` | 20180827204841 | `PHDQ2ATBO6NT7SK7MNLCI76NATM4QWSH` | 3862301 |
| `producao-terra-1989-2sem.csv` | 20180827204617 | `TCUWKQHNQGJSDMVA6GX7KEHH6U73Z53J` | 3869223 |
| `producao-terra-1990-1sem.csv` | 20180827204203 | `H7Z2ABSW56T7YMOVW2IFHTATFC2DRLNK` | 3865778 |
| `producao-terra-1990-2sem.csv` | 20180827204627 | `PCTYWGHND4FJUQUEI3DCKSLX4GTAVRIB` | 3895772 |
| `producao-terra-1991-1sem.csv` | 20180827204208 | `LIO627H4XONNOFCR7AQIGYGTGLK755XK` | 3925838 |
| `producao-terra-1991-2sem.csv` | 20180827204212 | `CS2H4BX2FZZ4TTLDNDJIC5IGL23UYUSA` | 4045769 |
| `producao-terra-1992-1sem.csv` | 20180827205836 | `XPY6R3ILBSYHOI4V3GOY5EBFXHQL74W4` | 4164381 |
| `producao-terra-1992-2sem.csv` | 20180827202327 | `ZQ52E4WWPXYALRV7FTEWRS7WRMOV3KRH` | 4389863 |
| `producao-terra-1993-1sem.csv` | 20180827205845 | `KTDYE322Q2V7YJUX2SC25VIMMVPUQ5I6` | 4460735 |
| `producao-terra-1993-2sem.csv` | 20180827204632 | `VDLPRPCUJMROEMNQB6FXKYD2LXUR4O5G` | 4534672 |
| `producao-terra-1994-1sem.csv` | 20180827202339 | `RWJOJTNV2BSZJ25WDM7PUCGMYF3OTQTT` | 4596390 |
| `producao-terra-1994-2sem.csv` | 20180827205852 | `X4IGYJJPXCPZ2VLNPISYZFARJOOZFBQA` | 4685568 |
| `producao-terra-1995-1sem.csv` | 20180827210230 | `AAREOYGEZX2BRP5ESJV3IDOYZVIB4GHL` | 4746839 |
| `producao-terra-1995-2sem.csv` | 20180827204846 | `BJWPUI4DY2EWK6RBN3H5D3DJ2ACMBPRP` | 4858297 |
| `producao-terra-1996-1sem.csv` | 20180827205912 | `QB6VJKNKKCPGKNGJIPLIN7DADJF7HEDQ` | 4917155 |
| `producao-terra-1996-2sem.csv` | 20180827205155 | `OLRLTAETJFTXZBBPLBWBOSTA6YCHGUM4` | 4919773 |
| `producao-terra-1997-1sem.csv` | 20180827204221 | `J2MMITFRYIVMOAMP7PK4PRHXEJDQRSZA` | 4956134 |
| `producao-terra-1997-2sem.csv` | 20180827202350 | `XFONEFWX6FYVZ2IP7NIZFNPDPLCRQC63` | 4993974 |
| `producao-terra-1998-1sem.csv` | 20180827205209 | `D4D2SFGFLDCZGIVZBDXIPAN7LWL446NB` | 5032747 |
| `producao-terra-1998-2sem.csv` | 20180827210235 | `MWASPHOH5JN5MP3D52A2WZNZIP4GGBVR` | 5050058 |
| `producao-terra-1999-1sem.csv` | 20180827205650 | `E2XORYTUFBAOLP6HH56BLRBTCIMQDKSC` | 4977741 |
| `producao-terra-1999-2sem.csv` | 20180827202400 | `KBIL7NJJELEGLWFCROH2C6BRC5DIFF6N` | 4968726 |
| `producao-terra-2000-1sem.csv` | 20180827210240 | `PGNEC4YS6BOTHQEGWSPOHKZN4Q5EY6E2` | 4960197 |
| `producao-terra-2000-2sem.csv` | 20180827204227 | `73ZXJNUFSEJQCVIVJV5IHIML7AVWY6OZ` | 5017862 |
| `producao-terra-2001-1sem.csv` | 20180827204637 | `VFVJPMSSEGHP5LLGXVLWFDB4VDOFLJ67` | 5185137 |
| `producao-terra-2001-2sem.csv` | 20180827204852 | `QRLOQOFMKMZKFJHTO7C2GMTLYBXTYZLE` | 5312765 |
| `producao-terra-2002-1sem.csv` | 20180827205705 | `ZYJKZUSNXUL7AJZ4MESMVWNTKW3JD5TG` | 5432951 |
| `producao-terra-2002-2sem.csv` | 20180827202406 | `AO6NV5JJ4IKCP5MTP24MH7XNZQ52TSY5` | 5557056 |
| `producao-terra-2003-1sem.csv` | 20180827204900 | `KVVU2IKSWHXIAV4S6QMAAAESWJ2MIZQW` | 5976124 |
| `producao-terra-2003-2sem.csv` | 20180827204908 | `IQE4NAJWPMX5EDH2KFVVSCINCXXAXOPA` | 6659655 |
| `producao-terra-2004-1sem.csv` | 20180827205719 | `M2ORXYUXFB7INDXACB3P3GO4NCQY7EKF` | 6870274 |
| `producao-terra-2004-2sem.csv` | 20180827205933 | `RHORZ2LCKZPRDXYGP76W3664C423BPI3` | 7019260 |
