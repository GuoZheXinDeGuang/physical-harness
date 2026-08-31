#!/usr/bin/env python3
"""Run the grasp-cube DISCOVERY campaign (M6 §3b c3 pivot; from-scratch).

The preregistration is ``grasp_cube_prereg()`` from scripts/prereg_grasp_cube.py
-- the demo Lift campaign field-for-field, moved to the grasp-cube node's exact
operating point (percept_noise 0.012, pick_stages, terminal_label, the chain's
clear_build_provider policy) and fresh blocks 51000-51299 (dev) / 51300-51499
(held-out). parent_store=None -> from-scratch (the machinery decision, sealed in
runs/scripted-calibration/grasp-cube-cal/campaign_plan: the demo lineage was earned at 0.02-noise /
stageless / base_rate 0.43, so seeding it would import a mis-calibrated threshold).

Runs only after probe_grasp_cube.py cleared the §4 go/no-go. Held-out is NOT
deferred: run_campaign scores it ONCE at the end iff the bundle promotes >=1 rule
(paired_gate over the bundle) -- exactly the "burn held-out once, only on a
promotion" discipline. Zero promotions -> bundle.rules empty -> held-out untouched.

Mount = base_profile + clear_build_provider policy (the grasp-cube node's actual
policy_ref) + an on-disk skill graph, so rsi_run publishes each promoted+
judgement-established rule as a SkillRecord under <out>/skills for the fold.
rsi_run stamps env/policy/percept onto the prereg from this mount -> its sha
equals the sealed prereg sha (both resolve the same refs). Nothing new here.

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/grasp_cube_campaign.py \
        --out runs/scripted-calibration/grasp-cube-g1
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from harness.manifest import discover
from plugins.rsi.workload import run as rsi_run
from profiles import base_profile
from scripts.prereg_grasp_cube import grasp_cube_prereg


def _mount(out: Path) -> Kernel:
    """base_profile + the chain's clear_build_provider policy + on-disk skill
    graph -- the grasp-cube node's exact policy mount (lift -> four-phase driver);
    embodiment.env / percept.model keep base refs."""
    binding = discover().task_bindings["inventory_build"]
    plan = resolve_plan(base_profile(), patches=(
        Patch("grasp_cube_campaign", override=(
            Mount("policy.driver", binding["policy"]),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def _stamped_sha(prereg, kernel: Kernel) -> str:
    return dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model")).sha()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "runs/scripted-calibration/grasp-cube-g1")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    if (args.out / "index.jsonl").exists():
        print(f"{args.out} already holds a store; pass a fresh --out", file=sys.stderr)
        return 2

    prereg = grasp_cube_prereg()   # held-out KEPT: scored once iff a rule promotes
    kernel = _mount(args.out)
    print(f"from-scratch prereg sha {_stamped_sha(prereg, kernel)}  -> {args.out}")
    print(f"dev={len(prereg.dev)} (51000-51299)  heldout={len(prereg.heldout)} "
          f"(51300-51499, scored once ONLY on a promotion)\n", flush=True)

    out = rsi_run(prereg, args.out, kernel, workers=args.workers)
    r = out["result"]
    print(f"\ngenerations={r['generations']} promoted={r['promoted']} "
          f"final_sha={r['final_sha'][:12]} rules={r['rules']}")
    if out["skills"]:
        print(f"published skills: {[d[:12] for d in out['skills']]}")
    held = r.get("heldout")
    if held:
        print(f"held-out (n={held['n']}): base {held['base_rate']:.1%} -> "
              f"governed {held['governed_rate']:.1%}, fixed {held['fixed']} / "
              f"broken {held['broken']}, p={held['p_value']:.4g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
