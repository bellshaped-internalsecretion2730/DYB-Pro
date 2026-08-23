# skill.chemistry (v1.0.0)

pI, net charge at the working pH, GRAVY, extinction coefficient, cysteine/PTM liabilities and a
buffer recommendation that keeps its distance from the isoelectric point explicit.

Module: `backend/app/skills/chemistry.py` - Tests: `backend/tests/test_skills.py`,
`backend/tests/test_evidence.py`

## Input

| Field | Type | Notes |
| --- | --- | --- |
| `sequence` | string (required) | designed sequence |
| `working_ph` | number | assay/formulation pH |
| `buffer` | string | current buffer, if any |
| `reducing_environment` | bool | true for cytoplasmic *E. coli* expression |

## Output

| Field | Notes |
| --- | --- |
| `metrics` | pI, net charge at `working_ph`, GRAVY, extinction coefficient, Cys count |
| `ptm_sites` | residue indices per liability class (N-glycosylation, deamidation, oxidation, ...) |
| `liabilities` | free Cys, unpaired disulfides, hydrophobic patches |
| `buffer_recommendation` | pH/buffer suggestion with the distance from the pI kept explicit |
| `flags`, `citations` | developability warnings and sources |

## Behaviour

The buffer recommendation exists because solubility bottoms out near the pI: the skill reports the
`|pH - pI|` margin it achieved rather than only naming a buffer. The 1.0 pH-unit margin it aims for
is a **project policy** with no empirical calibration - solubility minima near the pI are textbook,
but the size of a safe margin is protein- and salt-dependent. A280 quantification is flagged as
unreliable below `epsilon280 = 1500 M^-1 cm^-1`, also a project policy (roughly one tyrosine), not a
published cut-off.

Reported uncertainty: pI carries `sd = 0.87` pH units, which is the published mean absolute error of
sequence-based protein pI prediction - **not** instrument precision. GRAVY and epsilon280 carry no
sd, because none is published for them.

## Evidence

Machine-readable in `app.skills.chemistry.EVIDENCE`. Status: *calibrated* = published method **and**
published error; *anchored* = published method, our constants are not the source's fitted values;
*policy* = a Foldsmith choice; *proxy* = uncalibrated screening heuristic.

| Key | Claim | DOI | Applicability | Known error | Status |
| --- | --- | --- | --- | --- | --- |
| `chemistry.isoelectric_point` | pI from the Bjellqvist pKa set, published with sd 0.87 pH units | [10.1002/elps.11501401163](https://doi.org/10.1002/elps.11501401163) | denatured sequence behaviour in immobilised pH gradients; folded proteins with buried ionisable groups deviate further | 0.87 pH units mean absolute error for proteins (0.25 for peptides) on the IPC benchmark | calibrated |
| `chemistry.pi_error_benchmark` | the pI sd is a benchmark number, not a guess | [10.1186/s13062-016-0159-9](https://doi.org/10.1186/s13062-016-0159-9) | large sequence benchmarks of pI predictors | 0.87 pH units (proteins), 0.25 (peptides); IPC 2.0 ([10.1093/nar/gkab295](https://doi.org/10.1093/nar/gkab295)) improves this, but not by an order of magnitude | calibrated |
| `chemistry.gravy` | GRAVY = mean Kyte-Doolittle hydropathy | [10.1016/0022-2836(82)90515-0](https://doi.org/10.1016/0022-2836(82)90515-0) | any sequence; the scale was built for interior/membrane propensity plots | none published - a descriptor, never a predicted solubility | anchored |
| `chemistry.extinction_coefficient` | `eps280 = 5500·Trp + 1490·Tyr + 125·cystine` | [10.1016/0003-2697(89)90602-7](https://doi.org/10.1016/0003-2697(89)90602-7), [10.1002/pro.5560041120](https://doi.org/10.1002/pro.5560041120) | proteins containing at least one Trp or Tyr | fitted on 116 measured coefficients for 80 proteins; reliable for Trp-containing proteins, less reliable without Trp. The authors recommend measuring eps rather than predicting it, and publish no single error figure - so we attach no sd | anchored |
| `chemistry.a280_floor` | A280 flagged unreliable below eps280 = 1500 M^-1 cm^-1 | [10.1002/pro.5560041120](https://doi.org/10.1002/pro.5560041120) | our own quantification advice before a construct is ordered | no published threshold exists; 1500 ~ one tyrosine (1490), chosen so a single aromatic cannot carry the assay | policy |
| `chemistry.ptm_motifs` | N-X-S/T sequons and Asn/Asp liability contexts are reported as *sites* | [10.1073/pnas.98.3.944](https://doi.org/10.1073/pnas.98.3.944), [10.1093/protein/3.5.433](https://doi.org/10.1093/protein/3.5.433) | deamidation contexts measured on 306 asparaginyl sequences at pH 7.4, 37 C, 0.15 M Tris; sequon statistics from glycosylated vs non-glycosylated sites | site presence only: measured half-times span orders of magnitude and depend on main-chain conformation ([10.3390/ijms21197035](https://doi.org/10.3390/ijms21197035)), so no rate is emitted and none may be inferred | anchored |
| `chemistry.instability_index` | Guruprasad dipeptide-composition instability index | [10.1093/protein/4.2.155](https://doi.org/10.1093/protein/4.2.155) | 12 unstable + 32 stable proteins; in-vivo metabolic stability, not thermal stability | the paper reports no accuracy and the quoted cut-off of 40 is ExPASy ProtParam convention, so the index is emitted as a flag with no pass/fail rule | proxy |
| `chemistry.pi_buffer_margin` | formulation pH is kept >= 1.0 pH unit from the pI | — | our buffer recommendation | uncalibrated: the safe margin is protein- and salt-dependent; the 1.0-unit rule is a project decision | policy |

## Citations

- Bjellqvist et al. 1993, *The focusing positions of polypeptides in immobilized pH gradients*
  (Electrophoresis 14:1023) - doi:10.1002/elps.11501401163
- Kozlowski 2016, *IPC - Isoelectric Point Calculator* (Biol Direct 11:55) -
  doi:10.1186/s13062-016-0159-9; IPC 2.0 (Nucleic Acids Res 49:W285) - doi:10.1093/nar/gkab295
- Kyte & Doolittle 1982, *A simple method for displaying the hydropathic character of a protein*
  (J Mol Biol 157:105) - doi:10.1016/0022-2836(82)90515-0
- Pace et al. 1995, *How to measure and predict the molar absorption coefficient of a protein*
  (Protein Sci 4:2411) - doi:10.1002/pro.5560041120
- Gill & von Hippel 1989, *Calculation of protein extinction coefficients from amino acid sequence
  data* (Anal Biochem 182:319) - doi:10.1016/0003-2697(89)90602-7
- Robinson & Robinson 2001, *Molecular clocks* (PNAS 98:944) - doi:10.1073/pnas.98.3.944 - Asn
  deamidation sequence context
- Gavel & von Heijne 1990, *Sequence differences between glycosylated and non-glycosylated Asn-X-Thr/
  Ser acceptor sites* (Protein Eng 3:433) - doi:10.1093/protein/3.5.433
- Kato et al. 2020, *Structural and conformational features affecting deamidation* (Int J Mol Sci
  21:7035) - doi:10.3390/ijms21197035
- Guruprasad, Reddy & Pandit 1990, *Correlation between stability of a protein and its dipeptide
  composition* (Protein Eng 4:155) - doi:10.1093/protein/4.2.155
