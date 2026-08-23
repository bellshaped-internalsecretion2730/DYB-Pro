"""Small, synchronous clients for NVIDIA BioNeMo NIM protein services.

The surrounding design cycle already runs in Celery, so these intentionally block the worker and
never an HTTP request. Raw model output is validated before it can be labelled as AlphaFold or
ProteinMPNN evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

ALPHAFOLD_PATH = "/protein-structure/alphafold2/predict-structure-from-sequence"
PROTEINMPNN_PATH = "/biology/ipd/proteinmpnn/predict"


class NIMError(RuntimeError):
    """A configured NIM was unreachable or returned invalid model output."""


@dataclass(frozen=True)
class AlphaFoldPrediction:
    pdb: str
    response_metadata: dict[str, Any]


@dataclass(frozen=True)
class ProteinMPNNPrediction:
    mfasta: str
    sequences: list[tuple[str, str]]
    scores: list[Any]
    response_metadata: dict[str, Any]


def _endpoint(base_or_endpoint: str, path: str) -> str:
    value = base_or_endpoint.rstrip("/")
    return value if value.endswith(path) else f"{value}{path}"


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> Any:
    try:
        response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise NIMError(f"compute provider request failed: {exc}") from exc
    if response.status_code >= 400:
        raise NIMError(f"compute provider returned HTTP {response.status_code}: {response.text[:500]}")
    try:
        return response.json()
    except ValueError:
        # Some compatible gateways return a PDB/FASTA body directly.
        return response.text


def _find_pdb(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.replace("\\n", "\n") if "\\n" in value and "\n" not in value else value
        if any(line.startswith(("ATOM", "HETATM")) for line in text.splitlines()):
            return text
        return None
    if isinstance(value, dict):
        for key in ("pdb", "structure", "output", "result", "prediction"):
            if key in value and (found := _find_pdb(value[key])):
                return found
        for item in value.values():
            if found := _find_pdb(item):
                return found
    if isinstance(value, list):
        for item in value:
            if found := _find_pdb(item):
                return found
    return None


def _metadata(value: Any) -> dict[str, Any]:
    """Keep small scalar provenance while excluding raw coordinates/probability tensors."""
    if not isinstance(value, dict):
        return {}
    excluded = {"pdb", "structure", "output", "result", "prediction", "mfasta", "probs"}
    return {
        str(key): item
        for key, item in value.items()
        if key not in excluded and isinstance(item, str | int | float | bool | type(None))
    }


def _parse_fasta(text: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    header = ""
    chunks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if chunks:
                records.append((header or f"design-{len(records) + 1}", "".join(chunks)))
            header, chunks = line[1:].strip(), []
        else:
            chunks.append(line)
    if chunks:
        records.append((header or f"design-{len(records) + 1}", "".join(chunks)))
    return records


class NIMClient:
    def __init__(
        self,
        *,
        alphafold_url: str | None = None,
        alphafold_key: str | None = None,
        alphafold_timeout: float = 3600.0,
        proteinmpnn_url: str | None = None,
        proteinmpnn_key: str | None = None,
        proteinmpnn_timeout: float = 900.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.alphafold_url = alphafold_url
        self.alphafold_key = alphafold_key
        self.alphafold_timeout = alphafold_timeout
        self.proteinmpnn_url = proteinmpnn_url
        self.proteinmpnn_key = proteinmpnn_key
        self.proteinmpnn_timeout = proteinmpnn_timeout
        self._client = client

    def predict_structure(self, sequence: str) -> AlphaFoldPrediction:
        if not self.alphafold_url:
            raise NIMError("AlphaFold provider is not configured")
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.alphafold_timeout)
        try:
            payload = {
                "sequence": sequence,
                "databases": ["uniref90", "mgnify", "small_bfd"],
                "algorithm": "mmseqs2",
                "relax_prediction": True,
            }
            raw = _request_json(
                client,
                _endpoint(self.alphafold_url, ALPHAFOLD_PATH),
                payload,
                _headers(self.alphafold_key),
            )
            pdb = _find_pdb(raw)
            if not pdb:
                raise NIMError("AlphaFold provider returned no valid PDB coordinates")
            return AlphaFoldPrediction(pdb=pdb, response_metadata=_metadata(raw))
        finally:
            if owns_client:
                client.close()

    def design_sequences(
        self,
        pdb: str,
        *,
        fixed_positions: list[int],
        num_sequences: int,
        temperature: float,
        random_seed: int,
    ) -> ProteinMPNNPrediction:
        if not self.proteinmpnn_url:
            raise NIMError("ProteinMPNN provider is not configured")
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.proteinmpnn_timeout)
        try:
            payload: dict[str, Any] = {
                "input_pdb": pdb,
                "ca_only": False,
                "use_soluble_model": False,
                "random_seed": random_seed,
                "num_seq_per_target": num_sequences,
                "sampling_temp": [temperature],
            }
            if fixed_positions:
                payload["fixed_positions_jsonl"] = json.dumps({"input": {"A": sorted(set(fixed_positions))}})
            raw = _request_json(
                client,
                _endpoint(self.proteinmpnn_url, PROTEINMPNN_PATH),
                payload,
                _headers(self.proteinmpnn_key),
            )
            if isinstance(raw, dict):
                mfasta = str(raw.get("mfasta") or "")
                scores = list(raw.get("scores") or [])
            else:
                mfasta, scores = str(raw), []
            records = _parse_fasta(mfasta)
            if not records:
                raise NIMError("ProteinMPNN provider returned no valid multi-FASTA records")
            return ProteinMPNNPrediction(
                mfasta=mfasta,
                sequences=records,
                scores=scores,
                response_metadata=_metadata(raw),
            )
        finally:
            if owns_client:
                client.close()
