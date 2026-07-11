"""Offline tests for connector ``fetch`` implementations.

All HTTP is served by ``httpx.MockTransport`` with canned publisher responses,
so these tests exercise the real URL discovery, download, and unpacking logic
without touching the network.
"""

import io
import zipfile

import httpx
import pytest

import ogprod

ARG_PACKAGE_SHOW = {
    "success": True,
    "result": {
        "resources": [
            {
                "format": "CSV",
                "name": "Producción de Pozos de Gas y Petróleo – 2024",
                "url": "http://datos.energia.gob.ar/dataset/c8/resource/aa/download/pozos-2024.csv",
            },
            {
                "format": "CSV",
                "name": "Producción de Pozos de Gas y Petróleo – 2025",
                "url": "http://datos.energia.gob.ar/dataset/c8/resource/bb/download/pozos-2025.csv",
            },
            {
                "format": "SHP",
                "name": "Capítulo IV - Pozos",
                "url": "http://datos.energia.gob.ar/dataset/c8/resource/cc/download/pozos.shp",
            },
        ]
    },
}


@pytest.fixture
def argentina_csv(data_dir) -> bytes:
    return (data_dir / "argentina_capitulo_iv_sample.csv").read_bytes()


@pytest.fixture
def argentina_client(argentina_csv) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/3/action/package_show":
            assert request.url.params["id"] == "produccion-de-petroleo-y-gas-por-pozo"
            return httpx.Response(200, json=ARG_PACKAGE_SHOW)
        if request.url.path.endswith("/download/pozos-2024.csv"):
            return httpx.Response(200, content=argentina_csv)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_argentina_fetch_resolves_year_resource(tmp_path, argentina_client):
    paths = ogprod.get("argentina").fetch(tmp_path, year=2024, client=argentina_client)

    assert [p.name for p in paths] == ["pozos-2024.csv"]
    assert paths[0].parent == tmp_path
    frame = ogprod.load("argentina", paths[0])
    assert set(frame["source"]) == {"argentina"}


def test_argentina_fetch_unknown_year(tmp_path, argentina_client):
    with pytest.raises(ValueError, match="2024"):
        ogprod.get("argentina").fetch(tmp_path, year=1990, client=argentina_client)


@pytest.fixture
def sodir_client(data_dir) -> httpx.Client:
    csv = (data_dir / "sodir_field_production_monthly_sample.csv").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "factpages.sodir.no"
        assert "field_production_monthly" in str(request.url)
        return httpx.Response(200, content=csv, headers={"content-type": "text/csv"})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_sodir_fetch_downloads_full_table(tmp_path, sodir_client):
    paths = ogprod.get("sodir").fetch(tmp_path, client=sodir_client)

    assert [p.name for p in paths] == ["field_production_monthly.csv"]
    frame = ogprod.load("sodir", paths[0])
    assert set(frame["source"]) == {"sodir"}


ANP_BASE = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/"
    "arquivos-producao-de-petroleo-e-gas-natural-por-poco"
)

# The real page mixes several filename conventions across years.
ANP_PAGE = f"""
<html><body>
<a href="{ANP_BASE}/producao-por-poco-2019.zip">2019</a>
<a href="{ANP_BASE}/2021_01_producao.zip">Janeiro</a>
<a href="{ANP_BASE}/2021-08-producao.zip">Agosto</a>
<a href="{ANP_BASE}/2022/producao-2022-02.zip">Fevereiro</a>
<a href="{ANP_BASE}/2022/2022_05_producao.zip">Maio</a>
<a href="{ANP_BASE}/2023/producao-01.zip">Janeiro</a>
<a href="{ANP_BASE}/metadados-dados-producao-poco.pdf">Metadados</a>
</body></html>
"""


@pytest.fixture
def anp_client(data_dir) -> httpx.Client:
    csv = (data_dir / "anp_pocos_sample.csv").read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2023_01_terra.csv", csv)
        archive.writestr("2023_01_mar.csv", csv)
        archive.writestr("leia-me.txt", "notas")
    zip_bytes = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".zip"):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(200, text=ANP_PAGE)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_anp_fetch_extracts_monthly_zip(tmp_path, anp_client):
    paths = ogprod.get("anp").fetch(tmp_path, year=2023, month=1, client=anp_client)

    assert sorted(p.name for p in paths) == ["2023_01_mar.csv", "2023_01_terra.csv"]
    frame = ogprod.load("anp", paths[0])
    assert set(frame["source"]) == {"anp"}


@pytest.mark.parametrize(
    ("year", "month"),
    [(2021, 1), (2021, 8), (2022, 2), (2022, 5)],
    ids=["2021-underscore", "2021-dash", "2022-nested", "2022-nested-underscore"],
)
def test_anp_fetch_matches_all_link_conventions(tmp_path, anp_client, year, month):
    paths = ogprod.get("anp").fetch(tmp_path, year=year, month=month, client=anp_client)
    assert len(paths) == 2


def test_anp_fetch_yearly_archive_without_month(tmp_path, anp_client):
    paths = ogprod.get("anp").fetch(tmp_path, year=2019, client=anp_client)
    assert len(paths) == 2
    assert all(p.parent == tmp_path for p in paths)  # single zip extracts flat


def test_anp_fetch_monthly_year_without_month_gets_all_months(tmp_path, anp_client):
    # 2021 has no yearly zip, only monthly ones (01 and 08 in the fixture page);
    # each period lands in its own subdirectory.
    paths = ogprod.get("anp").fetch(tmp_path, year=2021, client=anp_client)
    assert len(paths) == 4
    assert {p.parent.name for p in paths} == {"2021-01", "2021-08"}


def test_anp_fetch_without_year_downloads_everything(tmp_path, anp_client):
    # 6 zips on the fixture page x 2 CSVs each
    paths = ogprod.get("anp").fetch(tmp_path, client=anp_client)
    assert len(paths) == 12
    assert {p.parent.name for p in paths} == {
        "2019",
        "2021-01",
        "2021-08",
        "2022-02",
        "2022-05",
        "2023-01",
    }


def test_anp_fetch_recent_year_points_at_cdp(tmp_path, anp_client):
    with pytest.raises(ValueError, match="cdp.anp.gov.br"):
        ogprod.get("anp").fetch(tmp_path, year=2025, month=3, client=anp_client)


def test_module_level_fetch_dispatches(tmp_path, sodir_client):
    paths = ogprod.fetch("sodir", tmp_path, client=sodir_client)
    assert [p.name for p in paths] == ["field_production_monthly.csv"]
