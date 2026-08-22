from __future__ import annotations

import httpx

from app.services import databases
from tests.conftest import headers


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client(**kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(databases.httpx, "Client", client)


def test_rcsb_search_maps_results(monkeypatch):
    def handler(request):
        assert request.method == "POST"
        assert request.url.host == "search.rcsb.org"
        return httpx.Response(
            200,
            json={"result_set": [{"identifier": "1ABC", "score": 12.5}]},
        )

    _install_transport(monkeypatch, handler)
    result = databases.search("rcsb", "kinase")
    assert result["provenance"] == "RCSB PDB Search API"
    assert result["results"] == [
        {"id": "1ABC", "score": 12.5, "url": "https://www.rcsb.org/structure/1ABC"}
    ]


def test_pubchem_search_maps_properties(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "PropertyTable": {
                    "Properties": [
                        {"CID": 2244, "Title": "Aspirin", "MolecularFormula": "C9H8O4"}
                    ]
                }
            },
        )

    _install_transport(monkeypatch, handler)
    result = databases.search("pubchem", "aspirin")
    assert result["results"][0]["url"].endswith("/2244")
    assert result["results"][0]["Title"] == "Aspirin"


def test_pubchem_404_is_empty(monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(404))
    result = databases.search("pubchem", "unknown compound")
    assert result["count"] == 0
    assert result["results"] == []


def test_chembl_search_maps_molecules(monkeypatch):
    def handler(request):
        assert request.url.host == "www.ebi.ac.uk"
        return httpx.Response(
            200,
            json={
                "molecules": [
                    {
                        "molecule_chembl_id": "CHEMBL25",
                        "pref_name": "ASPIRIN",
                        "molecule_type": "Small molecule",
                        "max_phase": 4,
                        "molecule_structures": {
                            "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
                            "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                        },
                    }
                ]
            },
        )

    _install_transport(monkeypatch, handler)
    result = databases.search("chembl", "aspirin")
    item = result["results"][0]
    assert item["chembl_id"] == "CHEMBL25"
    assert item["canonical_smiles"].startswith("CC(=O)")
    assert item["url"].endswith("/CHEMBL25")


def test_invalid_database_search_inputs_are_bad_requests(client):
    response = client.get(
        "/api/databases/search",
        params={"source": "nope", "query": "protein"},
        headers=headers("viewer"),
    )
    assert response.status_code == 400
    response = client.get(
        "/api/databases/search",
        params={"source": "rcsb", "query": "x"},
        headers=headers("viewer"),
    )
    assert response.status_code == 400


def test_database_upstream_failure_is_bad_gateway(client, monkeypatch):
    def fail(source, query):
        raise databases.DatabaseSearchError("RCSB PDB search failed: unavailable")

    monkeypatch.setattr(databases, "search", fail)
    response = client.get(
        "/api/databases/search",
        params={"source": "rcsb", "query": "kinase"},
        headers=headers("viewer"),
    )
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]
