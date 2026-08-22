"""Pure-python small-molecule toolkit (no RDKit dependency).

Everything here is deterministic and dependency-free so the same molecule always
produces the same numbers, whether it is scored inside a Devin agent handoff or in
the offline simulation provider. Estimators are literature-derived approximations
(see CITATIONS.md) and are labelled as proxies in the payloads they return.
"""

from app.chem.admet import admet_panel
from app.chem.alerts import alert_report
from app.chem.binding import pocket_from_sequence, score_ligand_pocket
from app.chem.descriptors import descriptors
from app.chem.evaluate import evaluate_molecule, rank_molecules
from app.chem.library import enumerate_analogs
from app.chem.pk import dose_projection, simulate_pk
from app.chem.smiles import Molecule, SmilesError, molecule_hash, parse_smiles
from app.chem.synthesis import synthesis_report

__all__ = [
    "Molecule",
    "SmilesError",
    "admet_panel",
    "alert_report",
    "descriptors",
    "dose_projection",
    "enumerate_analogs",
    "evaluate_molecule",
    "molecule_hash",
    "parse_smiles",
    "pocket_from_sequence",
    "rank_molecules",
    "score_ligand_pocket",
    "simulate_pk",
    "synthesis_report",
]
