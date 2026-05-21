# Annix-Geo H2 — Part 1

**Subsurface hydrogen leak detection through aquifer migration modelling and geochemical signature analysis.**

The first child of Annix-Geo. An industrial-tier AI tool that solves two specific problems Cerulean Energy must answer for every natural hydrogen target:

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
├── demo/                # Synthetic Saskatchewan Duperow aquifer
└── run.py               # CLI entry point
```

## Run the demo

```bash
python run.py              # both scenarios
python run.py --leak       # leak scenario only
python run.py --natural    # natural reducing zone only
```

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

Part 2 of Annix-Geo H2 will add the two safety guarantees:

- The gas will not migrate and contaminate shallow drinking-water aquifers
- Pressure changes will not destabilise the surrounding rock formations
