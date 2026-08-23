"""Compute adapters validate payloads and never relabel malformed output as model evidence."""

from __future__ import annotations

import json

import httpx
import pytest

from app.compute.nim import NIMClient, NIMError
from app.compute.workflow import WorkflowError, normalize_policies, validate_required_providers
from app.config import Settings

PDB = """ATOM      1  CA  MET A   1       0.000   0.000   0.000  1.00 80.00           C
ATOM      2  CA  THR A   2       3.800   0.000   0.000  1.00 85.00           C
END
"""


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_alphafold_nim_sends_documented_payload_and_requires_real_coordinates():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"pdb": PDB, "model": "alphafold2"})

    prediction = NIMClient(
        alphafold_url="https://gpu.example",
        alphafold_key="provider-secret",
        client=_mock_client(handler),
    ).predict_structure("MT")

    assert seen["url"].endswith("/protein-structure/alphafold2/predict-structure-from-sequence")
    assert seen["body"]["sequence"] == "MT"
    assert seen["body"]["algorithm"] == "mmseqs2"
    assert seen["authorization"] == "Bearer provider-secret"
    assert prediction.pdb.startswith("ATOM")

    invalid = NIMClient(
        alphafold_url="https://gpu.example",
        client=_mock_client(lambda request: httpx.Response(200, json={"status": "ok"})),
    )
    with pytest.raises(NIMError, match="no valid PDB"):
        invalid.predict_structure("MT")


def test_proteinmpnn_nim_applies_fixed_positions_and_parses_multifasta():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"mfasta": ">native\nMT\n>design-1\nMK\n", "scores": [-1.0, -0.8]},
        )

    prediction = NIMClient(
        proteinmpnn_url="https://gpu.example/biology/ipd/proteinmpnn/predict",
        client=_mock_client(handler),
    ).design_sequences(
        PDB,
        fixed_positions=[1],
        num_sequences=2,
        temperature=0.1,
        random_seed=37,
    )

    assert seen["url"].endswith("/biology/ipd/proteinmpnn/predict")
    assert json.loads(seen["body"]["fixed_positions_jsonl"]) == {"input": {"A": [1]}}
    assert prediction.sequences == [("native", "MT"), ("design-1", "MK")]
    assert prediction.scores == [-1.0, -0.8]


def test_compute_policy_defaults_and_required_provider_validation():
    assert normalize_policies({}) == {"alphafold": "auto", "proteinmpnn": "auto"}
    settings = Settings(alphafold_api_url=None, proteinmpnn_api_url=None)
    with pytest.raises(WorkflowError, match="ALPHAFOLD_API_URL"):
        validate_required_providers({"alphafold": "required", "proteinmpnn": "auto"}, settings)
