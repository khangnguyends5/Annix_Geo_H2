"""
annix_geo_h2.hydroflow
─────────────────────────────────────────────────────────────────────────────
Physics layer — multiphase porous-media flow + dissolved hydrogen transport.

Implements the equations specified by Perplexity for Annix Geo H2:

  ∂/∂t (φ ρ_α S_α) + ∇·(ρ_α u_α) = q_α
  u_α = -k k_rα/μ_α (∇p_α - ρ_α g)
  p_g - p_w = p_c(S_w)

  ∂/∂t (φ C) + ∇·(uC - φD∇C) = R

This is a 1D vertical column solver — minimum viable scope that captures the
correct physics. Production version swaps in DuMux. Architecture stays identical.

Author: Annix Geo H2 v0.1
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


# ─── PHYSICAL CONSTANTS ──────────────────────────────────────────────────────
GRAVITY              = 9.81           # m/s²
RHO_WATER            = 1000.0         # kg/m³
RHO_H2_STP           = 0.0899         # kg/m³ at standard pressure
MU_WATER             = 1.0e-3         # Pa·s
MU_H2                = 8.9e-6         # Pa·s
H2_DIFFUSION_WATER   = 5.0e-9         # m²/s  (molecular diffusion)
DISPERSIVITY_LONG    = 1.0            # m     (longitudinal dispersivity)


@dataclass
class AquiferColumn:
    """
    Vertical 1D aquifer column from source depth to surface.

    Coordinate convention: z = 0 at surface, z increases downward.
    Cells are numbered top (0) to bottom (n-1).
    """
    depth_total:      float = 2000.0   # m — total column depth
    n_cells:          int   = 200      # number of vertical cells
    porosity:         float = 0.18     # fraction
    permeability:     float = 1.0e-13  # m² (≈ 100 mD)
    capillary_entry:  float = 5.0e5    # Pa — Pc threshold (Brooks-Corey λ_p)
    residual_water:   float = 0.20     # Swr
    residual_gas:     float = 0.05     # Sgr
    aquifer_top:      float = 200.0    # m — shallow drinking water aquifer depth
    aquifer_bottom:   float = 350.0    # m
    seal_top:         float = 800.0    # m — evaporite seal
    seal_bottom:      float = 1000.0   # m
    source_depth:     float = 1800.0   # m — assumed source location

    def __post_init__(self):
        self.dz = self.depth_total / self.n_cells
        self.z  = np.linspace(self.dz/2, self.depth_total - self.dz/2, self.n_cells)

        # Per-cell properties (allows seal/aquifer/reservoir layering)
        self.k_array         = np.full(self.n_cells, self.permeability)
        self.porosity_array  = np.full(self.n_cells, self.porosity)
        self.pc_array        = np.full(self.n_cells, self.capillary_entry)

        # Mark seal layer — 1000× lower permeability, much higher capillary entry
        seal_mask = (self.z >= self.seal_top) & (self.z <= self.seal_bottom)
        self.k_array[seal_mask]   = self.permeability * 1e-3
        self.pc_array[seal_mask]  = self.capillary_entry * 50   # 25 MPa entry pressure

        # Mark aquifer — higher k, lower pc
        aq_mask = (self.z >= self.aquifer_top) & (self.z <= self.aquifer_bottom)
        self.k_array[aq_mask]     = self.permeability * 5
        self.pc_array[aq_mask]    = self.capillary_entry * 0.2

    # ── geometry helpers ────────────────────────────────────────────────────
    def cell_index(self, depth_m: float) -> int:
        return int(np.clip(depth_m / self.dz, 0, self.n_cells - 1))

    def is_in_aquifer(self, depth_m: float) -> bool:
        return self.aquifer_top <= depth_m <= self.aquifer_bottom

    def is_in_seal(self, depth_m: float) -> bool:
        return self.seal_top <= depth_m <= self.seal_bottom


def brooks_corey_kr(S_w: np.ndarray, S_wr: float, S_gr: float,
                    lambda_p: float = 2.0) -> tuple:
    """
    Brooks-Corey relative permeability for water and gas phases.
    Returns (k_rw, k_rg) for each cell.
    """
    S_eff = np.clip((S_w - S_wr) / (1 - S_wr - S_gr), 1e-6, 1 - 1e-6)
    k_rw  = S_eff ** ((2 + 3*lambda_p) / lambda_p)
    k_rg  = (1 - S_eff)**2 * (1 - S_eff**((2 + lambda_p)/lambda_p))
    return k_rw, k_rg


def darcy_two_phase_step(
    aq:           AquiferColumn,
    S_w:          np.ndarray,      # water saturation [n_cells]
    p_w:          np.ndarray,      # water pressure [Pa]
    source_rate:  float,           # kg/s of H2 injected at source_depth
    dt:           float,           # timestep (s)
) -> tuple:
    """
    One explicit timestep of two-phase Darcy flow (upwind finite volume).

    Returns (S_w_new, p_w_new, gas_flux_top).

    NB: this is a teaching-scale implementation. Stable for the demo aquifer
    parameters but not optimised. Production version uses implicit time-stepping.
    """
    n     = aq.n_cells
    k     = aq.k_array
    phi   = aq.porosity_array
    pc    = aq.pc_array
    S_w_new = S_w.copy()

    # Relative permeabilities
    k_rw, k_rg = brooks_corey_kr(S_w, aq.residual_water, aq.residual_gas)

    # Gas pressure from capillarity
    S_eff   = np.clip((S_w - aq.residual_water) / (1 - aq.residual_water - aq.residual_gas), 1e-6, 1-1e-6)
    pc_curr = pc * S_eff**(-1/2.0)   # Brooks-Corey Pc(Sw)
    p_g     = p_w + pc_curr

    # Phase velocities (upwind, mid-cell)
    u_w = np.zeros(n - 1)
    u_g = np.zeros(n - 1)
    for i in range(n - 1):
        kh   = 2 * k[i] * k[i+1] / (k[i] + k[i+1] + 1e-30)   # harmonic
        krwh = 0.5 * (k_rw[i] + k_rw[i+1])
        krgh = 0.5 * (k_rg[i] + k_rg[i+1])

        # head gradient (downward positive)
        dpw_dz = (p_w[i+1] - p_w[i]) / aq.dz - RHO_WATER * GRAVITY
        dpg_dz = (p_g[i+1] - p_g[i]) / aq.dz - RHO_H2_STP * GRAVITY

        u_w[i] = -kh * krwh / MU_WATER * dpw_dz
        u_g[i] = -kh * krgh / MU_H2    * dpg_dz   # gas migrates UP from buoyancy

    # Source term — H2 injected at source_depth, expressed as volumetric Sg source
    q_g = np.zeros(n)
    src_idx = aq.cell_index(aq.source_depth)
    rho_h2_subsurface = RHO_H2_STP * (1 + aq.source_depth * 0.1)   # crude compressibility
    q_g[src_idx] = source_rate / (rho_h2_subsurface * aq.dz)      # 1/s

    # Saturation update — mass balance. Convention: u_g positive = downward,
    # so u_g[i-1] is the gas flux entering cell i from above (top face), and
    # u_g[i] is the flux leaving through the bottom face.
    for i in range(n):
        flux_top    = u_g[i-1] if i > 0     else 0.0
        flux_bottom = u_g[i]   if i < n-1   else 0.0
        net_gas     = (flux_top - flux_bottom) / aq.dz + q_g[i]
        dS_w        = -dt / phi[i] * net_gas
        S_w_new[i]  = np.clip(S_w[i] + dS_w, aq.residual_water, 1 - aq.residual_gas)

    # Top boundary — gas flux escaping upward (proxy for seepage to surface)
    gas_flux_top = max(0.0, -u_g[0]) * RHO_H2_STP

    # Pressure is held quasi-hydrostatic for MVP (not solving full pressure eqn)
    return S_w_new, p_w, gas_flux_top


# ─── DISSOLVED H2 TRANSPORT ──────────────────────────────────────────────────

def dissolved_h2_step(
    aq:        AquiferColumn,
    C_h2:      np.ndarray,         # dissolved H2 [mol/L]
    S_w:       np.ndarray,         # water saturation
    velocity:  np.ndarray,         # water velocity [m/s] (cell faces, length n-1)
    R_consume: np.ndarray,         # reaction sink [mol/(L·s)]
    dt:        float,
) -> np.ndarray:
    """
    Advection–dispersion–reaction transport of dissolved H2.

      ∂/∂t (φ S_w C) + ∇·(uC - φS_w D ∇C) = -R_consume + dissolution_from_gas

    Upwind finite volume, explicit time step.
    """
    n   = aq.n_cells
    dz  = aq.dz
    phi = aq.porosity_array

    # Effective dispersion: molecular + mechanical
    v_mag = np.zeros(n)
    v_mag[:-1] = np.abs(velocity)
    v_mag[-1]  = v_mag[-2] if n > 1 else 0
    D_eff = H2_DIFFUSION_WATER + DISPERSIVITY_LONG * v_mag

    C_new = C_h2.copy()

    for i in range(n):
        # advection (upwind based on velocity direction)
        adv_in  = 0.0
        adv_out = 0.0
        if i > 0:
            v = velocity[i-1] if i-1 < len(velocity) else 0
            # Upwind flux at top face, signed (+ = downward into cell i).
            adv_in = max(v, 0) * C_h2[i-1] + min(v, 0) * C_h2[i]
        if i < n - 1:
            v = velocity[i] if i < len(velocity) else 0
            adv_out = max(v, 0) * C_h2[i] + min(v, 0) * C_h2[i+1]

        # dispersion
        disp_in  = 0.0
        disp_out = 0.0
        if i > 0:
            disp_in  = D_eff[i] * (C_h2[i-1] - C_h2[i]) / dz
        if i < n - 1:
            disp_out = D_eff[i] * (C_h2[i] - C_h2[i+1]) / dz

        # Dissolution from free gas phase (Henry's law proxy)
        # H2 Henry constant ~ 0.78 mM/atm. Only dissolves where gas saturation
        # exceeds residual (active gas phase actually present).
        S_g = max(0, 1 - S_w[i] - aq.residual_gas)
        C_eq = 7.8e-6 * S_g   # mol/L — realistic Henry equilibrium
        dissolution_rate = 0.001 * max(0, C_eq - C_h2[i])  # slow relaxation

        net_flux = (adv_in - adv_out + disp_in - disp_out) / dz
        dC = dt * (net_flux / (phi[i] * S_w[i] + 1e-9)
                   - R_consume[i] + dissolution_rate)
        C_new[i] = max(0.0, C_h2[i] + dC)

    return C_new


# ─── INITIAL CONDITIONS ──────────────────────────────────────────────────────

def initial_conditions(aq: AquiferColumn) -> dict:
    """Hydrostatic water, fully saturated, zero dissolved H2."""
    p_w = RHO_WATER * GRAVITY * aq.z + 101325.0          # hydrostatic + atmospheric
    S_w = np.full(aq.n_cells, 1 - aq.residual_gas)       # initially water-filled
    C_h2= np.full(aq.n_cells, 1e-9)                      # near zero
    return {"p_w": p_w, "S_w": S_w, "C_h2": C_h2}
