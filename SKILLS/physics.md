# skill.physics (v1.0.0)

Structural features (Rg, compactness, contact order, clashes), coarse relaxation, a ddG proxy and a
Becktel-Schellman predicted Tm, each with a benchmark-scale error bar.

Module: `backend/app/skills/physics.py` - Tests: `backend/tests/test_skills.py`,
`backend/tests/test_evidence.py`

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

`ddg_proxy` is an unfitted burial-weighted score in arbitrary units; the sd we publish
(`DDG_UNCERTAINTY_KCAL = 1.2`) is the *scale of the field*, not this score's measured error.
`predicted_tm` combines an uncalibrated composition prior with `dTm = -ddG / dS_m`
(`dS_m = 4.5 cal/(mol*K)` per residue) and is published with a deliberately conservative
`TM_UNCERTAINTY_C = 9.0`, more than twice the best published sequence-only RMSE. These are proxies
for triage, not a substitute for FEP or a DSF run, and the wet-lab planner treats them that way.

## Evidence

Machine-readable in `app.skills.physics.EVIDENCE` (`app.skills.evidence.Evidence` records). Status:
*calibrated* = published method **and** published error; *anchored* = published method, our
constants are not the source's fitted values; *policy* = a Foldsmith choice; *proxy* = uncalibrated.

| Key | Claim | DOI | Applicability | Known error | Status |
| --- | --- | --- | --- | --- | --- |
| `physics.ddg_proxy_sd` | ddG-style scores may be used for triage only, with a wide error bar | [10.1093/protein/gzp030](https://doi.org/10.1093/protein/gzp030) | single point mutations in globular monomeric proteins, benchmarked against curated ProTherm-derived sets | published predictors reach r = 0.26-0.59 vs experiment, where experimental replicates reach r = 0.86 | anchored |
| `physics.becktel_schellman` | `dTm = -ddG / dS_m` with `dS_m ~ 4.5 cal/(mol*K)` per residue | [10.1002/bip.360261104](https://doi.org/10.1002/bip.360261104) | small single-domain proteins, reversible two-state unfolding, near Tm | exact under two-state assumptions; breaks for multi-domain, oligomeric or irreversibly unfolding proteins, and dS_m varies between proteins. Temperature-dependent treatment: [10.1093/bioinformatics/btx417](https://doi.org/10.1093/bioinformatics/btx417) | anchored |
| `physics.ivywrel_prior` | IVYWREL content tracks thermal stability | [10.1371/journal.pcbi.0030005](https://doi.org/10.1371/journal.pcbi.0030005) | ~204 prokaryotic **proteomes**, organismal growth temperatures ~-10 to 110 C | r up to 0.93 at the proteome level; there is **no** published single-protein calibration, and cross-species rank correlation overstates within-species prediction ([10.1002/prot.70019](https://doi.org/10.1002/prot.70019), [10.1016/j.compbiolchem.2009.10.002](https://doi.org/10.1016/j.compbiolchem.2009.10.002)) | proxy |
| `physics.predicted_tm_sd` | `predicted_tm` is published with sd 9 C | [10.1038/s41598-025-98667-9](https://doi.org/10.1038/s41598-025-98667-9) | any single sequence; the reference model is a trained protein language model, ours is composition-only | best published sequence-only validation RMSE is 4.11 C (MAE 3.00 C, PCC 0.89, R2 0.80). Our prior is weaker, so 9 C is a conservative policy floor, not a measured sd | policy |
| `physics.binding_proxy_sd` | interface/binding scores carry ~1 log10 of uncertainty | [10.1021/pr9009854](https://doi.org/10.1021/pr9009854) | rigid-body protein-protein complexes; benchmark of 81 complexes with measured affinities | docking scoring functions correlated poorly with measured affinity across that benchmark; 1 log10 is a policy envelope consistent with it | policy |
| `physics.relative_contact_order` | relative contact order is a fold-topology descriptor related to folding kinetics | [10.1006/jmbi.1998.1645](https://doi.org/10.1006/jmbi.1998.1645) | small single-domain, two-state folding proteins | correlates with folding rate for that class; it is not a stability or Tm predictor and carries no error bar here | anchored |

## Citations

- Potapov, Cohen & Schreiber 2009, *Assessing computational methods for predicting protein stability
  upon mutation* (Protein Eng Des Sel 22:553) - doi:10.1093/protein/gzp030
- Becktel & Schellman 1987, *Protein stability curves* (Biopolymers 26:1859) -
  doi:10.1002/bip.360261104
- Zeldovich, Berezovsky & Shakhnovich 2007, *Protein and DNA sequence determinants of thermophilic
  adaptation* (PLoS Comput Biol 3:e5) - doi:10.1371/journal.pcbi.0030005
- Pucci et al. 2017, *SCooP: an accurate and fast predictor of protein stability curves*
  (Bioinformatics 33:3415) - doi:10.1093/bioinformatics/btx417
- Tijare et al. 2025, *Prediction and design of thermostable proteins with a desired melting
  temperature* (Sci Rep 15) - doi:10.1038/s41598-025-98667-9
- López et al. 2025, *Supervised learning of protein melting temperature: cross-species vs.
  species-specific prediction* (Proteins) - doi:10.1002/prot.70019
- Kastritis & Bonvin 2010, *Are scoring functions in protein-protein docking ready to predict
  interactomes?* (J Proteome Res 9:2216) - doi:10.1021/pr9009854
- Plaxco, Simons & Baker 1998, *Contact order, transition state placement and the refolding rates of
  single domain proteins* (J Mol Biol 277:985) - doi:10.1006/jmbi.1998.1645
