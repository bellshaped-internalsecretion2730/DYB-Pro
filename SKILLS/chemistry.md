# skill.chemistry (v1.0.0)

pI, net charge, GRAVY, extinction coefficient, cysteine/PTM liabilities and a buffer recommendation
that keeps the formulation off the solubility minimum.

Module: `backend/app/skills/chemistry.py` - Tests: `backend/tests/test_skills.py`

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
`|pH - pI|` margin it achieved rather than only naming a buffer. A280 quantification is flagged as
unreliable when the extinction coefficient is dominated by a single Trp/Tyr.

## Citations

- Bjellqvist et al. 1993, *The focusing positions of polypeptides in immobilized pH gradients*
  (Electrophoresis 14:1023)
- Kyte & Doolittle 1982, *A simple method for displaying the hydropathic character of a protein*
  (J Mol Biol 157:105)
- Pace et al. 1995, *How to measure and predict the molar absorption coefficient of a protein*
  (Protein Sci 4:2411)
- Gill & von Hippel 1989, *Calculation of protein extinction coefficients from amino acid sequence
  data* (Anal Biochem 182:319)
- Robinson & Robinson 2001, *Molecular clocks* (PNAS 98:944) - Asn deamidation sequence context
