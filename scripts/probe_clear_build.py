#!/usr/bin/env python3
"""Calibration probe for the clear_build long-horizon mission (local-archive/docs/retired-from-public/long-horizon-design.md §4).

Runs the BASELINE (ungoverned) 4-node chain over the calibration block and reports
the go/no-go numbers the design's abort criteria read: chain base rate, q_pre =
P(n1..n3 all succeed), a per-node first-death histogram (which node kills the
chain), per-node success, and timing. Calibration blocks NEVER gate (STATUS
ledger); this only MEASURES. Baseline arm only on purpose: n1..n3 (grasp/pick/
pick) are ungoverned in EVERY arm, so the baseline fixes q_pre and the chain
base rate exactly, and governance only ever touches n4 (build-stack).

Parallel across seeds (the calibrate_stack Pool pattern): the parent does NO sim
work before the fork, each worker builds its own kernel + env. Composes
workload.run over the card's binding -- seals nothing, measures everything.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/probe_clear_build.py \
        --seeds 48900:49050 --out runs/clear-build-cal/calibration.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TASK = "clear_build"
#: n1..n3 -- the ungoverned pre-nodes whose joint success IS q_pre. Governance
#: only reaches n4 (build-stack), so the baseline arm fixes q_pre for every arm.
PRE_NODES = ("grasp-cube", "pick-can", "pick-milk")
GOVERNED_NODE = "build-stack"
#: Battery operating config (chain_battery defaults): 4 nodes + replan headroom.
MAX_REPLANS, MAX_ACTUATIONS = 2, 6


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def _probe_one(seed: int) -> dict:
    """One baseline (ungoverned) chain. Builds its own kernel so it is a clean
    Pool task -- no sim state crosses the fork."""
    from harness.config import Mount, Patch, resolve_plan
    from harness.definitions import CAPABILITIES
    from harness.events import SessionLog
    from harness.kernel import Kernel
    from harness.manifest import discover
    from plugins.task import workload
    from profiles import base_profile

    binding = discover().task_bindings[TASK]
    catalogue = _load_attr(binding["catalogue"])
    oracles = _load_attr(binding["oracles"])
    # baseline arm: graph.skill keeps the base's rootless empty store (n4 ungoverned).
    plan = resolve_plan(base_profile(), patches=(Patch("probe_clear_build", override=(
        Mount("task.planner", binding["planner"]),
        Mount("policy.driver", binding["policy"]),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    brief = {"task": TASK, "catalogue": catalogue, "oracles": oracles}
    t0 = time.perf_counter()
    out = workload.run(brief, kernel, seed=seed,
                       max_replans=MAX_REPLANS, max_actuations=MAX_ACTUATIONS)
    dt = time.perf_counter() - t0
    if out["success"]:
        death = "none"
    else:
        fault = out["faults"][-1]
        death = fault.get("node") or fault["kind"]
    return {"seed": seed, "success": bool(out["success"]), "first_death": death,
            "replans": out["replans"], "actuations": out["actuations"],
            "seconds": round(dt, 3),
            "nodes": {nid: bool(n["success"]) for nid, n in out["nodes"].items()}}


def _seeds(spec: str) -> list[int]:
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":", 1))
        return list(range(lo, hi))
    return [int(x) for x in spec.split(",")]


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    successes = sum(r["success"] for r in rows)
    q_pre = sum(all(r["nodes"].get(k) for k in PRE_NODES) for r in rows)
    deaths = Counter(r["first_death"] for r in rows)
    # per-node success among episodes that reached (dispatched) the node
    per_node = {}
    for node in (*PRE_NODES, GOVERNED_NODE):
        reached = [r for r in rows if node in r["nodes"]]
        got = sum(r["nodes"][node] for r in reached)
        per_node[node] = {"reached": len(reached), "success": got,
                          "rate": round(got / len(reached), 4) if reached else None}
    failed = n - successes
    deaths_ungoverned = sum(v for k, v in deaths.items()
                            if k in (*PRE_NODES, "budget", "invalid_plan"))
    deaths_governed = deaths.get(GOVERNED_NODE, 0)
    chain_rate = successes / n if n else 0.0
    q_pre_rate = q_pre / n if n else 0.0

    # local-archive/docs/retired-from-public/long-horizon-design.md §4 abort / go-no-go
    base_degenerate = successes == 0 or successes == n            # task step 2 STOP
    c1_low_qpre = q_pre_rate < 0.30                               # §4.1
    c2_ceiling = chain_rate >= 0.90                               # §4.2
    c3_dies_ungoverned = failed > 0 and deaths_ungoverned > deaths_governed  # §4.3
    proceed = not (base_degenerate or c1_low_qpre or c2_ceiling or c3_dies_ungoverned)
    return {
        "task": TASK, "arm": "baseline", "n": n,
        "chain_success": successes, "chain_rate": round(chain_rate, 4),
        "q_pre": q_pre, "q_pre_rate": round(q_pre_rate, 4),
        "first_death_histogram": dict(deaths),
        "deaths_ungoverned": deaths_ungoverned, "deaths_governed": deaths_governed,
        "per_node": per_node,
        "seconds_total": round(sum(r["seconds"] for r in rows), 3),
        "seconds_per_episode": round(sum(r["seconds"] for r in rows) / n, 3) if n else 0.0,
        "go_no_go": {
            "base_degenerate_0_or_100": base_degenerate,
            "c1_q_pre_below_0.30": c1_low_qpre,
            "c2_chain_rate_at_or_above_0.90": c2_ceiling,
            "c3_dies_at_ungoverned_node": c3_dies_ungoverned,
            "proceed": proceed,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="48900:49050",
                    help='calibration block; "S:E" (half-open) | "S1,S2,.." | "S"')
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", type=Path, default=None,
                    help="write the full per-episode JSON here (dir auto-created)")
    args = ap.parse_args(argv)

    from harness.executor import LocalPoolExecutor

    seeds = _seeds(args.seeds)
    rows = LocalPoolExecutor().map(_probe_one, seeds, workers=args.workers)
    rows.sort(key=lambda r: r["seed"])
    summary = summarize(rows)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({**summary, "episodes": rows},
                                       indent=1, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
