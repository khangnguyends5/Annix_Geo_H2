"""
Annix-Geo H2 — CLI entry point.

Run from the project root:
    python run.py              # full demo, both scenarios
    python run.py --leak       # leak scenario only
    python run.py --natural    # natural reducing zone only
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Box-drawing characters in the report break on Windows' default cp1252 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from demo.saskatchewan_duperow import (
    build_duperow_aquifer, run_simulation, print_scenario_report, main
)

if __name__ == "__main__":
    if "--leak" in sys.argv:
        aq = build_duperow_aquifer()
        result = run_simulation(aq, scenario="leak", n_timesteps=180, dt_days=2.0)
        print_scenario_report("A — Active Deep H2 Leak", result, aq)
    elif "--natural" in sys.argv:
        aq = build_duperow_aquifer()
        result = run_simulation(aq, scenario="natural", n_timesteps=180, dt_days=2.0)
        print_scenario_report("B — Natural Reducing Zone", result, aq)
    else:
        main()
