"""Atomic/parameter tables for the small-molecule toolkit."""

from __future__ import annotations

# --------------------------------------------------------------------------- elements

ATOMIC_WEIGHT: dict[str, float] = {
    "H": 1.008,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998,
    "Na": 22.990,
    "Mg": 24.305,
    "Si": 28.085,
    "P": 30.974,
    "S": 32.06,
    "Cl": 35.45,
    "K": 39.098,
    "Ca": 40.078,
    "Se": 78.971,
    "Br": 79.904,
    "I": 126.904,
    "*": 0.0,
}

# Allowed valences, smallest first: implicit H fills to the first valence >= bond order sum.
VALENCES: dict[str, tuple[int, ...]] = {
    "H": (1,),
    "B": (3,),
    "C": (4,),
    "N": (3, 5),
    "O": (2,),
    "F": (1,),
    "Si": (4,),
    "P": (3, 5),
    "S": (2, 4, 6),
    "Cl": (1, 3, 5, 7),
    "Se": (2, 4, 6),
    "Br": (1,),
    "I": (1,),
    "Na": (1,),
    "K": (1,),
    "Mg": (2,),
    "Ca": (2,),
    "*": (0,),
}

HALOGENS = frozenset({"F", "Cl", "Br", "I"})
HETEROATOMS = frozenset({"N", "O", "S", "P", "F", "Cl", "Br", "I", "B", "Si", "Se"})
POLAR_ELEMENTS = frozenset({"N", "O", "S", "P"})

# Organic subset that may be written without brackets.
ORGANIC_SUBSET = frozenset({"B", "C", "N", "O", "P", "S", "F", "Cl", "Br", "I"})
AROMATIC_SUBSET = frozenset({"b", "c", "n", "o", "p", "s", "se"})

# --------------------------------------------------------------------------- TPSA
# Ertl, Rohde & Selzer (2000) J. Med. Chem. 43:3714 - fragment contributions keyed by
# (element, heavy-degree, attached H, aromatic flag, formal charge). Missing keys fall
# back to ELEMENT_TPSA_FALLBACK.
TPSA_TABLE: dict[tuple[str, int, int, bool, int], float] = {
    ("N", 1, 0, False, 0): 23.79,  # nitrile N#C
    ("N", 1, 1, False, 0): 23.85,  # =NH
    ("N", 1, 2, False, 0): 26.02,  # -NH2
    ("N", 2, 0, False, 0): 12.36,  # =N-
    ("N", 2, 1, False, 0): 12.03,  # -NH-
    ("N", 3, 0, False, 0): 3.24,  # -N(-)-
    ("N", 3, 0, False, 1): 0.0,  # nitro / quaternary style N+
    ("N", 4, 0, False, 1): 0.0,
    ("N", 2, 0, True, 0): 12.89,  # pyridine
    ("N", 2, 1, True, 0): 15.79,  # pyrrole NH
    ("N", 3, 0, True, 0): 4.41,  # substituted pyrrole N
    ("N", 3, 0, True, 1): 4.10,
    ("O", 1, 0, False, 0): 17.07,  # carbonyl
    ("O", 1, 0, False, -1): 23.06,  # carboxylate / alkoxide
    ("O", 1, 1, False, 0): 20.23,  # hydroxyl
    ("O", 2, 0, False, 0): 9.23,  # ether
    ("O", 2, 0, True, 0): 13.14,  # furan
    ("S", 1, 0, False, 0): 32.09,  # thiocarbonyl
    ("S", 1, 1, False, 0): 38.80,  # thiol
    ("S", 2, 0, False, 0): 25.30,  # thioether
    ("S", 2, 0, True, 0): 28.24,  # thiophene
    ("S", 3, 0, False, 0): 19.21,  # sulfoxide
    ("S", 4, 0, False, 0): 8.38,  # sulfone
    ("P", 2, 0, False, 0): 23.47,
    ("P", 3, 0, False, 0): 13.59,
    ("P", 4, 0, False, 0): 9.81,
}

ELEMENT_TPSA_FALLBACK: dict[str, float] = {"N": 12.0, "O": 17.0, "S": 25.3, "P": 13.6}

