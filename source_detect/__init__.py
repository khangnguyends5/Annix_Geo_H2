"""
annix_geo_h2.source_detect
─────────────────────────────────────────────────────────────────────────────
Inference layer — hydrogen-driven redox anomaly detection.

Implements Perplexity's core insight: the signature of a deep H2 leak is NOT
hydrogen alone (it gets consumed too fast). It is a coherent redox PATTERN:

  Strong leak indicators:
    1. Dissolved H2 above background
    2. Sulfate depletion
    3. Nitrate depletion
    4. Elevated sulfide
    5. Elevated dissolved Fe / Mn
    6. Spatial gradient pointing back to depth
    7. Persistence over time (not seasonal noise)

  False-positive controls:
    - Natural reducing zones (no source-consistent depth gradient)
    - Organic contamination signature (would also produce CH4 early)
    - Seasonal recharge
    - Well construction artifacts
"""

import numpy as np
from dataclasses import dataclass
from typing import Tuple


# ─── BASELINE BACKGROUND ─────────────────────────────────────────────────────
# Typical Saskatchewan deep formation water background (from public WCSB data)
BASELINE = {
    "H2":      1e-9,    # mol/L
    "SO4":     2.5e-3,  # mol/L (~240 mg/L)
    "NO3":     5e-5,    # mol/L (deep formations are NO3-poor)
    "Fe2":     1e-6,    # mol/L
    "Mn2":     1e-7,    # mol/L
    "HS":      1e-7,    # mol/L
    "CH4":     1e-7,    # mol/L
    "pH":      7.4,
}

# Anomaly score thresholds (× baseline)
ANOMALY_THRESHOLD = {
    "H2_high":      100.0,    # 100× baseline = clear anomaly
    "SO4_low":      0.5,      # depleted to half = significant
    "NO3_low":      0.3,      # nitrate vanishes quickly
    "HS_high":      10.0,
    "Fe2_high":     10.0,
    "Mn2_high":     10.0,
    "CH4_high":     5.0,
}


@dataclass
class AnomalyScore:
    """Per-cell anomaly indicators (0 = none, 1 = strongly anomalous)."""
    H2_score:       float
    SO4_score:      float    # depletion
    NO3_score:      float    # depletion
    HS_score:       float
    Fe2_score:      float
    Mn2_score:      float
    CH4_score:      float
    coherence:      float    # spatial: does the pattern point back to depth?
    composite:      float    # weighted sum


def normalise_anomaly(value: float, baseline: float, threshold: float,
                      direction: str = "above") -> float:
    """
    Score how anomalous a value is.
    direction = 'above'  → score rises as value > baseline × threshold
    direction = 'below'  → score rises as value < baseline × threshold (depletion)
    """
    if direction == "above":
        ratio = value / (baseline + 1e-30)
        if ratio <= 1:
            return 0.0
        # Sigmoid: 0.5 at threshold, asymptotes to 1.0
        return float(np.clip((ratio - 1) / (threshold - 1 + 1e-30), 0, 1))
    else:  # below — depletion
        if value >= baseline:
            return 0.0
        ratio = value / (baseline + 1e-30)
        # 1.0 at full depletion, 0 at baseline; threshold = depletion factor
        return float(np.clip(1 - ratio / threshold, 0, 1))


def score_single_cell(H2, SO4, NO3, HS, Fe2, Mn2, CH4) -> AnomalyScore:
    s_h2  = normalise_anomaly(H2,  BASELINE["H2"],  ANOMALY_THRESHOLD["H2_high"],  "above")
    s_so4 = normalise_anomaly(SO4, BASELINE["SO4"], ANOMALY_THRESHOLD["SO4_low"],  "below")
    s_no3 = normalise_anomaly(NO3, BASELINE["NO3"], ANOMALY_THRESHOLD["NO3_low"],  "below")
    s_hs  = normalise_anomaly(HS,  BASELINE["HS"],  ANOMALY_THRESHOLD["HS_high"],  "above")
    s_fe  = normalise_anomaly(Fe2, BASELINE["Fe2"], ANOMALY_THRESHOLD["Fe2_high"], "above")
    s_mn  = normalise_anomaly(Mn2, BASELINE["Mn2"], ANOMALY_THRESHOLD["Mn2_high"], "above")
    s_ch4 = normalise_anomaly(CH4, BASELINE["CH4"], ANOMALY_THRESHOLD["CH4_high"], "above")

    # Composite — H2 and the redox products are weighted highest.
    # Spatial coherence is added separately.
    composite = (
        0.20 * s_h2  +
        0.15 * s_so4 +
        0.10 * s_no3 +
        0.20 * s_hs  +
        0.15 * s_fe  +
        0.10 * s_mn  +
        0.10 * s_ch4
    )

    return AnomalyScore(
        H2_score=s_h2, SO4_score=s_so4, NO3_score=s_no3,
        HS_score=s_hs, Fe2_score=s_fe,  Mn2_score=s_mn, CH4_score=s_ch4,
        coherence=0.0, composite=composite,
    )


