#!/usr/bin/env python3
"""One-command Stack task-planning closed loop (round 83, M3 first loop).

Mount shape mirrors scripts/stack_campaign.py: the base profile, patched with
the Stack policy driver and the deterministic StackPlanner behind the new
task.planner capability. One command runs the whole seam -- plan -> validate ->
governed rollout with stack_stages scoring -> verify -> (replan on failure) ->
ledger note -- and prints each stage result plus the chained note.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/task_plan.py --seed 90000

Round 86 adds ``--task clear_table``: the first true multi-node graph (pick
the can, then the milk), driven by the four-phase scripted grasp policy.

Every number is measured in simulation. The ledger stays in memory: this is a
seam proof, not a sealed claim, and runs/ holds only campaign artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.task import workload
from plugins.task.planner_stack import CATALOGUE, ORACLES
from profiles import base_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", choices=("stack", "clear_table"), default="stack")
    parser.add_argument("--seed", type=int, default=90000)
    parser.add_argument("--max-replans", type=int, default=3)
    parser.add_argument("--max-actuations", type=int, default=None,
                        help="default 3; 4 for clear_table (two nodes need headroom)")
    args = parser.parse_args()
    if args.max_actuations is None:
        args.max_actuations = 4 if args.task == "clear_table" else 3
    # clear_table's pick nodes run the four-phase scripted grasp, not the
    # eight-phase stack driver.
    policy_ref = ("plugins.policies:provider" if args.task == "clear_table"
                  else "plugins.policies:stack_scripted_provider")

    plan = resolve_plan(base_profile(), patches=(
        Patch("task", override=(
            Mount("task.planner", "plugins.task.planner_stack:provider"),
            Mount("policy.driver", policy_ref),)),))
    log = SessionLog()
    kernel = Kernel(CAPABILITIES, log=log)
    kernel.mount(plan)
    print(f"mount plan sha {plan.sha()[:12]}  "
          f"({len(plan.mounts)} capabilities mounted)\n")

    brief = {"task": args.task, "catalogue": CATALOGUE, "oracles": ORACLES}
    out = workload.run(brief, kernel, seed=args.seed,
                       max_replans=args.max_replans,
                       max_actuations=args.max_actuations)

    print(f"goal: {out['goal']}")
    for nid, node in out["nodes"].items():
        print(f"node {nid}: success={node['success']}  steps={node['steps']}")
        for s in node["stages"]:
            print(f"  stage {s['name']:<6} success={s['success']}  "
                  f"entered={s['entered_step']}  exited={s['exited_step']}")
    for fault in out["faults"]:
        print(f"fault: {fault['msg']}")
    print(f"\nreplans {out['replans']}  actuations {out['actuations']}  "
          f"success {out['success']}")

    note = next(r for r in log.rows() if r["kind"] == "task.plan_complete")
    print(f"ledger: {len(log.rows())} chained rows, verify={log.verify()}")
    print(f"task.plan_complete: {json.dumps(note['data'], sort_keys=True)}")
    return 0 if out["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
