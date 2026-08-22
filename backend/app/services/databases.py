"""Keyless public database lookups with explicit provenance."""

from __future__ import annotations

from urllib.parse import quote

import httpx


class DatabaseSearchError(RuntimeError):
    """An upstream database could not complete a search."""


def _json(response: httpx.Response, source: str) -> dict:
    try:
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DatabaseSearchError(f"{source} search failed: {exc}") from exc


def _rcsb(client: httpx.Client, query: str) -> list[dict]:
    payload = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": query},
        },
        "return_type": "entry",
        "request_options": {
            "results_content_type": ["experimental"],
            "paginate": {"start": 0, "rows": 8},
        },
    }
    data = _json(
        client.post("https://search.rcsb.org/rcsbsearch/v2/query", json=payload),
        "RCSB PDB",
    )
    return [
        {
            "id": item.get("identifier"),
            "score": item.get("score"),
            "url": f"https://www.rcsb.org/structure/{item.get('identifier')}",
        }
        for item in data.get("result_set", [])[:8]
    ]


def _pubchem(client: httpx.Client, query: str) -> list[dict]:
    url = (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{quote(query, safe='')}/property/"
        "Title,IUPACName,CanonicalSMILES,IsomericSMILES,MolecularFormula,MolecularWeight/JSON"
    )
    response = client.get(url)
    if response.status_code == 404:
        return []
    data = _json(response, "NIH PubChem")
    return [
        {
            **item,
            "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{item.get('CID')}",
        }
        for item in data.get("PropertyTable", {}).get("Properties", [])[:8]
    ]


def _chembl(client: httpx.Client, query: str) -> list[dict]:
    url = (
        "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json"
        f"?q={quote(query, safe='')}&limit=8"
    )
    data = _json(client.get(url), "EMBL-EBI ChEMBL")
    results = []
    for item in data.get("molecules", [])[:8]:
        chembl_id = item.get("molecule_chembl_id")
        results.append(
            {
                "chembl_id": chembl_id,
                "name": item.get("pref_name"),
                "type": item.get("molecule_type"),
                "max_phase": item.get("max_phase"),
                "canonical_smiles": item.get("molecule_structures", {}).get("canonical_smiles")
                if item.get("molecule_structures")
                else None,
                "standard_inchi_key": item.get("molecule_structures", {}).get("standard_inchi_key")
                if item.get("molecule_structures")
                else None,
                "url": f"https://www.ebi.ac.uk/chembl/explore/compound/{chembl_id}",
            }
        )
    return results


def search(source: str, query: str) -> dict:
    """Search one of the public structure and compound databases."""
    source = source.strip().lower()
    query = query.strip()
    if source not in {"rcsb", "pubchem", "chembl"}:
        raise ValueError("source must be one of: rcsb, pubchem, chembl")
    if len(query) < 2:
        raise ValueError("query must contain at least 2 characters")
    query = query[:120]

    try:
        with httpx.Client(timeout=15.0) as client:
            if source == "rcsb":
                results = _rcsb(client, query)
                provenance = "RCSB PDB Search API"
            elif source == "pubchem":
                results = _pubchem(client, query)
                provenance = "NIH PubChem PUG REST"
            else:
                results = _chembl(client, query)
                provenance = "EMBL-EBI ChEMBL Web Services"
    except DatabaseSearchError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise DatabaseSearchError(f"{source} search failed: {exc}") from exc

    return {
        "source": source,
        "query": query,
        "count": len(results),
        "results": results,
        "provenance": provenance,
    }
