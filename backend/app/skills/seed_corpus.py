"""A small bundled corpus so a literature pass still has evidence with no network.

Records are *metadata pointers* to open, widely-cited methods papers plus a one-line factual
summary written for this repository. No abstract is copied and no paywalled PDF is fetched, which
is why `source` is `bundled-corpus`: the daemon must never present these as a live search result.
A DOI is included only where it is verified; the rest are found by title. Once the network is
available, real OpenAlex/Semantic Scholar records for the same work supersede these on de-duplication
(a record with a longer abstract wins, and DOI keys collapse duplicates).

The summaries carry the numbers the methods sections of `app/skills` actually use, so
`skill.literature` extracts real assay values offline and the demo's citations point at the source of
each heuristic rather than at nothing.
"""

from __future__ import annotations

SOURCE = "bundled-corpus"

_RECORDS: list[dict] = [
    {
        "title": (
            "A novel, highly stable fold of the immunoglobulin binding domain of streptococcal "
            "protein G"
        ),
        "authors": ["Angela M. Gronenborn", "David R. Filpula", "Nadia Z. Essig"],
        "year": 1991,
        "venue": "Science",
        "abstract": (
            "Summary for the offline corpus: the 56-residue B1 domain of streptococcal protein G "
            "(GB1) folds as a four-stranded beta sheet packed against a single alpha helix and is "
            "unusually stable for its size, with a reported Tm near 87 C in the absence of "
            "disulfides or cofactors. It is the reference scaffold for the demo campaign."
        ),
        "url": "https://www.science.org/doi/10.1126/science.1871600",
        "doi": "10.1126/science.1871600",
    },
    {
        "title": "Protein stability curves",
        "authors": ["Wayne J. Becktel", "John A. Schellman"],
        "year": 1987,
        "venue": "Biopolymers",
        "abstract": (
            "Summary for the offline corpus: relates a change in folding free energy to a shift in "
            "melting temperature through the enthalpy of unfolding, dTm = ddG / dS_m. This is the "
            "conversion skill.physics uses to turn a ddG proxy in kcal/mol into a predicted delta "
            "Tm in C, and it is why a 1 kcal/mol design gain is only worth a couple of degrees for "
            "a domain this small."
        ),
        "doi": "10.1002/bip.360261104",
    },
    {
        "title": (
            "Predicting changes in the stability of proteins and protein complexes: a study of more "
            "than 1000 mutations"
        ),
        "authors": ["Raphael Guerois", "Jens Erik Nielsen", "Luis Serrano"],
        "year": 2002,
        "venue": "Journal of Molecular Biology",
        "abstract": (
            "Summary for the offline corpus: an empirical force field fitted to over 1000 point "
            "mutations predicts ddG of folding in kcal/mol with roughly 0.8 kcal/mol standard "
            "error, dominated by van der Waals packing and solvation terms. The scale of that "
            "residual error is the prior width skill.physics reports on its ddG proxy, and the "
            "reason core-packing substitutions are ranked ahead of surface ones."
        ),
        "doi": "10.1016/S0022-2836(02)00442-4",
    },
    {
        "title": (
            "A comprehensive biophysical description of pairwise epistasis throughout an entire "
            "protein domain"
        ),
        "authors": ["C. Anders Olson", "Nicholas C. Wu", "Ren Sun"],
        "year": 2014,
        "venue": "Current Biology",
        "abstract": (
            "Summary for the offline corpus: a deep mutational scan of all single and double "
            "mutants of the GB1 domain measured by binding enrichment. Most substitutions are "
            "neutral-to-deleterious, buried positions are the least tolerant, and epistasis is "
            "concentrated in contacting pairs. The demo uses this as the prior for stacking a "
            "second buried substitution on a version whose first one was measured as beneficial."
        ),
        "doi": "10.1016/j.cub.2014.09.072",
    },
    {
        "title": (
            "The use of differential scanning fluorimetry to detect ligand interactions that "
            "promote protein stability"
        ),
        "authors": ["Frank H. Niesen", "Helena Berglund", "Masoud Vedadi"],
        "year": 2007,
        "venue": "Nature Protocols",
        "abstract": (
            "Summary for the offline corpus: a plate-based DSF protocol that determines Tm from a "
            "fluorescence melt in a few hours at low material cost, with run-to-run reproducibility "
            "typically within 0.5 C when a reference construct is on the same plate. It is the "
            "cheapest informative stability readout, which is why the planner ranks DSF first for a "
            "stability campaign and requires a paired parent control."
        ),
        "doi": "10.1038/nprot.2007.321",
    },
    {
        "title": "Recombinant protein expression in Escherichia coli: advances and challenges",
        "authors": ["Germán L. Rosano", "Eduardo A. Ceccarelli"],
        "year": 2014,
        "venue": "Frontiers in Microbiology",
        "abstract": (
            "Summary for the offline corpus: reviews the levers that decide whether a construct "
            "expresses solubly in E. coli - induction temperature, strain background including "
            "disulfide-competent SHuffle, media and codon usage - and reports that lowering "
            "induction temperature is the single most common rescue for inclusion-body formation. "
            "These are the host options and mitigations the wet-lab planner offers."
        ),
        "doi": "10.3389/fmicb.2014.00172",
    },
    {
        "title": "Design of therapeutic proteins with enhanced stability",
        "authors": ["Naresh Chennamsetty", "Vladimir Voynov", "Veysel Kayser", "Bernhard Helk"],
        "year": 2009,
        "venue": "Proceedings of the National Academy of Sciences",
        "abstract": (
            "Summary for the offline corpus: solvent-exposed hydrophobic patches predict "
            "aggregation propensity, and mutating residues inside the highest-scoring patch lowers "
            "aggregation without loss of function. This is the basis of the hydrophobic-patch score "
            "in skill.chemistry and of the demo's liability label on the contiguous Leu/Ile patch."
        ),
        "doi": "10.1073/pnas.0904191106",
    },
    {
        "title": "Developability assessment during the selection of novel therapeutic antibodies",
        "authors": ["Alexander Jarasch", "Hans Koll", "Joerg T. Regula", "Martin Bader"],
        "year": 2015,
        "venue": "Journal of Pharmaceutical Sciences",
        "abstract": (
            "Summary for the offline corpus: argues for scoring expression, solubility, thermal "
            "stability, aggregation and chemical-liability motifs together at candidate selection, "
            "because a high-affinity molecule that fails any one of them is discarded later at much "
            "greater cost. This is the developability-versus-affinity tradeoff skill.drug_discovery "
            "reports as a single index with an explicit uncertainty."
        ),
        "doi": "10.1002/jps.24430",
    },
    {
        "title": "A simple method for displaying the hydropathic character of a protein",
        "authors": ["Jack Kyte", "Russell F. Doolittle"],
        "year": 1982,
        "venue": "Journal of Molecular Biology",
        "abstract": (
            "Summary for the offline corpus: the hydropathy scale and sliding-window average used "
            "to locate buried and membrane-associated stretches. skill.chemistry uses it for the "
            "GRAVY value and for windowed patch detection feeding the aggregation and solubility "
            "proxies."
        ),
        "doi": "10.1016/0022-2836(82)90515-0",
    },
    {
        "title": "Thermostability and aliphatic index of globular proteins",
        "authors": ["Atsushi Ikai"],
        "year": 1980,
        "venue": "Journal of Biochemistry",
        "abstract": (
            "Summary for the offline corpus: aliphatic side-chain volume correlates with the "
            "thermal stability of globular proteins, the observation behind composition-based Tm "
            "baselines such as the IVYWREL fraction. skill.physics starts from that baseline before "
            "applying any mutation-specific ddG term."
        ),
        "doi": "10.1093/oxfordjournals.jbchem.a133168",
    },
]


def records() -> list[dict]:
    """Offline corpus records, tagged so they can never be mistaken for a live search hit."""
    return [{**r, "source": SOURCE, "citation_count": 0} for r in _RECORDS]
