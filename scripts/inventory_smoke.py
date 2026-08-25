#!/usr/bin/env python3
"""One real headless episode of the M6 ``inventory_build`` heterogeneous mission.

Proves the full 11-node graph runs end to end through the GENERIC node-kind loop
(perceive / decide / verify / manipulate) with real per-node outcomes, and
measures wall-clock. This is a SMOKE, not a sealed claim: it runs on a SCRATCH
seed (default 990000, well outside the 50000 evidence block) and writes nothing
to runs/ or the ledger.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/inventory_smoke.py --seed 990000

Mount + brief mirror scripts/harness_runtime._run_task: base_profile patched with
the card's planner + composite policy, and the card's PREDICATES table threaded
onto the brief beside catalogue/oracles so the loop resolves each kindful node's
machine oracle. max_actuations covers the 11 nodes plus replan headroom.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from harness.manifest import discover
from plugins.task import workload
from profiles import base_profile

TASK = "inventory_build"


def _load_attr(ref: str):
    module_name, attr = ref.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=990000, help="SCRATCH seed")
    parser.add_argument("--max-replans", type=int, default=3)
    parser.add_argument("--max-actuations", type=int, default=20)
    args = parser.parse_args()

    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/inventory_build installed?")

    plan = resolve_plan(base_profile(), patches=(
        Patch("inventory_smoke", override=(
            Mount("task.planner", binding["planner"]),
            Mount("policy.driver", binding["policy"]),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    print(f"mount plan sha {plan.sha()[:12]}  ({len(plan.mounts)} capabilities)\n")

    brief = {"task": TASK, "catalogue": _load_attr(binding["catalogue"]),
             "oracles": _load_attr(binding["oracles"]),
             "predicates": _load_attr(binding["predicates"])}
    t0 = time.perf_counter()
    out = workload.run(brief, kernel, seed=args.seed,
                       max_replans=args.max_replans, max_actuations=args.max_actuations)
    dt = time.perf_counter() - t0

    plan_nodes = InventoryKinds()
    print(f"goal: {out['goal']}\n")
    for nid, node in out["nodes"].items():
        extra = ""
        if node.get("facts") is not None:
            extra = f"  facts={list((node['facts'].get('poses') or node['facts']).keys())}"
        if node.get("decision") is not None:
            extra = f"  decision={node['decision']}"
        stages = "".join(f" [{s['name']}:{'ok' if s['success'] else 'x'}]"
                         for s in node.get("stages", ()))
        print(f"node {nid:<16} kind={plan_nodes.get(nid):<11} "
              f"success={node['success']}{stages}{extra}")
    for fault in out["faults"]:
        print(f"fault: {fault['msg']}")
    ran = len(out["nodes"])
    print(f"\nnodes_run {ran}  replans {out['replans']}  "
          f"actuations {out['actuations']}  success {out['success']}  "
          f"wall {dt:.2f}s")
    print(json.dumps({"seed": args.seed, "nodes_run": ran,
                      "success": out["success"], "seconds": round(dt, 3)},
                     sort_keys=True))
    # smoke passes if the FULL heterogeneous graph executed end to end
    return 0 if ran >= 10 else 1


class InventoryKinds:
    """Map node id -> declared kind, read from the card's own plan (no hardcode)."""

    def __init__(self):
        planner = _load_attr(discover().task_bindings[TASK]["planner"])()
        self._k = {n["id"]: n.get("kind", "manipulate")
                   for n in planner.plan({"task": TASK})["nodes"]}

    def get(self, nid: str) -> str:
        return self._k.get(nid, "?")


if __name__ == "__main__":
    sys.exit(main())
