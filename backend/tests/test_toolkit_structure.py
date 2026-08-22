from __future__ import annotations

import pytest

from app.toolkit import developability as dev
from app.toolkit import docking, folding
from app.toolkit import structure as structlib

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"

PDB = """\
ATOM      1  N   MET A   1      -1.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  MET A   1       0.000   0.000   0.000  1.00  0.00           C
ATOM      3  CA  THR A   2       3.800   0.000   0.000  1.00  0.00           C
ATOM      4  CA  TYR A   3       7.600   0.500   0.000  1.00  0.00           C
ATOM      5  CA  LYS A   4      11.400   1.000   0.000  1.00  0.00           C
END
"""

CIF = """\
data_test
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
ATOM 1 CA MET A 1 0.000 0.000 0.000
ATOM 2 CA THR A 2 3.800 0.000 0.000
ATOM 3 CA TYR A 3 7.600 0.000 0.000
"""


def test_parse_pdb_keeps_ca_trace_and_sequence():
    struct = structlib.parse_pdb(PDB)
    assert struct.sequence == "MTYK"
    assert len(struct.coords) == 4
    assert struct.source.startswith("experimental") or struct.source


def test_parse_cif():
    struct = structlib.parse_cif(CIF)
    assert struct.sequence == "MTY"


def test_load_structure_dispatches_on_content():
    assert structlib.load_structure(PDB, "x.pdb").sequence == "MTYK"
    assert structlib.load_structure(CIF, "x.cif").sequence == "MTY"
    with pytest.raises(structlib.StructureError):
        structlib.load_structure("not a structure", "x.pdb")


def test_geometry_metrics_on_folded_model():
    model = folding.fold_sequence(GB1)
    assert model.sequence == GB1
    assert "model:" in model.source
    rg = structlib.radius_of_gyration(model)
    assert 5.0 < rg < 40.0
    assert structlib.clash_count(model) == 0
    exposure = structlib.relative_exposure(model)
    assert len(exposure) == len(GB1)
    assert all(0.0 <= e <= 1.0 for e in exposure)
    summary = structlib.summary(model)
    assert summary["residues"] == len(GB1)
    assert set(summary["secondary_structure"]) == {"helix", "strand", "coil"}
    assert summary["clashes"] == 0
    assert summary["source"] == "model:coarse-geometric"


def test_generated_models_are_never_labelled_experimental():
    model = folding.fold_sequence(GB1)
    assert "experimental" not in model.source


def test_template_threading_reuses_parent_geometry():
    parent = folding.fold_sequence(GB1)
    child = folding.fold_sequence(GB1[:-1] + "A", template=parent)
    assert len(child.coords) == len(GB1)
    assert child.source


def test_developability_profile_and_filters():
    model = folding.fold_sequence(GB1)
    prof = dev.profile(GB1, structure=model)
    for key in ("aggregation", "solubility", "immunogenicity", "ddg"):
        assert "method" in prof[key]
    assert prof["liabilities"]["cysteine_count"] == GB1.count("C")
    verdict = dev.apply_filters(prof)
    assert isinstance(verdict["passed"], bool)
    assert all({"name", "passed", "reason"} <= set(c) for c in verdict["checks"])


def test_docking_returns_uncertainty_and_interface():
    ligand = folding.fold_sequence(GB1)
    receptor = folding.fold_sequence(GB1[::-1])
    result = docking.dock(ligand, receptor)
    assert result.binding_score == result.binding_score  # not NaN
    assert result.uncertainty >= 0.0
    assert result.poses > 1  # uncertainty comes from a pose ensemble
    assert isinstance(result.interface_residues, list)
    assert result.method
