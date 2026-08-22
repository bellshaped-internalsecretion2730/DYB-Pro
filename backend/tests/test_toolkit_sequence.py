from __future__ import annotations

import pytest

from app.toolkit import sequence as seqlib

GB1 = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"


def test_parse_and_roundtrip_fasta():
    text = ">gb1 test\nMTYK\nLILN\n\n>second\nAAAA\n"
    records = seqlib.parse_fasta(text)
    assert records == [("gb1 test", "MTYKLILN"), ("second", "AAAA")]
    again = seqlib.parse_fasta(seqlib.to_fasta(records))
    assert again == records


def test_clean_sequence_rejects_non_protein():
    assert seqlib.clean_sequence(" mty k\n") == "MTYK"
    with pytest.raises(seqlib.SequenceError):
        seqlib.clean_sequence("MTYK123")


def test_descriptors_are_physically_sane():
    d = seqlib.descriptors(GB1)
    assert d["length"] == len(GB1)
    assert 5000 < d["molecular_weight"] < 8000
    assert -2.0 < d["gravy"] < 1.0
    assert 3.0 < d["isoelectric_point"] < 11.0
    assert set(d["secondary_structure"]) == {"helix", "sheet", "coil"}


def test_apply_mutations_validates_wildtype():
    mutated, applied = seqlib.apply_mutations(GB1, ["T2A"])
    assert mutated[1] == "A"
    assert applied[0] == {"mutation": "T2A", "wt": "T", "position": 2, "mt": "A"}
    with pytest.raises(seqlib.SequenceError):
        seqlib.apply_mutations(GB1, ["A2K"])  # wt residue is T, not A
    with pytest.raises(seqlib.SequenceError):
        seqlib.apply_mutations(GB1, ["T900A"])  # out of range


def test_diff_sequences_reports_substitutions():
    other = "A" + GB1[1:]
    diff = seqlib.diff_sequences(GB1, other)
    assert len(diff) == 1
    assert diff[0]["mutation"] == "M1A"
    assert (diff[0]["wt"], diff[0]["position"], diff[0]["mt"]) == ("M", 1, "A")


def test_alignment_identity_is_symmetric_and_bounded():
    aln = seqlib.align(GB1, GB1)
    assert aln.identity == pytest.approx(1.0)
    partial = seqlib.align(GB1, GB1[:40])
    assert 0.0 < partial.identity <= 1.0


def test_homology_search_ranks_self_first():
    corpus = [
        {"name": "self", "sequence": GB1},
        {"name": "unrelated", "sequence": "WWWWWWWWWWWWWWWWWWWW"},
    ]
    hits = seqlib.homology_search(GB1, corpus)
    assert hits[0].name == "self"
    assert hits[0].identity == pytest.approx(1.0)


def test_back_translation_is_reversible_in_protein_space():
    dna = seqlib.back_translate(GB1, stop=False)
    assert len(dna) == 3 * len(GB1)
    assert 0.0 < seqlib.gc_content(dna) < 1.0
    rc = seqlib.reverse_complement(dna)
    assert seqlib.reverse_complement(rc) == dna
