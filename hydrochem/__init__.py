"""
annix_geo_h2.hydrochem
─────────────────────────────────────────────────────────────────────────────
Chemistry layer — H2-driven redox reaction kinetics.

Implements the reaction stack from Perplexity:
  - Sulfate reduction:    H2 + 0.25 SO4²⁻ → 0.25 HS⁻ + H2O
  - Nitrate reduction:    H2 + 0.4 NO3⁻ → 0.2 N2 + 0.4 OH⁻ + 0.6 H2O
  - Iron reduction:       H2 + 2 Fe(III) → 2 Fe(II) + 2 H⁺
  - Manganese reduction:  H2 + Mn(IV) → Mn(II) + 2 H⁺
  - Methanogenesis:       4 H2 + CO2 → CH4 + 2 H2O

Each reaction uses a Monod-style rate law:
  R_i = k_i · f(C_H2) · f(C_acceptor) · f(T) · f(pH)
"""

import numpy as np
from dataclasses import dataclass, field


# ─── REACTION RATE CONSTANTS ─────────────────────────────────────────────────
# These are simplified values calibrated to give reasonable plume behaviour.
# Production version should be calibrated to PHREEQC equilibrium constants.

@dataclass
class ReactionRates:
    k_sulfate:    float = 5.0e-8   # 1/s — sulfate reduction (slower, realistic)
    k_nitrate:    float = 3.0e-7   # 1/s — fastest of the redox sequence
    k_iron:       float = 4.0e-8   # 1/s — Fe(III) reduction
    k_manganese:  float = 5.0e-8   # 1/s — Mn(IV) reduction
    k_methano:    float = 8.0e-9   # 1/s — slowest, only late-stage

    K_half_h2:    float = 5e-6     # mol/L — half-saturation for H2
    K_half_acc:   float = 1e-4     # mol/L — half-saturation for acceptors

    Q10:          float = 2.0      # rate doubles per 10°C
    pH_optimum:   float = 7.0
    pH_width:     float = 2.0


@dataclass
class ChemistryState:
    """Dissolved species concentrations [mol/L] at each cell."""
    H2:      np.ndarray
    SO4:     np.ndarray   # sulfate
    NO3:     np.ndarray   # nitrate
    Fe3:     np.ndarray   # Fe(III)
    Mn4:     np.ndarray   # Mn(IV)
    CO2:     np.ndarray
    HS:      np.ndarray   # sulfide (product)
    Fe2:     np.ndarray   # Fe(II) (product)
    Mn2:     np.ndarray   # Mn(II) (product)
    CH4:     np.ndarray   # methane (product)
    pH:      np.ndarray
    alkalinity: np.ndarray


def monod(C: np.ndarray, K: float) -> np.ndarray:
    """Monod (Michaelis-Menten) saturation."""
    return C / (C + K + 1e-30)


def temperature_factor(T_celsius: float, Q10: float = 2.0,
                       T_ref: float = 25.0) -> float:
    return Q10 ** ((T_celsius - T_ref) / 10.0)


def ph_factor(pH: np.ndarray, pH_opt: float, width: float) -> np.ndarray:
    """Gaussian pH inhibition."""
    return np.exp(-((pH - pH_opt) / width) ** 2)


# ─── REACTION STEP ───────────────────────────────────────────────────────────

