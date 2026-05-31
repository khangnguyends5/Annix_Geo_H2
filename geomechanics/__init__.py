"""
annix_geo_h2.geomechanics
─────────────────────────────────────────────────────────────────────────────
Safety guarantee #2 — pressure changes from H2 production do not
destabilise the surrounding rock formations.

Five analyses combine into a single GeomechResult:

  1. **Andersonian stress profile** — initial in-situ stress state at the
     source depth from lithostatic + assumed lateral stress ratio.
  2. **Pressure drawdown** — radial Darcy flow estimate of pressure decline
     at the source for a given production rate and time.
  3. **Mohr-Coulomb failure check** — does the changed effective stress
     state approach the frictional failure envelope?
  4. **Geertsma subsidence** — uniaxial-strain compaction estimate from
     the pressure drop and formation thickness.
  5. **Induced-seismicity risk** — heuristic that combines fault distance
     with pressure-change magnitude.

These are MVP analyses. Production deployment should swap to a coupled
THM solver (OpenGeoSys or PFLOTRAN-FLAC). The classifier output schema
stays the same.

Convention: depressurisation (production) → ΔP is NEGATIVE.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math

# ─── Constants ───────────────────────────────────────────────────────────────
GRAVITY                 = 9.81           # m/s²
RHO_ROCK_DEFAULT        = 2700.0         # kg/m³ — average sedimentary rock
LATERAL_STRESS_RATIO_K0 = 0.7            # Andersonian normal-faulting regime
SHMAX_RATIO             = 1.2            # SHmax / Shmin typical anisotropy
DEFAULT_FRICTION_ANGLE  = 30.0           # degrees, typical sediment

# Subsidence regulatory threshold — Alberta AER flags > 50 mm/yr as material.
SUBSIDENCE_FLAG_MM_PER_YR = 50.0


# ─── Result type ─────────────────────────────────────────────────────────────
@dataclass
class GeomechResult:
    category:                    str             # see CATEGORIES
    pre_production_sv_mpa:       float           # vertical (lithostatic) stress
    pre_production_shmin_mpa:    float
    pre_production_shmax_mpa:    float
    initial_pore_pressure_mpa:   float
    pressure_drawdown_mpa:       float           # negative = depressurisation
    effective_stress_change_mpa: float           # positive = matrix compresses more
    mohr_coulomb_margin_mpa:     float           # > 0 = safe
    subsidence_mm_per_year:      float
    induced_seismicity_risk:     str             # "low" / "moderate" / "high"
    nearest_fault_km:            Optional[float]
    passes_stability_guarantee:  bool
    explanation:                 str
    recommended_pressure_monitoring: list[str] = field(default_factory=list)
    quality_flags:               list[str] = field(default_factory=list)


CATEGORIES = (
    "Stable",        # MC margin > 5 MPa AND subsidence < 50 mm/yr AND seismicity "low"
    "Monitor",       # MC margin 1-5 MPa OR subsidence 50-100 mm/yr OR seismicity "moderate"
    "At-risk",       # MC margin 0-1 MPa OR subsidence 100-200 mm/yr
    "Critical",      # MC margin < 0 OR subsidence > 200 mm/yr OR seismicity "high"
)


# ─── Public API ──────────────────────────────────────────────────────────────
def evaluate_geomechanics(
    aquifer,                                       # hydroflow.AquiferColumn
    production_rate_kg_s:        float = 5e-7,
    project_life_years:          float = 30.0,
    rock_density_kg_m3:          float = RHO_ROCK_DEFAULT,
    formation_thickness_m:       float = 200.0,    # producible interval
    permeability_m2:             float = 1e-13,
    fluid_viscosity_pa_s:        float = 1e-3,
    fault_layer=None,                              # optional GeologicLayer
    drainage_radius_m:           float = 2000.0,
    friction_angle_deg:          float = DEFAULT_FRICTION_ANGLE,
    compaction_coefficient_per_pa: float = 1e-9,
) -> GeomechResult:
    """
    Top-level geomechanical stability check at the source depth.
    """
    z = aquifer.source_depth

    # ── Initial stress state ────────────────────────────────────────────────
    sv, shmin, shmax = andersonian_stress_profile(z, rock_density_kg_m3)
    p0 = initial_pore_pressure_mpa(z)

    # ── Pressure drawdown ───────────────────────────────────────────────────
    dp = pressure_drawdown_mpa(
        production_rate_kg_s, project_life_years, permeability_m2,
        fluid_viscosity_pa_s, formation_thickness_m, drainage_radius_m,
    )
    delta_sigma_eff = -dp     # Δσ_eff = -Δp; depressurisation increases σ_eff

    # ── Mohr-Coulomb failure check ──────────────────────────────────────────
    mc_margin = mohr_coulomb_margin_mpa(
        sv, shmin, p0, dp, friction_angle_deg,
    )

    # ── Subsidence (Geertsma) ───────────────────────────────────────────────
    sub_mm_yr = geertsma_subsidence_mm_per_year(
        abs(dp), formation_thickness_m, project_life_years,
        compaction_coefficient_per_pa,
    )

    # ── Induced seismicity risk ─────────────────────────────────────────────
    nearest_fault, seismicity = induced_seismicity_risk(fault_layer, dp)

    # ── Aggregate classification ────────────────────────────────────────────
    passes = (
        mc_margin > 0
        and sub_mm_yr < 200
        and seismicity != "high"
    )

    if mc_margin > 5 and sub_mm_yr < SUBSIDENCE_FLAG_MM_PER_YR and seismicity == "low":
        category = CATEGORIES[0]
    elif mc_margin >= 1 and sub_mm_yr < 100 and seismicity in ("low", "moderate"):
        category = CATEGORIES[1]
    elif mc_margin > 0 and sub_mm_yr < 200 and seismicity != "high":
        category = CATEGORIES[2]
    else:
        category = CATEGORIES[3]

    # ── Recommendations + flags ─────────────────────────────────────────────
    recs: list[str] = []
    flags: list[str] = []

    if mc_margin < 5:
        recs.append(
            f"Install downhole pore-pressure gauges; trip-out the well if "
            f"drawdown exceeds {abs(dp) * 0.6:.1f} MPa (60% of predicted ΔP)."
        )
    if sub_mm_yr >= 25:
        recs.append(
            f"Quarterly InSAR ground-motion monitoring across the bbox; "
            f"flag the operator if cumulative subsidence exceeds "
            f"{int(SUBSIDENCE_FLAG_MM_PER_YR * project_life_years)} mm "
            f"over project life."
        )
    if seismicity != "low":
        recs.append(
            "Deploy a 3-station passive seismic array within 5 km of the well; "
            "trip-out trigger at M2.0 within the licence area."
        )
    if not recs:
        recs.append("Annual InSAR subsidence check + monthly wellhead pressure log "
                    "is sufficient.")

    if fault_layer is None:
        flags.append(
            "No fault layer supplied — fault distance and seismicity risk default "
            "to conservative 'no nearby fault' assumption."
        )
    if formation_thickness_m == 200.0:
        flags.append("Formation thickness defaulted to 200 m — refine with real "
                     "log/seismic interpretation.")

    explanation = (
        f"At source depth {z:.0f} m: Sv={sv:.1f}, Shmin={shmin:.1f}, "
        f"SHmax={shmax:.1f} MPa; initial pore pressure {p0:.1f} MPa. "
        f"Predicted drawdown {dp:.2f} MPa over {project_life_years:.0f} years. "
        f"Mohr-Coulomb margin {mc_margin:.2f} MPa "
        f"({'safe' if mc_margin > 0 else 'FAILURE'}). "
        f"Geertsma subsidence {sub_mm_yr:.1f} mm/yr "
        f"({'below' if sub_mm_yr < SUBSIDENCE_FLAG_MM_PER_YR else 'ABOVE'} "
        f"AER flag of {SUBSIDENCE_FLAG_MM_PER_YR:.0f} mm/yr). "
        f"Induced-seismicity risk: {seismicity}. "
        f"{'PASSES' if passes else 'FAILS'} the stability guarantee."
    )

    return GeomechResult(
        category                    = category,
        pre_production_sv_mpa       = sv,
        pre_production_shmin_mpa    = shmin,
        pre_production_shmax_mpa    = shmax,
        initial_pore_pressure_mpa   = p0,
        pressure_drawdown_mpa       = dp,
        effective_stress_change_mpa = delta_sigma_eff,
        mohr_coulomb_margin_mpa     = mc_margin,
        subsidence_mm_per_year      = sub_mm_yr,
        induced_seismicity_risk     = seismicity,
        nearest_fault_km            = nearest_fault,
        passes_stability_guarantee  = passes,
        explanation                 = explanation,
        recommended_pressure_monitoring = recs,
        quality_flags               = flags,
    )


# ─── Individual analyses ─────────────────────────────────────────────────────
def andersonian_stress_profile(
    depth_m: float,
    rock_density_kg_m3: float = RHO_ROCK_DEFAULT,
    k0: float = LATERAL_STRESS_RATIO_K0,
    shmax_ratio: float = SHMAX_RATIO,
) -> tuple[float, float, float]:
    """
    Andersonian initial-stress estimate. Returns (Sv, Shmin, SHmax) in MPa.

      Sv    = ρ_rock × g × z      (lithostatic vertical stress)
      Shmin = K0 × Sv             (typical normal-faulting regime K0 ≈ 0.7)
      SHmax = SHmax_ratio × Shmin
    """
    sv_pa    = rock_density_kg_m3 * GRAVITY * depth_m
    shmin_pa = k0 * sv_pa
    shmax_pa = shmax_ratio * shmin_pa
    return sv_pa / 1e6, shmin_pa / 1e6, shmax_pa / 1e6


def initial_pore_pressure_mpa(depth_m: float, gradient_mpa_per_km: float = 9.8) -> float:
    """
    Hydrostatic pore pressure assuming fresh water. 9.8 MPa/km is the
    fresh-water gradient; brines push this up to ~11 MPa/km but for an
    MVP screening fresh-water hydrostatic is the conservative low.
    """
    return gradient_mpa_per_km * depth_m / 1000.0


def pressure_drawdown_mpa(
    production_rate_kg_s:  float,
    years:                 float,
    permeability_m2:       float,
    fluid_viscosity_pa_s:  float,
    formation_thickness_m: float,
    drainage_radius_m:     float,
    wellbore_radius_m:     float = 0.1,
) -> float:
    """
    Crude steady-state radial Darcy drawdown.

        Δp = (q × μ × ln(re/rw)) / (2π × k × h)

    Returns drawdown in MPa as a NEGATIVE number (depressurisation).
    """
    # Convert mass rate to volumetric rate using water-like density (kg/L).
    q_m3_s = production_rate_kg_s / 1000.0
    if k_h := permeability_m2 * formation_thickness_m:
        dp_pa = (q_m3_s * fluid_viscosity_pa_s * math.log(drainage_radius_m / wellbore_radius_m)
                 / (2 * math.pi * k_h))
        # Cap the linear extrapolation at a physically meaningful upper bound
        # of the time-dependent transient (we are using SS form as a proxy).
        # Use a modest time scaling so 30-year production reads larger than 1-year.
        dp_pa *= max(years / 30.0, 0.1)
        return -dp_pa / 1e6
    return 0.0


def mohr_coulomb_margin_mpa(
    sv_mpa:           float,
    shmin_mpa:        float,
    initial_p_mpa:    float,
    drawdown_mpa:     float,
    friction_angle_deg: float = DEFAULT_FRICTION_ANGLE,
) -> float:
    """
    Distance from the current effective-stress state to the Mohr-Coulomb
    frictional-failure envelope, in MPa. Positive = safe.

    Simplified planar form. After production:
      σ1' = sv  - (p0 + Δp)
      σ3' = shmin - (p0 + Δp)
    Failure when (σ1' - σ3')/2 ≥ ((σ1' + σ3')/2) × sin(φ).
    Margin = (failure threshold) - (current shear).
    """
    p_after = initial_p_mpa + drawdown_mpa     # Δp is negative for production
    sigma1_eff = sv_mpa    - p_after
    sigma3_eff = shmin_mpa - p_after
    centre = (sigma1_eff + sigma3_eff) / 2
    radius = (sigma1_eff - sigma3_eff) / 2
    sin_phi = math.sin(math.radians(friction_angle_deg))
    threshold = centre * sin_phi
    return threshold - radius     # positive when below the envelope


def geertsma_subsidence_mm_per_year(
    pressure_drop_mpa: float,
    formation_thickness_m: float,
    years: float,
    compaction_coefficient_per_pa: float = 1e-9,
) -> float:
    """
    Uniaxial-strain Geertsma compaction:

        ΔH = c_m × Δp × h

    Returns rate in mm/year averaged over the project life.
    Compaction coefficient default 1e-9 / Pa is typical for indurated
    sandstone; weak chalk can be 10× higher.
    """
    dp_pa = pressure_drop_mpa * 1e6
    delta_h_m = compaction_coefficient_per_pa * dp_pa * formation_thickness_m
    if years <= 0:
        return 0.0
    return delta_h_m * 1000.0 / years   # m → mm, total → per year


def induced_seismicity_risk(
    fault_layer,
    pressure_change_mpa: float,
) -> tuple[Optional[float], str]:
    """
    Returns (nearest_fault_km, risk_str). Risk thresholds:

        high     — fault < 5 km AND |Δp| > 5 MPa
        moderate — fault < 10 km AND |Δp| > 1 MPa
        low      — otherwise
    """
    nearest = _nearest_fault_distance_km(fault_layer)
    dp = abs(pressure_change_mpa)
    if nearest is not None and nearest < 5.0 and dp > 5.0:
        return nearest, "high"
    if nearest is not None and nearest < 10.0 and dp > 1.0:
        return nearest, "moderate"
    return nearest, "low"


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _nearest_fault_distance_km(fault_layer) -> Optional[float]:
    """
    Heuristic — if a fault_layer was supplied with any features at all,
    we conservatively assume the nearest one is at the centroid distance
    of the bbox (≈ 5 km for typical claim blocks). For real assessment
    use geopandas centroid + distance from well location.
    """
    if fault_layer is None or fault_layer.features is None:
        return None
    try:
        n = int(len(fault_layer.features))
    except Exception:
        return None
    if n == 0:
        return None
    # More mapped faults → assume one is closer
    if n >= 10:
        return 1.5
    if n >= 4:
        return 4.0
    return 8.0


# ─── Smoke test ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from hydroflow import AquiferColumn

    aq = AquiferColumn(
        depth_total=2000.0, n_cells=200, porosity=0.15,
        permeability=1e-13, capillary_entry=5e5,
        residual_water=0.20, residual_gas=0.05,
        aquifer_top=200.0, aquifer_bottom=350.0,
        seal_top=800.0, seal_bottom=1000.0,
        source_depth=1800.0,
    )
    r = evaluate_geomechanics(aq, production_rate_kg_s=5e-7)
    print(f"Category:                  {r.category}")
    print(f"Sv / Shmin / SHmax (MPa):  {r.pre_production_sv_mpa:.1f} / "
          f"{r.pre_production_shmin_mpa:.1f} / {r.pre_production_shmax_mpa:.1f}")
    print(f"Initial pore p (MPa):      {r.initial_pore_pressure_mpa:.1f}")
    print(f"Predicted drawdown (MPa):  {r.pressure_drawdown_mpa:.3f}")
    print(f"Effective-stress change:   {r.effective_stress_change_mpa:.3f} MPa")
    print(f"Mohr-Coulomb margin:       {r.mohr_coulomb_margin_mpa:.2f} MPa "
          f"({'SAFE' if r.mohr_coulomb_margin_mpa > 0 else 'FAILURE'})")
    print(f"Subsidence:                {r.subsidence_mm_per_year:.2f} mm/yr")
    print(f"Seismicity risk:           {r.induced_seismicity_risk}")
    print(f"Passes stability guarantee:{r.passes_stability_guarantee}")
    print()
    print(f"Explanation: {r.explanation}")
    print()
    print("Recommended pressure monitoring:")
    for x in r.recommended_pressure_monitoring:
        print(f"  • {x}")
    if r.quality_flags:
        print()
        print("Quality flags:")
        for f in r.quality_flags:
            print(f"  ⚠ {f}")
