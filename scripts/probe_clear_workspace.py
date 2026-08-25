#!/usr/bin/env python3
"""Calibration probe for the M7 ``clear_workspace`` PERSISTENT mission
(docs/m7-persistent-mission.md §3).

Runs the BASELINE (ungoverned) 12-node persistent episode over the calibration
block and reports the go/no-go numbers the design's abort criteria read. UNLIKE
M6's chain (where ``out['success']`` IS the scientific metric), a clear_workspace
run ALWAYS completes: a failed placement drops the object and the mission still
seals a faithful zero-placed report (planner.py ``_report`` honest-null). So the
scientific base rate is NOT ``out['success']`` -- it is how many of the four
objects actually ended up in their bins on the LIVE oracle (``report.cleared``),
and the per-sub-goal first-death is WHERE the clearing dies: a governable
grasp-slip (``clear-X`` segment fault) vs an ungoverned placement/transport
failure (``clear-X`` lifted but ``verify-X`` False -- the frozen mode-0 pick
policy never carried it to the bin).

Sibling of ``scripts/probe_inventory_build.py`` -- same parallel Pool pattern,
same brief assembly as ``scripts/clear_workspace_smoke.py`` (episodic=True +
episode + segment_specs), adapted metric. Calibration blocks NEVER gate (STATUS
ledger); this only MEASURES: base clearing rate, per-object clear/grasp rate,
per-sub-goal first-death histogram (by object AND by stage), horizon-exhaust
rate, replan stats, wall-clock.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/probe_clear_workspace.py \
        --seeds 51500:51650 --out runs/clear-workspace-cal/calibration.json
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

TASK = "clear_workspace"
#: The four mode-0 objects in canonical execution order (planner emits this).
OBJECTS = ("milk", "bread", "cereal", "can")
#: 12 nodes + generous replan headroom: 4 segments x up to 2 grasp retries + 4
#: verify skips = worst case ~12 faults; 15 replans / 40 actuations never lets
#: the budget mask a real sub-goal death.
MAX_REPLANS, MAX_ACTUATIONS = 15, 40


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def discover_binding() -> dict:
    from harness.manifest import discover
    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/clear_workspace installed?")
    return binding


def _probe_one(seed: int) -> dict:
    """One baseline (ungoverned) persistent episode. Builds its own kernel so it
    is a clean Pool task -- no sim state crosses the fork. Mirrors the smoke's
    mount + brief exactly (base_profile + card planner/policy, episodic brief)."""
    from harness.config import Mount, Patch, resolve_plan
    from harness.definitions import CAPABILITIES
    from harness.events import SessionLog
    from harness.kernel import Kernel
    from plugins.task import workload
    from profiles import base_profile

    binding = discover_binding()
    # baseline arm: graph.skill keeps the base's rootless empty store (ungoverned).
    plan = resolve_plan(base_profile(), patches=(Patch("probe_clear_workspace", override=(
        Mount("task.planner", binding["planner"]),
        Mount("policy.driver", binding["policy"]),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    brief = {"task": TASK,
             "catalogue": _load_attr(binding["catalogue"]),
             "oracles": _load_attr(binding["oracles"]),
             "predicates": _load_attr(binding["predicates"]),
             "episodic": True, "episode": _load_attr(binding["episode"]),
             "segment_specs": _load_attr(binding["segment_specs"])}
    horizon = int(dict(brief["episode"]).get("horizon", 1800))

    t0 = time.perf_counter()
    out = workload.run(brief, kernel, seed=seed,
                       max_replans=MAX_REPLANS, max_actuations=MAX_ACTUATIONS)
    dt = time.perf_counter() - t0

    nodes = out["nodes"]
    report = (nodes.get("report") or {}).get("decision") or {}
    placed = set(report.get("placed") or [])
    cleared = int(report.get("cleared") or 0)
    # per-object: did clear-X grasp+lift (segment success) and did verify-X pass?
    grasp = {o: bool((nodes.get(f"clear-{o}") or {}).get("success")) for o in OBJECTS}
    verify = {o: bool((nodes.get(f"verify-{o}") or {}).get("success")) for o in OBJECTS}
    # first sub-goal death: first object (execution order) that did NOT land in bin.
    # Attribute the killer: segment grasp-slip (governable, grasp-cube family) vs
    # placement/transport (clear-X lifted but verify-X False -- ungoverned surface).
    first_death, first_stage = "none", "none"
    for o in OBJECTS:
        if o not in placed:
            first_death = o
            first_stage = "grasp" if not grasp[o] else "placement"
            break
    # cursor high-water off the shared step spans (governance seals entered/exited).
    spans = [g["exited_env_step"] for n in nodes.values()
             if (g := n.get("governance") or {}) and "exited_env_step" in g]
    total_steps = max(spans) if spans else 0
    return {"seed": seed, "cleared": cleared, "placed": sorted(placed),
            "grasp": grasp, "verify": verify,
            "first_death": first_death, "first_stage": first_stage,
            "total_env_steps": total_steps,
            "horizon_exhaust": total_steps >= horizon,
            "graph_complete": bool(out["success"]),
            "replans": out["replans"], "actuations": out["actuations"],
            "seconds": round(dt, 3)}


def _seeds(spec: str) -> list[int]:
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":", 1))
        return list(range(lo, hi))
    return [int(x) for x in spec.split(",")]


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    full_clear = sum(r["cleared"] == len(OBJECTS) for r in rows)      # mission base rate
    any_clear = sum(r["cleared"] > 0 for r in rows)
    total_cleared = sum(r["cleared"] for r in rows)
    # per-object clear (in bin) and grasp (lifted) rates across all episodes
    obj_clear = {o: sum(o in r["placed"] for r in rows) for o in OBJECTS}
    obj_grasp = {o: sum(r["grasp"][o] for r in rows) for o in OBJECTS}
    deaths = Counter(r["first_death"] for r in rows)                  # by object
    deaths_by_stage = Counter(r["first_stage"] for r in rows)         # none/grasp/placement
    horizon_exhaust = sum(r["horizon_exhaust"] for r in rows)
    total_replans = sum(r["replans"] for r in rows)
    episodes_with_replan = sum(r["replans"] > 0 for r in rows)
    graph_complete = sum(r["graph_complete"] for r in rows)

    full_rate = full_clear / n if n else 0.0
    mean_cleared = total_cleared / n if n else 0.0
    deaths_grasp = deaths_by_stage.get("grasp", 0)                    # governable residual
    deaths_placement = deaths_by_stage.get("placement", 0)           # ungoverned residual

    # docs/m7-persistent-mission.md §3 go/no-go (reuse M6 §4 gates on the CLEARING rate)
    base_degenerate = full_clear == 0 or full_clear == n             # (1) 0%/100% STOP
    c2_ceiling = full_rate >= 0.90                                    # (2) null ceiling
    horizon_dominant = horizon_exhaust > (n - graph_complete) and horizon_exhaust > 0  # (3)
    c4_dies_ungoverned = (n - full_clear) > 0 and deaths_placement > deaths_grasp       # (4)
    proceed = not (base_degenerate or c2_ceiling or horizon_dominant or c4_dies_ungoverned)
    return {
        "task": TASK, "arm": "baseline", "n": n,
        "full_clear": full_clear, "full_clear_rate": round(full_rate, 4),
        "any_clear": any_clear, "mean_cleared": round(mean_cleared, 4),
        "objects": len(OBJECTS),
        "per_object_clear": obj_clear,
        "per_object_clear_rate": {o: round(c / n, 4) if n else None for o, c in obj_clear.items()},
        "per_object_grasp": obj_grasp,
        "per_object_grasp_rate": {o: round(c / n, 4) if n else None for o, c in obj_grasp.items()},
        "first_death_by_object": dict(deaths),
        "first_death_by_stage": dict(deaths_by_stage),
        "deaths_grasp_governable": deaths_grasp,
        "deaths_placement_ungoverned": deaths_placement,
        "horizon_exhaust": horizon_exhaust,
        "graph_complete": graph_complete,
        "replans_total": total_replans, "episodes_with_replan": episodes_with_replan,
        "seconds_total": round(sum(r["seconds"] for r in rows), 3),
        "seconds_per_episode": round(sum(r["seconds"] for r in rows) / n, 3) if n else 0.0,
        "go_no_go": {
            "base_degenerate_0_or_100": base_degenerate,
            "c2_full_clear_at_or_above_0.90": c2_ceiling,
            "horizon_exhaust_dominant": horizon_dominant,
            "c4_dies_at_ungoverned_placement": c4_dies_ungoverned,
            "proceed": proceed,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="51500:51650",
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