def reaction_step(
    state:        ChemistryState,
    rates:        ReactionRates,
    temperature:  np.ndarray,        # °C per cell
    dt:           float,
) -> tuple:
    """
    One explicit Euler step of all H2 redox reactions.

    Returns (new_state, total_h2_consumption_per_cell)
    """
    n = len(state.H2)
    new = ChemistryState(
        H2=state.H2.copy(),       SO4=state.SO4.copy(),
        NO3=state.NO3.copy(),     Fe3=state.Fe3.copy(),
        Mn4=state.Mn4.copy(),     CO2=state.CO2.copy(),
        HS=state.HS.copy(),       Fe2=state.Fe2.copy(),
        Mn2=state.Mn2.copy(),     CH4=state.CH4.copy(),
        pH=state.pH.copy(),       alkalinity=state.alkalinity.copy(),
    )

    fH2  = monod(state.H2, rates.K_half_h2)
    fpH  = ph_factor(state.pH, rates.pH_optimum, rates.pH_width)
    fT   = np.array([temperature_factor(T, rates.Q10) for T in temperature])
    common = fH2 * fpH * fT

    # ── Reaction rates [mol H2 consumed per L per second] ─────────────────────
    r_nitrate = rates.k_nitrate   * common * monod(state.NO3, rates.K_half_acc)
    r_sulfate = rates.k_sulfate   * common * monod(state.SO4, rates.K_half_acc)
    r_iron    = rates.k_iron      * common * monod(state.Fe3, rates.K_half_acc)
    r_mn      = rates.k_manganese * common * monod(state.Mn4, rates.K_half_acc)
    r_methano = rates.k_methano   * common * monod(state.CO2, rates.K_half_acc)

    # Nitrate has kinetic priority — suppress slower reactions when NO3 high
    no3_inhibit  = 1.0 / (1.0 + state.NO3 / 1e-5)
    r_sulfate    *= no3_inhibit
    r_iron       *= no3_inhibit
    r_mn         *= no3_inhibit
    r_methano    *= no3_inhibit

    # Cap each reaction by acceptor availability over dt. Without this, fast
    # kinetics + large dt yields rates that consume more acceptor than exists,
    # and feed a nonsensical R_consume into the transport step.
    inv_dt = 1.0 / max(dt, 1e-30)
    r_nitrate = np.minimum(r_nitrate, state.NO3 * inv_dt / 0.4)
    r_sulfate = np.minimum(r_sulfate, state.SO4 * inv_dt / 0.25)
    r_iron    = np.minimum(r_iron,    state.Fe3 * inv_dt / 2.0)
    r_mn      = np.minimum(r_mn,      state.Mn4 * inv_dt / 1.0)
    r_methano = np.minimum(r_methano, state.CO2 * inv_dt / 0.25)

    total_h2_rate = r_nitrate + r_sulfate + r_iron + r_mn + r_methano

    # Cap so total H2 consumption can't exceed what's there in this step.
    h2_avail_rate = state.H2 * inv_dt
    scale = np.where(total_h2_rate > h2_avail_rate,
                     h2_avail_rate / (total_h2_rate + 1e-30), 1.0)
    r_nitrate *= scale
    r_sulfate *= scale
    r_iron    *= scale
    r_mn      *= scale
    r_methano *= scale
    total_h2_rate = r_nitrate + r_sulfate + r_iron + r_mn + r_methano

    # ── Update species (stoichiometry from balanced reactions) ────────────────
    new.H2  = np.maximum(state.H2 - dt * total_h2_rate, 0)

    new.NO3 = np.maximum(state.NO3 - dt * 0.4 * r_nitrate, 0)
    new.SO4 = np.maximum(state.SO4 - dt * 0.25 * r_sulfate, 0)
    new.Fe3 = np.maximum(state.Fe3 - dt * 2.0 * r_iron, 0)
    new.Mn4 = np.maximum(state.Mn4 - dt * 1.0 * r_mn, 0)
    new.CO2 = np.maximum(state.CO2 - dt * 0.25 * r_methano, 0)

    new.HS  = state.HS  + dt * 0.25 * r_sulfate
    new.Fe2 = state.Fe2 + dt * 2.0  * r_iron
    new.Mn2 = state.Mn2 + dt * 1.0  * r_mn
    new.CH4 = state.CH4 + dt * 0.25 * r_methano

    # pH and alkalinity shift (qualitative — reactions consume H+ overall)
    dH_plus     = dt * (2 * r_iron + 2 * r_mn - 0.4 * r_nitrate)
    new.pH      = np.clip(state.pH - 0.05 * dH_plus * 1e3, 6.0, 10.0)
    new.alkalinity = state.alkalinity + dt * 0.5 * (r_nitrate + r_methano)

    return new, total_h2_rate


# ─── INITIAL CHEMISTRY ───────────────────────────────────────────────────────

def initial_chemistry(n_cells: int) -> ChemistryState:
    """Typical Saskatchewan Duperow aquifer background chemistry."""
    return ChemistryState(
        H2  = np.full(n_cells, 1e-9),         # essentially zero
        SO4 = np.full(n_cells, 2.5e-3),       # ~240 mg/L (formation water)
        NO3 = np.full(n_cells, 5e-5),         # low, deep formation
        Fe3 = np.full(n_cells, 1e-4),         # iron oxides in matrix
        Mn4 = np.full(n_cells, 5e-5),         # Mn oxide coatings
        CO2 = np.full(n_cells, 1e-3),         # dissolved CO2
        HS  = np.full(n_cells, 1e-7),
        Fe2 = np.full(n_cells, 1e-6),
        Mn2 = np.full(n_cells, 1e-7),
        CH4 = np.full(n_cells, 1e-7),
        pH  = np.full(n_cells, 7.4),          # neutral background
        alkalinity = np.full(n_cells, 3e-3),
    )
