# Annix Geo H2 — Part 1

**Subsurface hydrogen leak detection through aquifer migration modelling and geochemical signature analysis.**

The first product in the **Annix Geo** family (sibling: **Annix Geo Minerals**). An industrial-tier AI tool that solves two specific problems Cerulean Energy must answer for every natural hydrogen target:

1. Model subsurface gas migration through aquifers
2. Identify the geochemical signature in deep groundwater that indicates a major hydrogen source is leaking from below

## Architecture

Five layers, per Perplexity specification:

```
annix_geo_h2/
├── hydroflow/           # Layer 2 — Multiphase Darcy flow + H2 transport
├── hydrochem/           # Layer 3 — Redox reaction kinetics (5 reactions)
├── source_detect/       # Layer 4 — Anomaly scoring (redox pattern detection)
├── uncertainty/         # Layer 5 — Monte Carlo classification (4 categories)
├── containment/         # Layer 6 — Safety guarantee #1: drinking-water aquifer
├── geomechanics/        # Layer 7 — Safety guarantee #2: rock-formation stability
├── demo/                # Synthetic Saskatchewan Duperow aquifer
├── dossier.py           # Customer dossier generator (uses annix_intel)
└── run.py               # CLI entry point
```

## Run the demo

```bash
python run.py              # both scenarios
python run.py --leak       # leak scenario only
python run.py --natural    # natural reducing zone only
```

## Generate a customer dossier

The `dossier.py` module wires the simulation pipeline into `annix_intel` for
live AGS data + Claude-orchestrated narrative. Output is a 3-page Markdown
file ready to email to a CEO or chief geologist.

```bash
# Requires sibling annix_intel/ checkout. Set ANTHROPIC_API_KEY for the
# real LLM verdict; otherwise falls back to a stub that still renders the
# full simulation + classifier output.
python dossier.py \
    --operator "Acme Hydrogen Corp" \
    --block    "Wabamun-N Block 4" \
    --bbox     -114.5 53.2 -113.8 53.6 \
    --formation Duperow \
    --out      dossiers/acme_wabamun_n4.md
```

If `VOYAGE_API_KEY` is set and the RAG corpus has been built, the LLM
verdict is grounded in retrieved geological literature with citations.

## What it does

The tool simulates a 2 km vertical aquifer column with:

- A shallow drinking-water aquifer (200–350 m)
- A Devonian evaporite seal (800–1000 m)
- A hypothesised hydrogen source at the Precambrian basement (1800 m)

It then runs coupled physics and chemistry simulation for 180 timesteps (≈1 year), tracking:

- Two-phase Darcy flow (water + gas saturation)
- Dissolved H₂ transport with advection-dispersion-reaction
- Five H₂-consuming redox reactions: sulfate, nitrate, iron, manganese reduction + methanogenesis
- Spatial coherence of the H₂ anomaly relative to inferred source depth

## Output

The classifier returns one of four probabilistic categories with confidence intervals:

- **Likely leak** — leak_likelihood ≥ 0.65 AND spatial coherence ≥ 0.4
- **Possible leak** — leak_likelihood ≥ 0.35
- **Likely natural reducing zone** — moderate anomaly but no depth coherence
- **Insufficient evidence** — signal below threshold or noise too high

Plus: source depth estimate with uncertainty, recommended sampling locations, and quality flags.

## What this is not

This is a minimum viable prototype with the correct physics architecture. It is not yet a production groundwater simulator. For production deployment, the physics engine should be swapped to DuMux and the chemistry engine to PHREEQC. The architecture and output schema remain identical.

The point of this build is to demonstrate that Cerulean's methodology produces probabilistic, defensible answers to the two critical questions — and to provide an engine that runs on real data once it is available.

## Next steps

**Part 2 — Safety guarantees — shipped in v0.2.**

Two new layers (`containment/` and `geomechanics/`) compute pass/fail
verdicts on the two regulatory requirements any natural-hydrogen developer
must answer:

1. **Drinking-water aquifer protection.** Brooks-Corey seal capacity check
   against the predicted gas column, AGS fault-intersection risk score, and
   a 100-year analytical prediction of dissolved H₂ in the protected aquifer
   versus the USDW threshold (1e-6 mol/L). Output: classification ∈
   {Strong, Adequate, At-risk, Insufficient containment} + a
   recommended monitoring program.

2. **Rock-formation stability.** Andersonian initial stress at the source
   depth, radial-Darcy pressure drawdown over the project life, Mohr-Coulomb
   margin to frictional failure, Geertsma uniaxial subsidence rate, and an
   induced-seismicity risk score from fault distance × pressure change.
   Output: classification ∈ {Stable, Monitor, At-risk, Critical} +
   recommended pressure-monitoring + InSAR program.

Both modules are MVP-grade (same caveat as the rest of the codebase — swap
to OpenGeoSys/PFLOTRAN-FLAC for THM-coupled production simulation when a
customer requires it). The classifier output schemas are stable; the
implementations can be swapped without changing downstream code.

Sections 7 and 8 of every generated dossier now carry the pass/fail
verdicts, the underlying numbers, and the monitoring recommendations.
