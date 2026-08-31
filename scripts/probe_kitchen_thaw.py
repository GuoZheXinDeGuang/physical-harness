#!/usr/bin/env python3
"""Calibration probe for the ``kitchen_thaw`` PERSISTENT mission (RSI campaign
step 1 on the RoboCasa embodiment; local-archive/docs/retired-from-public/m7-persistent-mission.md §3 discipline).

Runs the BASELINE (ungoverned) 15-node persistent episode over the calibration
block and reports the numbers the §3 go/no-go criteria read -- it never gates.
Sibling of ``scripts/probe_clear_workspace.py`` (same Pool pattern, same brief
assembly), with the two kitchen differences:

* **second sim**: the binding names ``env``/``percept`` refs (robocasa card is
  ``enabled=false``, kept out of the base fold), so the probe overrides
  ``embodiment.env`` / ``percept.model`` exactly as ``harness_runtime._mount_plan``
  does. RoboCasa determinism holds only for a fresh env's FIRST reset -- satisfied
  here because every episode is its own ``open_episode`` (one make/reset/close).
* **linear chain**: one meat, one appliance path. The scientific base rate is
  ``report.decision.thawed`` (microwave on, the mission headline); first-death is
  the first canonically-ordered node whose FINAL outcome is False, and the
  mechanism falls out of WHICH node died (segment stall vs its verify's live-state
  drop): grasp cap-burn = enclosure failure vs grasped-then-lost; nav-micro stall
  (seed-5-type door blockage / no path) vs at-micro carry drop; place stall vs
  cavity drop; close/press = the phase-3 door-latch arc-sweep dead end. The scene
  fingerprint (layout/style, sealed by survey) rides every row so "which kitchens
  kill which node" is computable.

Usage (robocasa venv, cwd = this repo, EGL headless):

    cd /home/yusenzlabpc/Desktop/physical-harness && MUJOCO_GL=egl PYTHONPATH=. \
      /home/yusenzlabpc/Desktop/sims/robocasa-venv/bin/python \
      scripts/probe_kitchen_thaw.py --seeds 52150:52300 --workers 6 \
      --out runs/scripted-calibration/kitchen-thaw-cal/calibration.json
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

TASK = "kitchen_thaw"

#: Canonical node order (the planner's fixed linear chain).
ORDER = ("survey", "plan",
         "nav-fridge", "at-fridge", "grasp", "grasped",
         "nav-micro", "at-micro", "place", "inside",
         "close", "closed", "press", "on", "report")

#: node -> (sub-goal leg, death mechanism). Segment vs verify death IS the
#: mechanism split (carry-probe.md): a segment burns its cap without reaching its
#: own done (stall / enclosure failure / unreachable latch), its verify fails when
#: the segment CLAIMED done but the live world disagrees (drop).
MECH = {
    "survey": ("survey", "survey_read"), "plan": ("survey", "plan_gate"),
    "nav-fridge": ("nav-fridge", "nav_stall"), "at-fridge": ("nav-fridge", "fridge_not_open"),
    "grasp": ("grasp", "grasp_enclose_fail"), "grasped": ("grasp", "grasp_verify_drop"),
    "nav-micro": ("nav-micro", "nav_stall_blocked"), "at-micro": ("nav-micro", "carry_drop"),
    "place": ("place", "place_stall"), "inside": ("place", "cavity_drop"),
    "close": ("close", "door_latch_arc"), "closed": ("close", "door_latch_arc"),
    "press": ("press", "press_latch"), "on": ("press", "press_latch"),
    "report": ("report", "report_read"),
}

#: The runtime operating point: max_replans=3 is harness_runtime's default and the
#: planner's designed in-episode retry budget ("~2 retries"). NOT clear_workspace's
#: 15 -- on this LINEAR chain a replan re-drives the SAME dead segment, so a big
#: replan budget just burns the horizon on a dead sub-goal (measured: 15 replans
#: on a grasp-dead seed = guaranteed horizon-exhaust, corrupting gate 3's meaning).
#: actuations stay generous so budget never masks a sub-goal death.
MAX_REPLANS, MAX_ACTUATIONS = 3, 40


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def discover_binding() -> dict:
    from harness.manifest import discover
    binding = discover().task_bindings.get(TASK)
    if binding is None:
        raise SystemExit(f"no task binding for {TASK!r}; is plugins/mission_kitchen_thaw installed?")
    return binding


def _probe_one(seed: int) -> dict:
    """One baseline (ungoverned) persistent kitchen episode; a clean Pool task
    (fresh kernel, fresh env, robocasa imported lazily in the child)."""
    from harness.config import Mount, Patch, resolve_plan
    from harness.definitions import CAPABILITIES
    from harness.events import SessionLog
    from harness.kernel import Kernel
    from plugins.task import workload
    from profiles import base_profile

    binding = discover_binding()
    override = [Mount("task.planner", binding["planner"]),
                Mount("policy.driver", binding["policy"])]
    # the second-sim mounts, exactly as harness_runtime._mount_plan overlays them
    for cap, key in (("embodiment.env", "env"), ("percept.model", "percept")):
        if key in binding:
            override.append(Mount(cap, binding[key]))
    plan = resolve_plan(base_profile(), patches=(
        Patch("probe_kitchen_thaw", override=tuple(override)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    brief = {"task": TASK,
             "catalogue": _load_attr(binding["catalogue"]),
             "oracles": _load_attr(binding["oracles"]),
             "predicates": _load_attr(binding["predicates"]),
             "episodic": True, "episode": _load_attr(binding["episode"]),
             "segment_specs": _load_attr(binding["segment_specs"])}
    horizon = int(dict(brief["episode"]).get("horizon", 2000))

    t0 = time.perf_counter()
    out = workload.run(brief, kernel, seed=seed,
                       max_replans=MAX_REPLANS, max_actuations=MAX_ACTUATIONS)
    dt = time.perf_counter() - t0

    nodes = out["nodes"]
    node_ok = {n: bool((nodes.get(n) or {}).get("success")) for n in ORDER}
    reached = {n: n in nodes for n in ORDER}
    report = (nodes.get("report") or {}).get("decision") or {}
    scene = ((nodes.get("survey") or {}).get("facts") or {}).get("scene") or {}
    # first death: first canonically-ordered node whose FINAL outcome is not a pass
    # (never reached counts as dead there too -- the chain aborted before it).
    first_death = next((n for n in ORDER if not node_ok[n]), "none")
    leg, mech = MECH.get(first_death, ("none", "none"))
    depth = ORDER.index(first_death) if first_death != "none" else len(ORDER)
    spans = [g["exited_env_step"] for n in nodes.values()
             if (g := n.get("governance") or {}) and "exited_env_step" in g]
    total_steps = max(spans) if spans else 0
    return {"seed": seed,
            "thawed": bool(report.get("thawed")),
            "live": report.get("live"),
            "layout_id": scene.get("layout_id"), "style_id": scene.get("style_id"),
            "lang": scene.get("lang"),
            "node_ok": node_ok, "reached": reached,
            "first_death": first_death, "death_leg": leg, "death_mech": mech,
            "verified_depth": depth,
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
    thawed = sum(r["thawed"] for r in rows)
    graph_complete = sum(r["graph_complete"] for r in rows)
    deaths_node = Counter(r["first_death"] for r in rows)
    deaths_leg = Counter(r["death_leg"] for r in rows)
    deaths_mech = Counter(r["death_mech"] for r in rows)
    # node pass rate among episodes that reached it (per-node outcome table)
    node_pass = {nd: {"reached": sum(r["reached"][nd] for r in rows),
                      "passed": sum(r["node_ok"][nd] for r in rows)} for nd in ORDER}
    # scene-difficulty axis: layout -> first-death histogram (style kept per-row)
    by_layout: dict = {}
    for r in rows:
        by_layout.setdefault(str(r["layout_id"]), Counter())[r["first_death"]] += 1
    horizon_exhaust = sum(r["horizon_exhaust"] for r in rows)
    secs = [r["seconds"] for r in rows]
    failures = n - thawed
    # governable-drop-shaped deaths (a live-oracle drop a same-world recovery could
    # target) vs capability-missing deaths (stall / latch the frozen driver cannot
    # do at all -- carry-probe.md). Mechanical counts only; the go/no-go DECISION
    # is the main session's, not this probe's.
    drop_shaped = sum(deaths_mech.get(m, 0) for m in
                      ("grasp_verify_drop", "carry_drop", "cavity_drop"))
    capability = sum(deaths_mech.get(m, 0) for m in
                     ("nav_stall", "nav_stall_blocked", "fridge_not_open",
                      "grasp_enclose_fail", "place_stall", "door_latch_arc",
                      "press_latch"))
    return {
        "task": TASK, "arm": "baseline", "n": n,
        "thawed": thawed, "thawed_rate": round(thawed / n, 4) if n else None,
        "graph_complete": graph_complete,
        "first_death_by_node": dict(deaths_node),
        "first_death_by_leg": dict(deaths_leg),
        "first_death_by_mech": dict(deaths_mech),
        "deaths_drop_shaped": drop_shaped,
        "deaths_capability_missing": capability,
        "node_outcomes": node_pass,
        "first_death_by_layout": {k: dict(v) for k, v in sorted(by_layout.items())},
        "mean_verified_depth": round(sum(r["verified_depth"] for r in rows) / n, 3) if n else None,
        "horizon_exhaust": horizon_exhaust,
        "replans_total": sum(r["replans"] for r in rows),
        "episodes_with_replan": sum(r["replans"] > 0 for r in rows),
        "seconds_total": round(sum(secs), 3),
        "seconds_per_episode": round(sum(secs) / n, 3) if n else None,
        "seconds_max": max(secs) if secs else None,
        # §3 gate READOUTS (mechanical facts; decision deferred to the main session)
        "gate_readouts": {
            "g1_base_degenerate_0_or_100": thawed == 0 or thawed == n,
            "g2_base_at_or_above_0.90": n > 0 and thawed / n >= 0.90,
            "g3_horizon_exhaust_dominant": horizon_exhaust > failures - horizon_exhaust and horizon_exhaust > 0,
            "g4_counts": {"drop_shaped": drop_shaped, "capability_missing": capability},
            "g5_wall_clock_seconds_per_episode": round(sum(secs) / n, 3) if n else None,
            "decision": "DEFERRED -- calibration never gates; main session decides",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", default="52150:52300",
                    help='calibration block; "S:E" (half-open) | "S1,S2,.." | "S"')
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", type=Path, default=None,
                    help="write the full per-episode JSON here (dir auto-created)")
    args = ap.parse_args(argv)

    seeds = _seeds(args.seeds)
    # live progress heartbeat (runs/<store>/progress.json) beside --out: the
    # console's 演进 panel reads it while the battery runs. Best-effort by
    # contract -- tracker/write_progress never raise into the battery.
    tick = None
    if args.out is not None:
        from scripts.campaign_progress import tracker
        tick = tracker(args.out.parent, len(seeds), label=f"{TASK} {args.seeds}")
    if args.workers <= 1 or len(seeds) == 1:
        rows = []
        for s in seeds:
            rows.append(_probe_one(s))
            if tick is not None:
                tick(rows[-1])
    else:
        from harness.executor import LocalPoolExecutor
        rows = LocalPoolExecutor().map(_probe_one, seeds, workers=args.workers,
                                       on_result=tick)
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
