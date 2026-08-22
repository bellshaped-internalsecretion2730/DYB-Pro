# Small-molecule toolkit — methods and provenance

Every estimator in `app/chem` is a transparent, deterministic re-implementation of a
published method or an explicitly-labelled heuristic. Nothing here is a trained ML model,
and nothing here replaces measured data. Payloads carry a `method` field so agents and
gate decisions can weight them honestly.

| Module | Method | Source / status |
| --- | --- | --- |
| `smiles.py` | SMILES grammar subset, ring perception (smallest cycle per non-bridge bond), Morgan-relaxation graph invariants | Weininger (1988) *J. Chem. Inf. Comput. Sci.* 28:31; Morgan (1965) 5:107. Ring perception is an approximate SSSR. |
| `descriptors.py` — TPSA | Fragment contributions per polar atom keyed by connectivity/H/charge/aromaticity | Ertl, Rohde & Selzer (2000) *J. Med. Chem.* 43:3714 |
| `descriptors.py` — cLogP | Additive atom-type contributions, compact atom-type set | Wildman & Crippen (1999) *J. Chem. Inf. Comput. Sci.* 39:868. **Approximate**: the atom typing is collapsed, so treat as ±1 log unit. |
| `descriptors.py` — ESOL | logS = 0.16 − 0.63·logP − 0.0062·MW + 0.066·RB − 0.74·AP | Delaney (2004) *J. Chem. Inf. Comput. Sci.* 44:1000 |
| `descriptors.py` — QED-style | Weighted geometric mean of trapezoidal property desirabilities | In the spirit of Bickerton et al. (2012) *Nat. Chem.* 4:90; **not** the published ADS parameterisation. |
| `descriptors.py` — rules | Ro5, Veber, lead-likeness, CNS MPO ramps | Lipinski (1997); Veber (2002); Wager (2010) — CNS MPO uses simplified monotone ramps. |
| `alerts.py` | Reactive/genotoxic alert families, PAINS-style interference motifs, hERG pharmacophore | Kazius, McGuire & Bursi (2005) *J. Med. Chem.* 48:312; Baell & Holloway (2010) *J. Med. Chem.* 53:2719; hERG risk is a **heuristic**, not a QSAR. |
| `admet.py` — logBB | logBB = −0.0148·TPSA + 0.152·logP + 0.139 | Clark (1999) *J. Pharm. Sci.* 88:815 |
| `admet.py` — clearance | Well-stirred hepatic model with microsomal scaling (45 mg protein/g liver, 21 g liver/kg BW) plus fu·GFR renal term | Obach (1999); Rowland/Pang well-stirred model. CLint itself is a **lipophilicity heuristic**, not measured. |
| `admet.py` — absorption | Logistic permeability index over TPSA/HBD/MW/logP; BCS class hint from ESOL + HIA | Heuristic regressions in the spirit of Egan (2000) / Amidon (1995) BCS |
| `pk.py` | One-compartment model with first-order absorption, multiple-dose superposition; dose projected from free-drug trough coverage | Standard linear PK (Gibaldi & Perrier). Safety margin is derived from the structural-alert/hERG burden and is **not** an in-vivo tox margin. |
| `synthesis.py` | Complexity-based accessibility score, retrosynthetic disconnection sketch, cost-of-goods rollup | In the spirit of Ertl & Schuffenhauer (2009) *J. Cheminform.* 1:8; no reaction database is consulted. |
| `library.py` | Substituent scans and classical bioisosteric replacements, each validated by re-parsing | Meanwell (2011) *J. Med. Chem.* 54:2529 (bioisosterism) |
| `binding.py` | Complementarity proxy: shape fit, hydrophobic burial, H-bond pairing, electrostatics, stacking, desolvation, entropy penalty; deterministic weight-perturbation ensemble for uncertainty | **Proxy, not docking**: no coordinates, no pose, no force field. Never report as a docking result. |

## Interpretation rules used across the platform

1. Predictions are ranking aids. Absolute values (dose, Kd, margins) are hypotheses that
   only become evidence after wet-lab measurement is ingested.
2. Every number is reproducible: same input SMILES ⇒ same output, with no RNG anywhere.
3. Any candidate carrying a `block` severity alert is disqualified regardless of score.
4. `local-simulation` provenance is preserved end-to-end so simulated evidence is never
   mistaken for a real Devin agent result or a measured value.
