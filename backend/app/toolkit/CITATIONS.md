# Methods and citations

Every score DYB Pro reports is an independent implementation of a published method. Nothing in
this directory is derived from proprietary software, data or documentation.

| Module | Quantity | Method / source |
| --- | --- | --- |
| `sequence.py` | GRAVY, hydropathy windows | Kyte J, Doolittle RF. *A simple method for displaying the hydropathic character of a protein.* J Mol Biol 157:105-132 (1982) |
| `sequence.py` | isoelectric point, net charge | Bjellqvist B et al. *The focusing positions of polypeptides in immobilized pH gradients can be predicted from their amino acid sequences.* Electrophoresis 14:1023-1031 (1993) |
| `sequence.py` | instability index | Guruprasad K, Reddy BVB, Pandit MW. *Correlation between stability of a protein and its dipeptide composition.* Protein Eng 4:155-161 (1990) |
| `sequence.py` | extinction coefficient | Pace CN et al. *How to measure and predict the molar absorption coefficient of a protein.* Protein Sci 4:2411-2423 (1995) |
| `sequence.py` | mean hydrophobic moment | Eisenberg D, Weiss RM, Terwilliger TC. *The helical hydrophobic moment.* Nature 299:371-374 (1982) |
| `sequence.py` | BLOSUM62 substitution scores | Henikoff S, Henikoff JG. *Amino acid substitution matrices from protein blocks.* PNAS 89:10915-10919 (1992) |
| `sequence.py` | global alignment (affine gaps) | Needleman SB, Wunsch CD (1970); Gotoh O. *An improved algorithm for matching biological sequences.* J Mol Biol 162:705-708 (1982) |
| `sequence.py` | codon optimization | Sharp PM, Li WH. *The codon adaptation index.* Nucleic Acids Res 15:1281-1295 (1987) |
| `sequence.py` | primer Tm | Wallace RB et al. (1979) for short oligos; Howley PM et al. GC%/salt-adjusted formula for longer oligos |
| `folding.py` | secondary-structure propensities | Chou PY, Fasman GD. *Prediction of the secondary structure of proteins from their amino acid sequence.* Adv Enzymol 47:45-148 (1978) |
| `structure.py` | CA-geometry secondary structure | Distance-criteria assignment as in Levitt & Greer, *Automatic identification of secondary structure in globular proteins*, J Mol Biol 114:181-239 (1977) |
| `structure.py` | expected radius of gyration | Skolnick J, Kolinski A et al. scaling Rg ≈ 2.2 N^0.38 for globular proteins |
| `developability.py` | aggregation-prone regions | Tartaglia GG, Vendruscolo M. *The Zyggregator method for predicting protein aggregation propensities.* Chem Soc Rev 37:1395-1401 (2008) |
| `developability.py` | intrinsic solubility | Sormanni P, Aprile FA, Vendruscolo M. *The CamSol method of rational design of protein mutants with enhanced solubility.* J Mol Biol 427:478-490 (2015) — charge/hydropathy composite re-implementation |
| `developability.py` | MHC-II anchor motifs | Nielsen M, Lund O et al. anchor-position conventions for MHC class II binding cores (P1/P4/P6/P9) |
| `developability.py` | ΔΔG proxy terms | Guerois R, Nielsen JE, Serrano L. *Predicting changes in the stability of proteins and protein complexes.* J Mol Biol 320:369-387 (2002) — empirical term decomposition |
| `developability.py` | residue volumes | Zamyatnin AA. *Protein volume in solution.* Prog Biophys Mol Biol 24:107-123 (1972) |
| `developability.py` | sequence liability motifs | Jarasch A et al. *Developability assessment during the selection of novel therapeutic antibodies.* J Pharm Sci 104:1885-1898 (2015) |
| `docking.py` | coarse-grained interaction potential | Kim YC, Hummer G. *Coarse-grained models for simulations of multiprotein complexes.* J Mol Biol 375:1416-1433 (2008) |
| `docking.py` | Debye screening | Debye P, Hückel E (1923); screening length at physiological ionic strength |
| `docking.py` | rigid-body ensemble sampling | Katchalski-Katzir E et al. *Molecular surface recognition: determination of geometric fit.* PNAS 89:2195-2199 (1992) |
| `services/ranking.py` | Pareto/multi-objective selection | Deb K et al. *A fast and elitist multiobjective genetic algorithm: NSGA-II.* IEEE Trans Evol Comput 6:182-197 (2002) — non-dominated sorting |

Open-source libraries used: BioPython (BLOSUM62 matrices), NumPy, FastAPI, SQLAlchemy, Celery.
