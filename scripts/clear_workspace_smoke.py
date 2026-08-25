#!/usr/bin/env python3
"""One real headless episode of the M7 ``clear_workspace`` PERSISTENT mission.

Proves the whole point of M7: ONE robosuite mode-0 PickPlace episode (one make,
one reset, one close) threaded through the ≥12-node graph, four sub-goals driven
SEQUENTIALLY in the SAME world, each verify reading the LIVE ``not_in_bin`` state,
in-episode replan on failure (a dropped/unplaceable object skipped, the world
carrying forward -- never a reset), and a final machine report. Measures wall-clock.

This is a SMOKE, not a sealed claim: it runs on a SCRATCH seed (default 900003,
well outside every evidence block and below the RandomState overflow) and writes
nothing to runs/ or the ledger.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/clear_workspace_smoke.py --seed 900003

Mount + brief mirror scripts/harness_runtime._run_task: base_profile patched with
the card's planner + composite policy, and the card's PREDICATES / EPISODE block /
SEGMENT_SPECS threaded onto the brief so workload.run opens the ONE persistent
world and drives each segment in it. The per-segment seal carries the env-step
SPAN off the single shared cursor -- contiguous, monotonic spans are the evidence
that it was one episode with no resets between sub-goals.
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

TASK = "clear_workspace"


def _load_attr(ref: str):
    module_name, attr = ref.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=900003, help="SCRATCH seed")
    parser.add_argument("--max-replans", type=int, default=10)
    parser.add_argument("--max-actuations", type=int, default=40)
    args = parser.parse_args()

    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/clear_workspace installed?")

    plan = resolve_plan(base_profile(), patches=(
        Patch("clear_workspace_smoke", override=(
            Mount("task.planner", binding["planner"]),
            Mount("policy.driver", binding["policy"]),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    print(f"mount plan sha {plan.sha()[:12]}  ({len(plan.mounts)} capabilities)\n")

    brief = {"task": TASK, "catalogue": _load_attr(binding["catalogue"]),
             "oracles": _load_attr(binding["oracles"]),
             "predicates": _load_attr(binding["predicates"]),
             "episodic": True, "episode": _load_attr(binding["episode"]),
             "segment_specs": _load_attr(binding["segment_specs"])}
    kinds = {n["id"]: n.get("kind", "manipulate")
             for n in _load_attr(binding["planner"])().plan({"task": TASK})["nodes"]}

    t0 = time.perf_counter()
    out = workload.run(brief, kernel, seed=args.seed,
                       max_replans=args.max_replans, max_actuations=args.max_actuations)
    dt = time.perf_counter() - t0

    print(f"goal: {out['goal']}\n")
    spans: list[tuple[int, int]] = []
    for nid, node in out["nodes"].items():
        gov = node.get("governance") or {}
        span = ""
        if "entered_env_step" in gov:
            e, x = gov["entered_env_step"], gov["exited_env_step"]
            span = f"  env_step[{e}->{x}]"
            spans.append((e, x))
        stages = "".join(f" [{s['name']}:{'ok' if s['success'] else 'x'}]"
                         for s in node.get("stages", ()))
        extra = ""
        if node.get("decision") is not None:
            extra = f"  decision={node['decision']}"
        elif node.get("facts") is not None:
            extra = f"  facts={node['facts']}"
        print(f"node {nid:<15} kind={kinds.get(nid, '?'):<9} "
              f"success={str(node['success']):<5}{span}{stages}{extra}")
    print()
    for fault in out["faults"]:
        print(f"replan<- fault: {fault['msg']}")

    # ── the ONE-episode evidence: the segment spans read off the shared cursor ──
    # The spans shown are the FINAL seal per node; a grasp that slipped was re-driven
    # in place, so its earlier attempt's span was overwritten -- which is why the
    # final spans can leave gaps. What matters is that the cursor only ever advances
    # (no rewind == no reset) and the LAST exited step is the whole episode's step
    # count, > 4x the nominal exactly because retries drove more steps in one world.
    monotonic = all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
    total_steps = spans[-1][1] if spans else 0
    seg_retries = sum(1 for f in out["faults"] if str(f.get("node", "")).startswith("clear-"))
    verifies = {nid: n["success"] for nid, n in out["nodes"].items()
                if kinds.get(nid) == "verify"}
    print(f"\nsegment step-spans (final seal, one shared cursor): {spans}")
    print(f"  cursor monotonic, never rewinds (== ONE episode, no reset between sub-goals): {monotonic}")
    print(f"  segments driven: {len(spans)}   total env steps on the one cursor: {total_steps}")
    print(f"live-state verifies fired on the live world: {verifies}")
    print(f"in-episode replans: {out['replans']}  (of which {seg_retries} were "
          f"grasp-slip SEGMENT retries -- the same object re-driven where it now sits, no reset)")
    rep = (out['nodes'].get('report') or {}).get('decision')
    print(f"report: {rep}")
    print(f"\nnodes_run {len(out['nodes'])}  replans {out['replans']}  "
          f"actuations {out['actuations']}  success {out['success']}  wall {dt:.2f}s")
    print(json.dumps({"seed": args.seed, "segments": len(spans),
                      "one_episode_monotonic_cursor": monotonic, "total_env_steps": total_steps,
                      "verifies_fired": len(verifies), "replans": out["replans"],
                      "segment_retries": seg_retries, "cleared": (rep or {}).get("cleared"),
                      "success": out["success"], "seconds": round(dt, 3)},
                     sort_keys=True))
    # smoke passes if the persistent episode drove every sub-goal on ONE monotonic
    # cursor and at least one live verify fired -- the architecture, not a promotion
    return 0 if (len(spans) >= 4 and monotonic and verifies) else 1


if __name__ == "__main__":
    sys.exit(main())
