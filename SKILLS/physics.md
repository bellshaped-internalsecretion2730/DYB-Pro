# skill.physics (v1.0.0)

Structural features (Rg, compactness, contact order, clashes), coarse relaxation, a ddG proxy and a
Becktel-Schellman predicted Tm, each with a benchmark-scale error bar.

Module: `backend/app/skills/physics.py` - Tests: `backend/tests/test_skills.py`

## Input

| Field | Type | Notes |
| --- | --- | --- |
| `sequence` | string (required) | designed sequence |
| `structure_pdb` | string / null | PDB text; a coarse fold is generated when absent |
| `target_pdb` | string / null | binding partner; enables the interface proxy |
| `template_pdb` | string / null | parent structure used as folding template |
| `mutations` | object[] | mutations applied by the toolkit |
| `relax_steps` | int | coarse relaxation steps; `0` skips it (the daemon keeps this cheap) |

## Output

`length`, `structure_source`, `contacts`, `secondary_structure`, `metrics`, `citations`.

Metrics include radius of gyration, compactness, relative contact order, clash count, `ddg_proxy`,
`predicted_tm` and, with a target, an interface/binding proxy.

## Honesty about accuracy

`ddg_proxy` carries the sd implied by published benchmarks of ddG predictors (r = 0.26-0.59), and
`predicted_tm` converts ddG to dTm with the Becktel-Schellman relation `dTm = ddG / dS_m`, keeping
the propagated error bar. These are proxies for triage, not a substitute for FEP or a DSF run, and
the wet-lab planner treats them that way.

## Citations

- Potapov, Cohen & Schreiber 2009, *Assessing computational methods for predicting protein stability
  upon mutation* (Protein Eng Des Sel 22:553)
- Becktel & Schellman 1987, *Protein stability curves* (Biopolymers 26:1859)
- Zeldovich, Berezovsky & Shakhnovich 2007, *Protein and DNA sequence determinants of thermophilic
  adaptation* (PLoS Comput Biol 3:e5)
- Kastritis & Bonvin 2010, *Are scoring functions in protein-protein docking ready to predict
  interactomes?* (J Proteome Res 9:2216)
- Plaxco, Simons & Baker 1998, *Contact order, transition state placement and the refolding rates of
  single domain proteins* (J Mol Biol 277:985)
