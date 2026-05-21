"""
annix_geo_h2.uncertainty
─────────────────────────────────────────────────────────────────────────────
Uncertainty layer — probabilistic classification of leak likelihood.

Implements the four output categories from Perplexity:
  - Likely leak
  - Possible leak
  - Likely natural reducing zone
  - Insufficient evidence

Uses a Monte Carlo ensemble approach: perturb input parameters within their
uncertainty ranges and report the distribution of leak_likelihood scores.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict


# ─── CLASSIFICATION THRESHOLDS ───────────────────────────────────────────────
LIKELY_LEAK_MIN      = 0.65   # leak_likelihood ≥ this → likely leak
POSSIBLE_LEAK_MIN    = 0.35   # 0.35–0.65 → possible leak
NATURAL_REDUCING_MIN = 0.20   # 0.20–0.35 + low coherence → natural reducing
# Below 0.20 → insufficient evidence


@dataclass
class ClassificationResult:
    category:                    str
    leak_likelihood_mean:        float
    leak_likelihood_p5:          float        # 5th percentile
    leak_likelihood_p95:         float        # 95th percentile
    confidence_interval_width:   float
    spatial_coherence:           float
    source_depth_estimate:       float
    source_depth_uncertainty:    float
    explanation:                 str
    recommended_sampling:        List[str]
    quality_flags:               List[str]    # e.g. "sparse data", "high noise"


def classify(
    leak_likelihood_samples:   np.ndarray,    # Monte Carlo samples
    coherence:                 float,
    source_depth_samples:      List[float],
    sample_count:              int,
    aquifer_top:               float,
) -> ClassificationResult:
    """
    Classify into the four Perplexity categories based on ensemble statistics.
    """
    mean = float(np.mean(leak_likelihood_samples))
    p5   = float(np.percentile(leak_likelihood_samples, 5))
    p95  = float(np.percentile(leak_likelihood_samples, 95))
    width= p95 - p5

    # Source depth uncertainty
    valid_depths = [d for d in source_depth_samples if d is not None]
    if valid_depths:
        sd_mean = float(np.mean(valid_depths))
        sd_std  = float(np.std(valid_depths))
    else:
        sd_mean = None
        sd_std  = None

    # ── Classification logic ─────────────────────────────────────────────────
    quality = []
    if sample_count < 5:
        quality.append("sparse sampling — recommend more wells")
    if width > 0.4:
        quality.append("wide confidence interval — noisy data")

    # Insufficient evidence overrides — too noisy or too low signal
    if mean < NATURAL_REDUCING_MIN and width > 0.3:
        category = "Insufficient evidence"
        explanation = (
            "Anomaly signal is weak and uncertainty is high. The data does not "
            "support a confident determination. Additional sampling at greater "
            "depth and time-series monitoring are required before any conclusion."
        )
    elif mean >= LIKELY_LEAK_MIN and coherence >= 0.4:
        category = "Likely leak"
        explanation = (
            "Strong hydrogen-driven redox anomaly with spatial coherence "
            "pointing back to depth. The pattern matches an active hydrogen "
            "source migrating upward from below the aquifer. Persistence over "
            "time should be confirmed before final classification."
        )
    elif mean >= NATURAL_REDUCING_MIN and coherence < 0.3:
        # Discriminate before "possible leak": a moderate signal with no
        # depth-coherent gradient is the signature of a natural reducing zone,
        # not a leak. Checking this branch after "possible leak" would let any
        # moderate signal without coherence get misclassified as a leak.
        category = "Likely natural reducing zone"
        explanation = (
            "Redox products are elevated but the H2 anomaly does not show a "
            "depth-coherent gradient back to a deep source. This pattern is "
            "consistent with a natural reducing zone — for example, organic-"
            "rich sediments or a localised anoxic pocket — rather than active "
            "hydrogen leakage from below."
        )
    elif mean >= POSSIBLE_LEAK_MIN:
        category = "Possible leak"
        explanation = (
            "Moderate redox anomaly detected. Hydrogen and reaction product "
            "signals are elevated but either the spatial pattern or the "
            "magnitude is not yet decisive. Recommend targeted resampling at "
            "the inferred source depth and time-series monitoring."
        )
    else:
        category = "Insufficient evidence"
        explanation = (
            "Signal does not exceed background variation. Either no leak is "
            "present or sampling is inadequate to detect one."
        )

    # ── Sampling recommendations ─────────────────────────────────────────────
    recs = []
    if category in ("Possible leak", "Likely leak"):
        if sd_mean:
            recs.append(f"Drill or sample at {sd_mean:.0f} m ± {sd_std:.0f} m to confirm source")
        recs.append("Install time-series monitoring well in aquifer zone")
        recs.append("Add helium and methane to standard analyte suite")
    if category == "Likely natural reducing zone":
        recs.append("Compare with regional baseline aquifer chemistry")
        recs.append("Sample organic carbon and sulfate isotopes to rule in/out organic source")
    if category == "Insufficient evidence":
        recs.append("Increase well density — minimum 5 wells across suspected fairway")
        recs.append("Add depth-discrete sampling at the aquifer base")
        recs.append("Run quarterly time-series for 12 months to filter seasonal noise")

    return ClassificationResult(
        category=category,
        leak_likelihood_mean=mean,
        leak_likelihood_p5=p5,
        leak_likelihood_p95=p95,
        confidence_interval_width=width,
        spatial_coherence=coherence,
        source_depth_estimate=sd_mean if sd_mean else 0.0,
        source_depth_uncertainty=sd_std if sd_std else 0.0,
        explanation=explanation,
        recommended_sampling=recs,
        quality_flags=quality,
    )


def monte_carlo_ensemble(
    base_likelihood:  float,
    base_coherence:   float,
    base_source_depth: float,
    n_samples:        int = 100,
    measurement_noise:float = 0.10,
) -> Dict:
    """
    Generate a Monte Carlo ensemble by perturbing the deterministic result
    with realistic measurement noise. In production this would re-run the
    physics + chemistry simulation with sampled input parameters.
    """
    np.random.seed(42)
    likelihood_samples = np.clip(
        base_likelihood + np.random.normal(0, measurement_noise, n_samples),
        0, 1
    )
    depth_samples = [
        max(100, base_source_depth + np.random.normal(0, 50))
        if base_source_depth else None
        for _ in range(n_samples)
    ]

    return {
        "likelihood_samples": likelihood_samples,
        "depth_samples":      depth_samples,
    }
