"""Versioned, provenance-carrying planning prices."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

PRICE_LIST_VERSION = "2026-08-adaptyv-twist-ginkgo-inhouse"


@dataclass(frozen=True)
class PriceItem:
    sku: str
    unit: str
    unit_cost: float
    min_charge: float
    batch_size: int
    vendor: str
    source: str
    quoted_on: str
    unit_cost_low: float | None = None
    unit_cost_high: float | None = None

    def __post_init__(self) -> None:
        if self.unit_cost_low is None:
            object.__setattr__(self, "unit_cost_low", self.unit_cost)
        if self.unit_cost_high is None:
            object.__setattr__(self, "unit_cost_high", self.unit_cost)


_DISPLAY_SOURCE = (
    "~900,000 domains/week for ~$2,000 in reagents - "
    "https://doi.org/10.1038/s41586-023-06328-6"
)

PRICE_CATALOG: dict[str, PriceItem] = {
    "oligo_pool_library": PriceItem(
        "oligo_pool_library", "per_library", 2000.0, 2000.0, 1, "pooled-display", _DISPLAY_SOURCE, "2026-08"
    ),
    "display_enrichment_marginal": PriceItem(
        "display_enrichment_marginal",
        "per_protein",
        0.05,
        0.0,
        1,
        "pooled-display",
        f"{_DISPLAY_SOURCE} (marginal per-variant cost at the enrichment stage)",
        "2026-08",
    ),
    "gene_synthesis_express": PriceItem(
        "gene_synthesis_express",
        "per_base",
        0.07,
        0.0,
        1,
        "Twist",
        "$0.07/base clonal gene, 2-day express - "
        "https://investors.twistbioscience.com/news-releases/news-release-details/"
        "twist-bioscience-expands-express-delivery-turnaround-time-all",
        "2026-08",
    ),
    "outsourced_expression": PriceItem(
        "outsourced_expression",
        "per_protein",
        49.0,
        0.0,
        1,
        "Adaptyv",
        "$49/protein expression - https://www.adaptyvbio.com/services/binding",
        "2026-08",
    ),
    "outsourced_binding": PriceItem(
        "outsourced_binding",
        "per_protein",
        134.0,
        0.0,
        1,
        "Adaptyv",
        "$99-169/protein binding characterisation, midpoint used - "
        "https://www.adaptyvbio.com/services/binding",
        "2026-08",
        99.0,
        169.0,
    ),
    "cloud_lab_sample": PriceItem(
        "cloud_lab_sample",
        "per_sample",
        149.0,
        0.0,
        1,
        "Ginkgo",
        "$99-199/sample cloud-lab pricing, midpoint used - https://cloud.ginkgo.bio/",
        "2026-08",
        99.0,
        199.0,
    ),
    "gene_synthesis": PriceItem(
        "gene_synthesis",
        "per_bp",
        0.09,
        0.0,
        1,
        "in-house-quote",
        "existing Foldsmith constant, retained",
        "2026-08",
    ),
    "primer_base": PriceItem(
        "primer_base",
        "per_base",
        0.25,
        0.0,
        1,
        "in-house-quote",
        "existing Foldsmith constant, retained",
        "2026-08",
    ),
    "mutagenesis_reaction": PriceItem(
        "mutagenesis_reaction",
        "per_reaction",
        45.0,
        0.0,
        1,
        "in-house-quote",
        "existing Foldsmith constant, retained",
        "2026-08",
    ),
    "transformation_plasmid_prep": PriceItem(
        "transformation_plasmid_prep",
        "per_reaction",
        35.0,
        0.0,
        1,
        "in-house-quote",
        "existing",
        "2026-08",
    ),
    "sequence_verification": PriceItem(
        "sequence_verification",
        "per_reaction",
        18.0,
        0.0,
        1,
        "in-house-quote",
        "existing",
        "2026-08",
    ),
    "expression_purification": PriceItem(
        "expression_purification",
        "per_protein",
        320.0,
        0.0,
        1,
        "in-house-quote",
        "existing",
        "2026-08",
    ),
    "spr": PriceItem("spr", "per_sample", 180.0, 0.0, 1, "in-house-quote", "existing", "2026-08"),
    "nanodsf": PriceItem("nanodsf", "per_sample", 60.0, 0.0, 1, "in-house-quote", "existing", "2026-08"),
    "sec": PriceItem("sec", "per_sample", 95.0, 0.0, 1, "in-house-quote", "existing", "2026-08"),
    "endotoxin_qc": PriceItem(
        "endotoxin_qc", "per_sample", 40.0, 0.0, 1, "in-house-quote", "existing", "2026-08"
    ),
}

TIERS = {
    "T0": {
        "label": "pooled display / enrichment",
        "decides": {"binding_enrichment"},
        "caveat": "enrichment only; ranks variants, does not measure KD",
    },
    "T1": {
        "label": "outsourced express expression + binding",
        "decides": {"binding_kd", "expression"},
        "caveat": "per-protein outsourced measurement",
    },
    "T2": {
        "label": "in-house purification + biophysics",
        "decides": {"binding_kd", "thermostability", "aggregation", "expression", "endotoxin"},
        "caveat": "highest cost per candidate; required for non-binding readouts",
    },
}

# Compatibility name used by existing wet-lab callers.
COSTS = {
    "gene_synthesis_per_bp": PRICE_CATALOG["gene_synthesis"].unit_cost,
    "primer_per_base": PRICE_CATALOG["primer_base"].unit_cost,
    "site_directed_mutagenesis_reaction": PRICE_CATALOG["mutagenesis_reaction"].unit_cost,
    "transformation_and_plasmid_prep": PRICE_CATALOG["transformation_plasmid_prep"].unit_cost,
    "sequence_verification": PRICE_CATALOG["sequence_verification"].unit_cost,
    "expression_purification_small_scale": PRICE_CATALOG["expression_purification"].unit_cost,
    "binding_assay_spr": PRICE_CATALOG["spr"].unit_cost,
    "thermal_stability_dsf": PRICE_CATALOG["nanodsf"].unit_cost,
    "aggregation_sec": PRICE_CATALOG["sec"].unit_cost,
    "endotoxin_qc": PRICE_CATALOG["endotoxin_qc"].unit_cost,
}


def item_cost(item: PriceItem, quantity: float) -> float:
    """Return point cost after vendor batch rounding and minimum charge."""
    billed = ceil(quantity / item.batch_size) * item.batch_size
    return max(item.min_charge, item.unit_cost * billed)


def item_cost_interval(item: PriceItem, quantity: float) -> tuple[float, float]:
    """Return the low/high cost interval after vendor batch rounding."""
    billed = ceil(quantity / item.batch_size) * item.batch_size
    return (
        max(item.min_charge, item.unit_cost_low * billed),
        max(item.min_charge, item.unit_cost_high * billed),
    )