# --------------------------------------------------------------------------- logP
# Wildman & Crippen (1999) style additive atom contributions, collapsed to a compact
# atom-type set and ridge-fitted (lambda=0.35, prior = published contributions) against
# the EXPERIMENTAL_LOGP anchors below. In-sample MAE 0.22, worst case 1.3 log units
# (salicylic acid, where an intramolecular H-bond raises the measured value).
LOGP_CONTRIB: dict[str, float] = {
    "C_sp3": 0.4053,
    "C_sp3_hetero": -0.1399,
    "C_sp2": 0.4147,
    "C_sp2_hetero": -0.6039,
    "C_sp": -0.0116,
    "C_ar": 0.3107,
    "C_ar_hetero": 0.2590,
    "H_C": 0.0495,
    "H_hetero": -0.0127,
    "N_amine_pri": -0.7731,
    "N_amine_sec": -0.3739,
    "N_amine_ter": -0.0972,
    "N_amide": -0.9230,
    "N_aromatic": -0.4623,
    "N_nitrile": -0.6225,
    "N_charged": -0.1600,
    "O_hydroxyl": -0.2569,
    "O_ether": 0.0339,
    "O_carbonyl": 0.2755,
    "O_aromatic": 0.1129,
    "O_charged": -0.4660,
    "S_thio": 0.9450,
    "S_aromatic": 0.6308,
    "S_oxidized": -1.3128,
    "F": 0.3834,
    "Cl": 0.7635,
    "Br": 1.0270,
    "I": 1.2300,
    "P": -0.4020,
    "B": 0.0,
    "Si": 0.5000,
    "Se": 0.6482,
    "other": 0.0,
}
LOGP_INTERCEPT = -0.1606

# Experimental logP anchors: the fit set, also used by the regression tests to bound drift.
EXPERIMENTAL_LOGP: dict[str, float] = {
    "CO": -0.77,  # methanol
    "CCO": -0.31,  # ethanol
    "CCCO": 0.25,  # 1-propanol
    "CCCCO": 0.88,  # 1-butanol
    "CC(=O)O": -0.17,  # acetic acid
    "CC(=O)OCC": 0.73,  # ethyl acetate
    "CCOCC": 0.89,  # diethyl ether
    "CC(C)=O": -0.24,  # acetone
    "CC#N": -0.34,  # acetonitrile
    "c1ccccc1": 2.13,  # benzene
    "Cc1ccccc1": 2.73,  # toluene
    "Oc1ccccc1": 1.46,  # phenol
    "Nc1ccccc1": 0.90,  # aniline
    "COc1ccccc1": 2.11,  # anisole
    "Clc1ccccc1": 2.84,  # chlorobenzene
    "Fc1ccccc1": 2.27,  # fluorobenzene
    "Brc1ccccc1": 2.99,  # bromobenzene
    "Ic1ccccc1": 3.25,  # iodobenzene
    "O=[N+]([O-])c1ccccc1": 1.85,  # nitrobenzene
    "OC(=O)c1ccccc1": 1.87,  # benzoic acid
    "OC(=O)c1ccccc1O": 2.26,  # salicylic acid
    "NC(=O)c1ccccc1": 0.64,  # benzamide
    "c1ccc2ccccc2c1": 3.30,  # naphthalene
    "c1ccncc1": 0.65,  # pyridine
    "c1ccc2ncccc2c1": 2.03,  # quinoline
    "c1cc[nH]c1": 0.75,  # pyrrole
    "c1ccsc1": 1.81,  # thiophene
    "c1cnc[nH]1": -0.08,  # imidazole
    "c1ccc2[nH]ccc2c1": 2.14,  # indole
    "C=Cc1ccccc1": 2.95,  # styrene
    "c1ccc(-c2ccccc2)cc1": 4.01,  # biphenyl
    "CC(=O)Oc1ccccc1C(=O)O": 1.19,  # aspirin
    "CC(=O)Nc1ccc(O)cc1": 0.46,  # paracetamol
    "CC(C)Cc1ccc(cc1)C(C)C(=O)O": 3.97,  # ibuprofen
    "Cn1cnc2c1c(=O)n(C)c(=O)n2C": -0.07,  # caffeine
    "Cn1c(=O)c2[nH]cnc2n(C)c1=O": -0.02,  # theophylline
    "CC(C)NCC(O)COc1cccc2ccccc12": 3.48,  # propranolol
    "CCN(CC)CC(=O)Nc1c(C)cccc1C": 2.44,  # lidocaine
    "NS(=O)(=O)c1ccccc1": 0.31,  # benzenesulfonamide
    "CC(=O)Nc1ccccc1": 1.16,  # acetanilide
    "OCc1ccccc1": 1.10,  # benzyl alcohol
    "CCCCCC": 3.90,  # hexane
    "ClCCl": 1.25,  # dichloromethane
    "CS(C)=O": -1.35,  # DMSO
    "CN(C)C=O": -1.01,  # DMF
    "CCS": 1.27,  # ethanethiol
    "CSC": 0.92,  # dimethyl sulfide
    "CCN": -0.13,  # ethylamine
    "CCNCC": 0.58,  # diethylamine
    "CCN(CC)CC": 1.45,  # triethylamine
}

