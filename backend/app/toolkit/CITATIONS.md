# Methods and citations

Every score DYB Pro reports is an independent implementation of a published method. Nothing in
this directory is derived from proprietary software, data or documentation.

Each row states the DOI where we could verify one, the population the source covers, and the known
error magnitude. The last column is the honest calibration status of *our* implementation:

- **calibrated** — the quantity is computed by the published method and the sd we publish is that
  method's published error;
- **anchored** — the formula/ingredients are published, but our constants are not the source's
  fitted values, so the number is on an arbitrary scale;
- **policy** — a Foldsmith threshold or a deliberately conservative sd; no paper states it;
- **proxy** — an uncalibrated screening heuristic with no empirical calibration at all.

A dash in the DOI column means we could not verify a DOI for that reference from an open metadata
service; the reference is kept as-is and no error figure is claimed from it.

| Module | Quantity | Method / source | DOI | Applicability & known error | Status |
| --- | --- | --- | --- | --- | --- |
| `sequence.py` | GRAVY, hydropathy windows | Kyte J, Doolittle RF. *A simple method for displaying the hydropathic character of a protein.* J Mol Biol 157:105-132 (1982) | 10.1016/0022-2836(82)90515-0 | any sequence; a hydropathy scale with no published error bar - a descriptor, not a solubility prediction | anchored |
| `sequence.py` | isoelectric point, net charge | Bjellqvist B et al. *The focusing positions of polypeptides in immobilized pH gradients can be predicted from their amino acid sequences.* Electrophoresis 14:1023-1031 (1993) | 10.1002/elps.11501401163 | denatured sequence behaviour in IPGs; sequence-based pI prediction averages 0.87 pH units of error for proteins, 0.25 for peptides (Kozlowski 2016, 10.1186/s13062-016-0159-9) | calibrated |
| `sequence.py` | instability index | Guruprasad K, Reddy BVB, Pandit MW. *Correlation between stability of a protein and its dipeptide composition.* Protein Eng 4:155-161 (1990) | 10.1093/protein/4.2.155 | 12 unstable + 32 stable proteins, in-vivo metabolic stability; no accuracy published, and the "40" cut-off is ExPASy convention rather than this paper's | proxy |
| `sequence.py` | extinction coefficient | Pace CN et al. *How to measure and predict the molar absorption coefficient of a protein.* Protein Sci 4:2411-2423 (1995); Gill SC, von Hippel PH. *Calculation of protein extinction coefficients from amino acid sequence data.* Anal Biochem 182:319-326 (1989) | 10.1002/pro.5560041120 / 10.1016/0003-2697(89)90602-7 | eps280 = 5500·Trp + 1490·Tyr + 125·cystine, fitted to 116 measured coefficients for 80 proteins; reliable for Trp-containing proteins, less reliable without Trp; the authors recommend measuring | anchored |
| `sequence.py` | mean hydrophobic moment | Eisenberg D, Weiss RM, Terwilliger TC. *The helical hydrophobic moment.* Nature 299:371-374 (1982) | 10.1038/299371a0 | alpha-helical segments; amphiphilicity descriptor, no error bar | anchored |
| `sequence.py` | BLOSUM62 substitution scores | Henikoff S, Henikoff JG. *Amino acid substitution matrices from protein blocks.* PNAS 89:10915-10919 (1992) | 10.1073/pnas.89.22.10915 | aligned protein blocks; a log-odds score, symmetric and therefore reported as context only (it cannot carry the sign of a stability change) | anchored |
| `sequence.py` | global alignment (affine gaps) | Needleman SB, Wunsch CD (1970); Gotoh O. *An improved algorithm for matching biological sequences.* J Mol Biol 162:705-708 (1982) | — | exact dynamic programming; no statistical error | anchored |
| `sequence.py` | codon optimization | Sharp PM, Li WH. *The codon adaptation index.* Nucleic Acids Res 15:1281-1295 (1987) | — | codon usage of highly expressed genes in one host; no per-construct expression error is claimed | proxy |
| `sequence.py` | primer Tm | Wallace RB et al. (1979) for short oligos; GC%/salt-adjusted formula for longer oligos | — | short oligonucleotides at standard salt; nearest-neighbour models are more accurate | proxy |
| `folding.py` | secondary-structure propensities | Chou PY, Fasman GD. *Prediction of the secondary structure of proteins from their amino acid sequence.* Adv Enzymol 47:45-148 (1978) | — | globular proteins; we quote no accuracy for it and use it only as a coarse propensity feeding a coarse fold | proxy |
| `structure.py` | CA-geometry secondary structure | Distance-criteria assignment as in Levitt & Greer, *Automatic identification of secondary structure in globular proteins*, J Mol Biol 114:181-239 (1977) | — | CA-only models; geometric assignment, not DSSP | anchored |
| `structure.py` | expected radius of gyration | Skolnick J, Kolinski A et al. scaling Rg ≈ 2.2 N^0.38 for globular proteins | — | compact globular monomers; a scaling law, so disordered or elongated proteins deviate strongly | anchored |
| `developability.py` | aggregation-prone regions | Tartaglia GG, Vendruscolo M. *The Zyggregator method for predicting protein aggregation propensities.* Chem Soc Rev 37:1395-1401 (2008) | 10.1039/b706784b | window-based APR logic. Our 7-residue window, +1.5 hydropathy cut-off and \|q\| <= 1 condition are hand-chosen with no calibration; AggreProt (10.1093/nar/gkae420) is the benchmarked alternative | proxy |
| `developability.py` | intrinsic solubility | Sormanni P, Aprile FA, Vendruscolo M. *The CamSol method of rational design of protein mutants with enhanced solubility.* J Mol Biol 427:478-490 (2015) — charge/hydropathy composite re-implementation | 10.1016/j.jmb.2014.09.026 | ingredients only, weights not fitted, arbitrary units. Ceiling of the family: SoluProt reaches 58.5% accuracy / AUC 0.62 (10.1093/bioinformatics/btaa1102) | proxy |
| `developability.py` | MHC-II anchor motifs | Anchor-position conventions for MHC class II binding cores (P1/P4/P6/P9) | — | allele-agnostic motif count with no benchmark, no affinity and no HLA weighting; NetMHCIIpan-4.0 (10.1093/nar/gkaa379) is required for an epitope claim | proxy |
| `developability.py` | ΔΔG proxy terms | Guerois R, Nielsen JE, Serrano L. *Predicting changes in the stability of proteins and protein complexes.* J Mol Biol 320:369-387 (2002) — empirical term decomposition | 10.1016/s0022-2836(02)00442-4 | source tested on 1088 point mutants; our weights are not theirs and the score is unitless. Scale of the field: r = 0.26-0.59 vs experiment where replicates reach 0.86 (10.1093/protein/gzp030) | proxy |
| `developability.py` | residue volumes | Zamyatnin AA. *Protein volume in solution.* Prog Biophys Mol Biol 24:107-123 (1972) | — | amino-acid partial volumes in solution; transcribed constants, no prediction | anchored |
| `developability.py` | sequence liability motifs | Jarasch A et al. *Developability assessment during the selection of novel therapeutic antibodies.* J Pharm Sci 104:1885-1898 (2015); sequon statistics Gavel Y, von Heijne G, Protein Eng 3:433 (1990); Asn deamidation context Robinson NE, Robinson AB, PNAS 98:944 (2001) | 10.1002/jps.24430 / 10.1093/protein/3.5.433 / 10.1073/pnas.98.3.944 | motif *sites* only. Deamidation rates were measured on 306 asparaginyl sequences at pH 7.4/37 C and span orders of magnitude with context and conformation, so no rate is implied by a hit | anchored |
| `developability.py` | screening filters (`DEFAULT_FILTERS`) | Foldsmith triage policy | — | project gates tuned so one bad feature passes and a stack does not; overridable per campaign, never published acceptance criteria | policy |
| `docking.py` | coarse-grained interaction potential | Kim YC, Hummer G. *Coarse-grained models for simulations of multiprotein complexes.* J Mol Biol 375:1416-1433 (2008) | — | residue-level complexes; docking scores of this class correlate poorly with measured affinity (10.1021/pr9009854), hence ~1 log10 uncertainty downstream | anchored |
| `docking.py` | Debye screening | Debye P, Hückel E (1923); screening length at physiological ionic strength | — | dilute electrolyte approximation at ~150 mM ionic strength | anchored |
| `docking.py` | rigid-body ensemble sampling | Katchalski-Katzir E et al. *Molecular surface recognition: determination of geometric fit.* PNAS 89:2195-2199 (1992) | — | rigid bodies; no backbone flexibility, so induced fit is not modelled | anchored |
| `services/ranking.py` | Pareto/multi-objective selection | Deb K et al. *A fast and elitist multiobjective genetic algorithm: NSGA-II.* IEEE Trans Evol Comput 6:182-197 (2002) — non-dominated sorting | — | exact non-dominated sorting; no statistical error | anchored |

Open-source libraries used: BioPython (BLOSUM62 matrices), NumPy, FastAPI, SQLAlchemy, Celery.