def spatial_coherence(H2_profile: np.ndarray, depths: np.ndarray,
                      aquifer_top: float, aquifer_bottom: float) -> float:
    """
    Does the H2 anomaly increase with depth?
    A true leak from below should show H2 rising back toward the source depth.
    A natural reducing zone might be localised and not depth-coherent.

    Returns 0–1: higher = more consistent with deep-source migration.
    """
    aq_mask = (depths >= aquifer_top) & (depths <= aquifer_bottom)
    below   = depths > aquifer_bottom

    if not aq_mask.any() or not below.any():
        return 0.0

    # Peak-based comparison. Using the mean of all below-aquifer cells dilutes
    # a localised deep source through hundreds of cells at background, even
    # when the source is real — the metric should respond to the peak.
    peak_aquifer = float(H2_profile[aq_mask].max())
    peak_below   = float(H2_profile[below].max())

    if peak_below <= peak_aquifer:
        return 0.0

    ratio = peak_below / (peak_aquifer + 1e-30)
    return float(np.clip(np.log10(ratio) / 2.0, 0, 1))


def score_profile(
    chem,                                # ChemistryState
    aquifer,                             # AquiferColumn
    sampling_depths: list = None,        # depths where wells sample
) -> dict:
    """
    Score a full chemistry profile. Returns dictionary suitable for the
    classifier downstream.
    """
    # Sample at well depths (or use full profile if not specified)
    if sampling_depths is None:
        sample_idx = list(range(len(chem.H2)))
    else:
        sample_idx = [aquifer.cell_index(d) for d in sampling_depths]

    cell_scores = []
    for i in sample_idx:
        cs = score_single_cell(
            chem.H2[i], chem.SO4[i], chem.NO3[i],
            chem.HS[i], chem.Fe2[i], chem.Mn2[i], chem.CH4[i]
        )
        cell_scores.append(cs)

    # Maximum and mean composite scores in the aquifer
    aq_top_idx = aquifer.cell_index(aquifer.aquifer_top)
    aq_bot_idx = aquifer.cell_index(aquifer.aquifer_bottom)

    aquifer_h2_scores  = [cs.H2_score  for i, cs in zip(sample_idx, cell_scores)
                          if aq_top_idx <= i <= aq_bot_idx]
    aquifer_composite  = [cs.composite for i, cs in zip(sample_idx, cell_scores)
                          if aq_top_idx <= i <= aq_bot_idx]

    max_aquifer_score   = max(aquifer_composite) if aquifer_composite else 0.0
    mean_aquifer_score  = np.mean(aquifer_composite) if aquifer_composite else 0.0

    # Spatial coherence — H2 increases with depth?
    coherence = spatial_coherence(
        chem.H2, aquifer.z, aquifer.aquifer_top, aquifer.aquifer_bottom
    )

    # Final composite — weighted by coherence
    leak_likelihood = 0.6 * max_aquifer_score + 0.4 * coherence
    leak_likelihood = float(np.clip(leak_likelihood, 0, 1))

    # Estimate source depth — peak H2 below the aquifer
    below_mask    = aquifer.z > aquifer.aquifer_bottom
    h2_below      = chem.H2[below_mask]
    z_below       = aquifer.z[below_mask]
    # Only report a source depth when the deep H2 peak is well above what a
    # shallow reducing pocket can produce (~10× ANOMALY_THRESHOLD["H2_high"]
    # baseline), otherwise localised organic zones get mislabelled as deep
    # sources.
    deep_h2_threshold = BASELINE["H2"] * ANOMALY_THRESHOLD["H2_high"] * 10
    if len(h2_below) > 0 and h2_below.max() > deep_h2_threshold:
        source_depth_est = float(z_below[np.argmax(h2_below)])
    else:
        source_depth_est = None

    return {
        "leak_likelihood":       leak_likelihood,
        "spatial_coherence":     coherence,
        "max_anomaly_score":     max_aquifer_score,
        "mean_anomaly_score":    mean_aquifer_score,
        "source_depth_estimate": source_depth_est,
        "cell_scores":           cell_scores,
        "sample_indices":        sample_idx,
    }