# --------------------------------------------------------------------------- library design
# R-groups used for deterministic analog enumeration around an attachment point.
R_GROUPS: dict[str, str] = {
    "H": "",
    "Me": "C",
    "Et": "CC",
    "iPr": "C(C)C",
    "cPr": "C1CC1",
    "CF3": "C(F)(F)F",
    "F": "F",
    "Cl": "Cl",
    "Br": "Br",
    "CN": "C#N",
    "OH": "O",
    "OMe": "OC",
    "NH2": "N",
    "NMe2": "N(C)C",
    "morpholine": "N1CCOCC1",
    "piperidine": "N1CCCCC1",
    "piperazine": "N1CCNCC1",
    "COOH": "C(=O)O",
    "CONH2": "C(=O)N",
    "SO2NH2": "S(=O)(=O)N",
    "phenyl": "c1ccccc1",
    "pyridyl": "c1ccncc1",
    "imidazolyl": "c1cnc[nH]1",
    "oxazolyl": "c1ocnc1",
}

# Classical bioisosteric swaps applied as SMILES fragment rewrites; each candidate is
# re-parsed and dropped if it is not a valid molecule.
BIOISOSTERES: tuple[tuple[str, str, str], ...] = (
    ("C(=O)O", "S(=O)(=O)N", "carboxylic acid -> sulfonamide (acid isostere, lower PSA penalty)"),
    ("C(=O)O", "c1nn[nH]n1", "carboxylic acid -> tetrazole (metabolically robust acid isostere)"),
    ("C(=O)N", "C(=S)N", "amide -> thioamide (H-bond strength / metabolic shift)"),
    ("c1ccccc1", "c1ccncc1", "phenyl -> pyridyl (lower logP, added acceptor)"),
    ("c1ccccc1", "c1cc[nH]c1", "phenyl -> pyrrolyl (ring size / donor swap)"),
    ("OC", "F", "methoxy -> fluoro (block O-demethylation)"),
    ("C", "C(F)(F)F", "methyl -> trifluoromethyl (metabolic blocking)"),
    ("N1CCCCC1", "N1CCOCC1", "piperidine -> morpholine (reduce basicity / hERG)"),
    ("S(=O)(=O)N", "C(=O)N", "sulfonamide -> amide (permeability)"),
    ("C#N", "C(F)(F)F", "nitrile -> trifluoromethyl (tox liability swap)"),
)

# --------------------------------------------------------------------------- pocket model
# Coarse residue properties used to derive a binding-pocket fingerprint from sequence.
RESIDUE_HYDROPHOBIC = frozenset("AVLIMFWCY")
RESIDUE_AROMATIC = frozenset("FWYH")
RESIDUE_DONOR = frozenset("KRWNQSTYH")
RESIDUE_ACCEPTOR = frozenset("DENQSTYH")
RESIDUE_POSITIVE = frozenset("KRH")
RESIDUE_NEGATIVE = frozenset("DE")
# Approximate side-chain volumes (A^3), Zamyatnin (1972).
RESIDUE_VOLUME: dict[str, float] = {
    "A": 88.6,
    "R": 173.4,
    "N": 114.1,
    "D": 111.1,
    "C": 108.5,
    "Q": 143.8,
    "E": 138.4,
    "G": 60.1,
    "H": 153.2,
    "I": 166.7,
    "L": 166.7,
    "K": 168.6,
    "M": 162.9,
    "F": 189.9,
    "P": 112.7,
    "S": 89.0,
    "T": 116.1,
    "W": 227.8,
    "Y": 193.6,
    "V": 140.0,
}
