"""Upload ingestion: FASTA / PDB / mmCIF / CSV -> artifacts + root commits."""

from __future__ import annotations

import csv
import io
import logging

from sqlalchemy.orm import Session

from app.models import Artifact, Project
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
            structure_source=f"experimental:{kind}",
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

    db.flush()
    return result
