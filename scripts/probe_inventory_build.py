#!/usr/bin/env python3
"""Calibration probe for the M6 ``inventory_build`` heterogeneous mission
(local-archive/docs/retired-from-public/m6-mission-design.md §4).

Runs the BASELINE (ungoverned) 11-node heterogeneous chain over the calibration
block and reports the go/no-go numbers the design's abort criteria read: chain
base rate, q_pre = P(all nodes before the governed ``build-stack`` succeed), a
per-node AND per-KIND first-death histogram (which node/kind kills the chain),
per-node success, replan statistics, and timing. Calibration blocks NEVER gate
(STATUS ledger); this only MEASURES.

Baseline arm only on purpose: governance only ever reaches ``build-stack`` (the
M5 stack-g1 + place-g2 mount), so the baseline arm fixes q_pre and the chain
base rate exactly for every arm. Sibling of ``scripts/probe_clear_build.py`` --
same parallel Pool pattern, adapted to the 11-node / 4-KIND graph: it threads the
card's PREDICATES onto the brief (kindful nodes need them) and buckets first-death
by node KIND (design §3b) so the go/no-go can tell a governable death from a
deterministic one.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/probe_inventory_build.py \
        --seeds 50000:50150 --out runs/scripted-calibration/inventory-build-cal/calibration.json
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

TASK = "inventory_build"
#: Nodes before the governed build-stack -- their joint success IS q_pre (the
#: chain reaching the governable node). Governance only reaches build-stack, so
#: the baseline arm fixes q_pre for every arm (M5 §4 lever, unchanged).
PRE_NODES = ("survey", "classify", "plan-order", "grasp-cube", "verify-grasp")
GOVERNED_NODE = "build-stack"
#: 11 nodes + replan headroom. Each dispatched node (any kind) counts one
#: actuation; 40 guarantees max_replans caps the loop before budget ever masks a
#: real node death. Smoke used replans=3 over this graph.
MAX_REPLANS, MAX_ACTUATIONS = 3, 40


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def _kind_map() -> dict[str, str]:
    """node id -> declared kind, read from the card's OWN plan (no hardcode --
    the smoke's InventoryKinds pattern)."""
    planner = _load_attr(discover_binding()["planner"])()
    return {n["id"]: n.get("kind", "manipulate")
            for n in planner.plan({"task": TASK})["nodes"]}


def discover_binding() -> dict:
    from harness.manifest import discover
    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/inventory_build installed?")
    return binding


def _probe_one(seed: int) -> dict:
    """One baseline (ungoverned) chain. Builds its own kernel so it is a clean
    Pool task -- no sim state crosses the fork."""
    from harness.config import Mount, Patch, resolve_plan
    from harness.definitions import CAPABILITIES
    from harness.events import SessionLog
    from harness.kernel import Kernel
    from plugins.task import workload
    from profiles import base_profile

    binding = discover_binding()
    catalogue = _load_attr(binding["catalogue"])
    oracles = _load_attr(binding["oracles"])
    predicates = _load_attr(binding["predicates"])  # kindful nodes need these
    # baseline arm: graph.skill keeps the base's rootless empty store (ungoverned).
    plan = resolve_plan(base_profile(), patches=(Patch("probe_inventory_build", override=(
        Mount("task.planner", binding["planner"]),
        Mount("policy.driver", binding["policy"]),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    brief = {"task": TASK, "catalogue": catalogue, "oracles": oracles,
             "predicates": predicates}
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


def summarize(rows: list[dict], kinds: dict[str, str]) -> dict:
    n = len(rows)
    successes = sum(r["success"] for r in rows)
    q_pre = sum(all(r["nodes"].get(k) for k in PRE_NODES) for r in rows)
    deaths = Counter(r["first_death"] for r in rows)
    # per-KIND first-death (design §3b): map the death node to its kind; a
    # non-node fault (budget/invalid_plan) buckets as itself.
    deaths_by_kind: Counter[str] = Counter()
    for node, cnt in deaths.items():
        deaths_by_kind[kinds.get(node, node)] += cnt
    # per-node success among episodes that reached (dispatched) the node
    per_node = {}
    for node in kinds:
        reached = [r for r in rows if node in r["nodes"]]
        got = sum(r["nodes"][node] for r in reached)
        per_node[node] = {"kind": kinds[node], "reached": len(reached),
                          "success": got,
                          "rate": round(got / len(reached), 4) if reached else None}
    failed = n - successes
    # ungoverned = every death that is NOT the governable build-stack node (an
    # ungoverned manipulate, or a deterministic perceive/decide/verify that
    # should never fail) -- design §3b c3.
    deaths_governed = deaths.get(GOVERNED_NODE, 0)
    deaths_ungoverned = failed - deaths_governed
    chain_rate = successes / n if n else 0.0
    q_pre_rate = q_pre / n if n else 0.0
    total_replans = sum(r["replans"] for r in rows)
    chains_with_replan = sum(r["replans"] > 0 for r in rows)

    # local-archive/docs/retired-from-public/m6-mission-design.md §4 abort / go-no-go (reuse M5 §4 gates)
    base_degenerate = successes == 0 or successes == n            # (1) 0%/100% STOP
    c2_ceiling = chain_rate >= 0.90                               # (2) null ceiling
    c1_low_qpre = q_pre_rate < 0.30                               # (3) q_pre lever
    c3_dies_ungoverned = failed > 0 and deaths_ungoverned > deaths_governed  # (4)
    proceed = not (base_degenerate or c1_low_qpre or c2_ceiling or c3_dies_ungoverned)
    return {
        "task": TASK, "arm": "baseline", "n": n,
        "chain_success": successes, "chain_rate": round(chain_rate, 4),
        "q_pre": q_pre, "q_pre_rate": round(q_pre_rate, 4),
        "first_death_histogram": dict(deaths),
        "first_death_by_kind": dict(deaths_by_kind),
        "deaths_ungoverned": deaths_ungoverned, "deaths_governed": deaths_governed,
        "per_node": per_node,
        "replans_total": total_replans, "chains_with_replan": chains_with_replan,
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
    ap.add_argument("--seeds", default="50000:50150",
                    help='calibration block; "S:E" (half-open) | "S1,S2,.." | "S"')
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--out", type=Path, default=None,
                    help="write the full per-episode JSON here (dir auto-created)")
    args = ap.parse_args(argv)

    from harness.executor import LocalPoolExecutor

    kinds = _kind_map()
    seeds = _seeds(args.seeds)
    rows = LocalPoolExecutor().map(_probe_one, seeds, workers=args.workers)
    rows.sort(key=lambda r: r["seed"])
    summary = summarize(rows, kinds)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({**summary, "episodes": rows},
                                       indent=1, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
