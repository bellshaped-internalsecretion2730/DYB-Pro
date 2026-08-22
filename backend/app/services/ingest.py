"""Upload ingestion: FASTA / PDB / mmCIF / CSV -> artifacts + root commits."""

from __future__ import annotations

import csv
import io
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artifact, MeasuredResult, Project, ProteinCommit
from app.storage import store
from app.toolkit import sequence as seqlib
from app.toolkit import structure as structlib
from app.versioning import commit_design

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    "fasta": "text/x-fasta",
    "pdb": "chemical/x-pdb",
    "cif": "chemical/x-cif",
    "csv": "text/csv",
}


def detect_kind(filename: str, text: str) -> str:
    lower = filename.lower()
    if lower.endswith((".fa", ".fasta", ".faa", ".fas")):
        return "fasta"
    if lower.endswith(".pdb") or lower.endswith(".ent"):
        return "pdb"
    if lower.endswith((".cif", ".mmcif")):
        return "cif"
    if lower.endswith((".csv", ".tsv")):
        return "csv"
    head = text.lstrip()[:200]
    if head.startswith(">"):
        return "fasta"
    if "_atom_site." in text or head.startswith("data_"):
        return "cif"
    if head.startswith(("HEADER", "ATOM", "REMARK", "TITLE")):
        return "pdb"
    first_line = head.splitlines()[0] if head.splitlines() else ""
    if "," in first_line:
        return "csv"
    raise ValueError(f"cannot determine file type for '{filename}'")


def structure_provenance(kind: str, text: str) -> str:
    """Label the origin of uploaded coordinates.

    A `.pdb` extension says nothing about how the coordinates were obtained -- predicted models are
    distributed in the same format -- so experimental provenance is only claimed when the file
    declares an experimental method, and predicted models are labelled as such.
    """
    upper = text.upper()
    for line in upper.splitlines():
        if line.startswith("EXPDTA"):
            method = line[6:].strip().lower() or "unspecified"
            if "model" in method or "prediction" in method:
                return f"model:uploaded ({method})"
            return f"experimental:{method}"
    if "_EXPTL.METHOD" in upper:
        return "experimental:mmcif-declared"
    if "ALPHAFOLD" in upper or "PLDDT" in upper or "ESMFOLD" in upper:
        return "model:uploaded (predicted structure)"
    return f"uploaded:{kind} (provenance undeclared)"


def ingest_results_csv(
    db: Session, project: Project, rows: list[dict], filename: str
) -> list[MeasuredResult]:
    """Ingest wet-lab measurements from a CSV whose header declares commit/assay/value.

    Rows are matched to commits by id, short id or label. Unmatched or unparseable rows are
    skipped rather than guessed at; the caller reports how many landed.
    """
    if not rows:
        return []
    headers = {h.strip().lower() for h in rows[0]}
    if not ({"commit", "commit_id", "label"} & headers) or "value" not in headers:
        return []

    commits = list(
        db.scalars(select(ProteinCommit).where(ProteinCommit.project_id == project.id))
    )
    by_id = {c.id: c for c in commits}
    by_short = {c.id[:12]: c for c in commits}
    by_label = {c.label: c for c in commits if c.label}

    created: list[MeasuredResult] = []
    for row in rows:
        clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        ref = clean.get("commit") or clean.get("commit_id") or clean.get("label") or ""
        commit = by_id.get(ref) or by_short.get(ref[:12]) or by_label.get(ref)
        if commit is None:
            continue
        try:
            value = float(clean["value"])
        except (KeyError, ValueError):
            continue
        outcome = clean.get("outcome", "unknown").lower()
        result = MeasuredResult(
            project_id=project.id,
            commit_id=commit.id,
            assay=clean.get("assay") or f"upload {filename}",
            objective=clean.get("objective", ""),
            readout=clean.get("readout", ""),
            value=value,
            unit=clean.get("unit", ""),
            higher_is_better=clean.get("higher_is_better", "true").lower()
            not in {"false", "0", "no"},
            outcome=outcome if outcome in {"hit", "miss", "inconclusive"} else "unknown",
            origin="simulated" if clean.get("origin") == "simulated" else "measured",
            notes=clean.get("notes", "")[:2000],
        )
        db.add(result)
        created.append(result)
    db.flush()
    return created


def _store(db: Session, project: Project, kind: str, filename: str, data: bytes) -> Artifact:
    key = f"{project.id}/uploads/{filename}"
    stored = store.put(key, data, content_type=CONTENT_TYPES.get(kind, "text/plain"))
    artifact = Artifact(
        project_id=project.id,
        kind=kind,
        filename=filename,
        key=stored.key,
        backend=stored.backend,
        sha256=stored.sha256,
        size=stored.size,
        content_type=CONTENT_TYPES.get(kind, "text/plain"),
    )
    db.add(artifact)
    db.flush()
    return artifact


def ingest_file(
    db: Session,
    project: Project,
    filename: str,
    data: bytes,
    branch: str = "main",
) -> dict:
    """Store the upload immutably and create root commits / attach structures."""
    text = data.decode("utf-8", "replace")
    kind = detect_kind(filename, text)
    artifact = _store(db, project, kind, filename, data)
    result: dict = {
        "artifact": {
            "id": artifact.id,
            "kind": kind,
            "filename": filename,
            "key": artifact.key,
            "sha256": artifact.sha256,
            "size": artifact.size,
        },
        "commits": [],
        "assays": 0,
    }

    if kind == "fasta":
        records = seqlib.parse_fasta(text)
        if not records:
            raise ValueError("no sequences found in FASTA file")
        for header, seq in records:
            commit = commit_design(
                db,
                project_id=project.id,
                sequence=seq,
                message=f"root: {header} from {filename}",
                label=header.split()[0][:60] or "wild-type",
                branch=branch,
                agent_role="human",
                provider="upload",
                prompt=f"uploaded {filename}",
                citations=[f"uploaded file {filename} (sha256 {artifact.sha256[:12]})"],
                rationale="Uploaded reference sequence; root of the design lineage.",
            )
            result["commits"].append({"id": commit.id, "label": commit.label})

    elif kind in {"pdb", "cif"}:
        struct = structlib.load_structure(text, filename, name=filename)
        commit = commit_design(
            db,
            project_id=project.id,
            sequence=struct.sequence,
            message=f"root: structure {filename}",
            label=filename.rsplit(".", 1)[0][:60],
            branch=branch,
            agent_role="human",
            provider="upload",
            structure_key=artifact.key,
            structure_source=structure_provenance(kind, text),
            structure_content=text,
            prompt=f"uploaded {filename}",
            citations=[f"uploaded structure {filename} (sha256 {artifact.sha256[:12]})"],
            rationale="Uploaded reference structure; root of the design lineage.",
        )
        result["commits"].append({"id": commit.id, "label": commit.label})
        result["structure"] = structlib.summary(struct)

    elif kind == "csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        result["assays"] = len(rows)
        result["columns"] = list(rows[0].keys()) if rows else []
        result["preview"] = rows[:5]
        ingested = ingest_results_csv(db, project, rows, filename)
        result["measured_results_ingested"] = len(ingested)
        if rows and not ingested:
            result["measured_results_note"] = (
                "no measurements ingested: needs a 'commit' (or 'label') column, a 'value' column, "
                "and rows matching commits in this project"
            )

    db.flush()
    return result
