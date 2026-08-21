#!/usr/bin/env python3
"""One-command end-to-end demo of the backbone pipeline.

Mounts the base profile on the kernel, runs a real robosuite/MuJoCo evolution
campaign through the RSI workload, and prints the skills it published. Every
number you see is measured in simulation; nothing is mocked.

    PYTHONPATH=. .venv/bin/python scripts/demo_campaign.py

Takes roughly 10-15 minutes on an M-series Mac (about 2,500 episodes at ~210
episodes/min). The demo uses its own seed blocks (30000+), which are not part
of any published claim in docs/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.rsi.campaign import Preregistration
from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.rsi.workload import run as rsi_run
from profiles import base_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--out", type=Path, default=Path("runs/demo"),
                        help="campaign store + skill graph output (default runs/demo)")
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a campaign store; pass a fresh --out", file=sys.stderr)
        return 2

    # Config layering in action: the base profile, patched so the skill graph
    # persists to disk. Providers are strings; the resolved plan is content-hashed.
    plan = resolve_plan(base_profile(), patches=(
        Patch("demo", override=(
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(args.out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(args.out / "session-log"))
    kernel.mount(plan)
    print(f"mount plan sha {plan.sha()[:12]}  "
          f"({len(plan.mounts)} capabilities mounted)\n")

    prereg = Preregistration(
        dev=tuple(range(30000, 30140)), heldout=tuple(range(30200, 30300)),
        percept_noise=0.02, task="lift", policy="scripted",
        critic_budget=0, action_budget=0, recovery_sensor_sd=0.02,
        max_generations=2, scale_dev_by_power=True,
    )
    out = rsi_run(prereg, args.out, kernel, workers=args.workers)

    print("\n=== published skills ===")
    graph = kernel.resolve("graph.skill", consumer="demo")
    for digest, skill in zip(out["skills"], graph.skills()):
        gate = skill["effects"]["dev_gate_vs_parent"]
        print(f"  {digest[:12]}  gen{skill['generation']}  "
              f"trigger {skill['preconditions']['feature']} "
              f"{skill['preconditions']['op']} {skill['preconditions']['threshold']:.4g}  "
              f"dev {gate['base_rate']:.1%} -> {gate['governed_rate']:.1%} "
              f"({gate['fixed']} fixed / {gate['broken']} broken)")
    heldout = out["result"].get("heldout") or {}
    if heldout:
        print(f"\nheld-out (n={heldout['n']}): {heldout['base_rate']:.1%} -> "
              f"{heldout['governed_rate']:.1%}, {heldout['fixed']} fixed / "
              f"{heldout['broken']} broken, p={heldout['p_value']:.2g}")
    print(f"\nartifacts: {args.out}/  (content-addressed store, chained session log, "
          f"skill records under skills/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
