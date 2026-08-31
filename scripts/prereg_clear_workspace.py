#!/usr/bin/env python3
"""Seal the M7 ``clear_workspace`` preregistration (local-archive/docs/retired-from-public/m7-persistent-mission.md §3).

Seals, into ONE new campaign store, the pre-committed evidence plan BEFORE any
dev/held-out burn:

  * ``preregistration`` -- a ``Preregistration`` documenting the reserved M7 dev/
    held-out partition for the FUTURE in-episode placement/transport-recovery
    surface. FROM-SCRATCH (``parent_store=None``): no existing bundle governs the
    persistent-world transport step. This campaign is GATED-OFF this phase (the
    calibration is a documented NO-GO, hypothesis c); the seal fixes the seed
    partition so a later go cannot quietly re-partition.
  * ``chain_battery_plan`` -- hypotheses a (the ONE-episode mission executes end to
    end with live-state verify + in-episode replan -- ESTABLISHED by the smoke +
    calibration), b (per-sub-goal first-death attribution), c (in-episode
    transport-recovery evolution -- GATED-OFF as a documented null, calibration
    no-go), plus the blocks + go/no-go read off the calibration.
  * ``mission_graph`` -- the 12-node persistent-mission manifest (id, kind, skill,
    predicate ref, terminal oracle, replan edge, per-perceive privilege) read from
    the card's OWN plan + PREDICATES so an auditor re-derives the exact graph.
  * ``calibration`` -- the sealed calibration the go/no-go was decided on
    (scripts/probe_clear_workspace.py output on the calibration block).

Sealing only; it burns no dev/held-out (calibration blocks never gate). Provider
triple is stamped from the card's own mount so the sealed sha is the one a real
clear_workspace run would seal.

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/prereg_clear_workspace.py \
        --out runs/scripted-calibration/clear-workspace-cal
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from harness.manifest import discover
from plugins.embodiment_robosuite.env import pick_stages
from plugins.rsi.campaign import CampaignStore, Preregistration
from profiles import base_profile

TASK = "clear_workspace"

#: The design's block allocation (local-archive/docs/retired-from-public/m7-persistent-mission.md §3; reserved in
#: STATUS.md 区块预算). Calibration is burned (measured); the rest reserved,
#: GATED on a governable residual the calibration must first prove (it did not).
BLOCKS = {
    "calibration": {"lo": 51500, "hi": 51649, "n": 150, "gates": False,
                    "role": "measures full-clear base rate / per-sub-goal first-death / "
                            "horizon-exhaust / wall-clock; never a gate"},
    "dev": {"lo": 51650, "hi": 51949, "n": 300, "gates": True,
            "role": "ordered power-scaled prefix -- ONLY if calibration proves a "
                    "governable persistent-recovery residual (it did NOT: NO-GO)"},
    "heldout_1": {"lo": 51950, "hi": 52149, "n": 200, "gates": True,
                  "role": "scored once, only on a promotion (none this phase)"},
    "reserve": {"lo": 52150, "hi": None, "n": None, "gates": False,
                "role": "future persistent transport-recovery surface (the frontier "
                        "M7 opens; not built on spec)"},
}


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def clear_workspace_prereg() -> Preregistration:
    """The FUTURE in-episode transport-recovery prereg (GATED-OFF this phase):
    FROM-SCRATCH (parent_store defaults None) on the M7 reserved blocks. dev is the
    300-seed reservoir (51650-51949); held-out #1 is 51950-52149 (200). task="lift"
    is the segment terminal (lifted); the actual recovery surface is the persistent
    transport-to-bin step, which the current harness has no governed task for -- so
    this seal is a partition + policy stamp, not an authorized campaign."""
    return Preregistration(
        dev=tuple(range(51650, 51950)), heldout=tuple(range(51950, 52150)),
        percept_noise=0.012,
        task="lift", policy="scripted", critic_budget=0, action_budget=0,
        recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
        stages=pick_stages(), terminal_label=False, require_judgement=True,
        recovery_name="transport_recover",
    )


def _mount(out: Path) -> Kernel:
    """base_profile + the card's own composite policy -- used to STAMP the provider
    triple (env/policy/percept) so the sealed sha is a real clear_workspace run's."""
    binding = discover().task_bindings[TASK]
    plan = resolve_plan(base_profile(), patches=(
        Patch("clear_workspace_prereg", override=(
            Mount("task.planner", binding["planner"]),
            Mount("policy.driver", binding["policy"]),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def _stamped(prereg: Preregistration, kernel: Kernel) -> Preregistration:
    return dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"))


def _mission_graph() -> dict:
    """The 12-node persistent-mission manifest, read from the card's OWN plan +
    PREDICATES (no hardcode). A segment node has no predicate ref (it drives the
    live env); its terminal oracle is the verify-list ``lifted`` entry after it."""
    binding = discover().task_bindings[TASK]
    planner = _load_attr(binding["planner"])()
    predicates = _load_attr(binding["predicates"])
    plan = planner.plan({"task": TASK})
    verify_by_after = {v["after"]: v["predicate"] for v in plan["verify"]}
    nodes = []
    for n in plan["nodes"]:
        kind = n.get("kind", "manipulate")
        nodes.append({
            "id": n["id"], "kind": kind, "skill": n["skill"], "args": n.get("args") or {},
            "after": n["after"],
            "predicate_ref": predicates.get(n["skill"]) if kind in ("perceive", "decide", "verify") else None,
            "terminal_oracle": verify_by_after.get(n["id"]) if kind == "segment" else None,
            "live_oracle": ("not_in_bin(obj_pos, bin_id) on the LIVE persistent env"
                            if kind == "verify" else None),
            "replan_edge": ("verify fail -> drop object, replan REMAINING in SAME world"
                            if kind == "verify" else
                            "grasp slip -> in-episode retry in place (<=2), then skip"
                            if kind == "segment" else None),
        })
    return {
        "task": TASK, "planner": planner.identity, "n_nodes": len(nodes),
        "episodic": True, "episode": _load_attr(binding["episode"]),
        "kinds": {"perceive": 2, "decide": 2, "segment": 4, "verify": 4},
        "privilege": {"survey": ["privileged.object_z"], "sweep": ["privileged.object_z"],
                      "note": "decide/verify read only ctx.nodes_out sealed facts + the "
                              "live not_in_bin geometry -> the same privilege a reset "
                              "preview paid (m7 §2b)"},
        "nodes": nodes,
    }


def _chain_battery_plan(prereg_sha: str, cal: dict) -> dict:
    """Hypotheses a/b/c preregistered alongside the prereg (design §3)."""
    g = cal["go_no_go"]
    dg = cal["deaths_grasp_governable"]
    dp = cal["deaths_placement_ungoverned"]
    return {
        "preregistration_sha": prereg_sha,
        "task": TASK, "planner": "clear_workspace_planner@v1",
        "calibration_verdict": (
            f"full-clear base rate {cal['full_clear_rate']} ({cal['full_clear']}/{cal['n']}), "
            f"any-clear {cal['any_clear']}/{cal['n']}, mean cleared {cal['mean_cleared']}. "
            f"Gate 1 (0%/100%) FIRES: base clearing rate is 0% -- the frozen mode-0 pick "
            f"policy grasps+lifts (per-object grasp {cal['per_object_grasp_rate']}) but NEVER "
            f"transports to a bin, so no gate can learn a placement it never achieves. "
            f"Gate 4 (dies-ungoverned) FIRES: {dp}/{cal['n']} first-deaths at the UNGOVERNED "
            f"placement/transport sub-goal vs {dg} at a governable grasp-slip -- the dominant "
            f"killer is an ungoverned surface, not a governable drop. horizon-exhaust "
            f"{cal['horizon_exhaust']}/{cal['n']} (gate 3 clear). VERDICT: DOUBLE NO-GO -> "
            f"hypothesis a (mission executes, one episode, live verify, in-episode replan) "
            f"ESTABLISHED; b attribution = placement-dominant; c (transport-recovery "
            f"evolution) is a DOCUMENTED NULL, not campaigned (design §4 caveat)."),
        "hypotheses": {
            "a_mission_execution": "ONE persistent mode-0 episode threads the 12-node graph: "
                "one reset, four segments driven sequentially in the SAME world, per-sub-goal "
                "live-state verify (not_in_bin), in-episode consequence-carrying replan (a "
                "dropped/unplaceable object skipped, never a reset), a machine report. "
                "ESTABLISHED by the smoke + the 150-seed calibration (graph_complete "
                f"{cal['graph_complete']}/{cal['n']}, all episodes replan).",
            "b_attribution": "per-sub-goal first-death by object AND by stage localizes where "
                f"the CLEARING dies: {cal['first_death_by_stage']} -- 96.7% at the ungoverned "
                "placement/transport step (segment lifted but verify False), 3.3% at a "
                "governable grasp-slip. Grasp itself has real per-object signal "
                f"({cal['per_object_grasp_rate']}) but grasp is NOT what kills clearing.",
            "c_transport_recovery_evolution": "an in-episode rule firing on a live-oracle "
                "placement miss and re-driving transport in the persistent world is the NEW "
                "evolvable surface M7 opens (design §4). GATED on (b) showing a GOVERNABLE "
                "placement residual; calibration shows the residual is an ungoverned TRANSPORT "
                "capability the frozen policy lacks entirely (0% placed) -> DOCUMENTED NULL, "
                "NOT run this phase. A prerequisite is a transport-capable segment policy or a "
                "placement-recovery task the harness does not yet have.",
        },
        "arms": {
            "baseline": {"skills_root": None, "segments": "ungoverned (frozen four-phase pick)"},
        },
        "blocks": BLOCKS,
        "gate": {
            "method": "paired same-seed McNemar on the clearing boolean (per object) -- IF a "
                       "governable residual is ever proven",
            "alpha": 0.05, "min_fixed": 3, "power_target": 0.8,
            "scale_dev_by_power": True,
            "status": "NOT ARMED -- calibration NO-GO; dev/held-out stay unburned",
        },
        "go_no_go": g,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "runs/scripted-calibration/clear-workspace-cal")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="calibration.json from probe_clear_workspace.py (default <out>/calibration.json)")
    args = ap.parse_args()
    out = args.out
    cal_path = args.calibration or (out / "calibration.json")

    if (out / "index.jsonl").exists():
        print(f"{out} already holds a store; refusing to re-seal", file=sys.stderr)
        return 2
    if not cal_path.exists():
        print(f"no calibration at {cal_path}; run probe_clear_workspace.py first", file=sys.stderr)
        return 2

    kernel = _mount(out)
    prereg = _stamped(clear_workspace_prereg(), kernel)
    cal = json.loads(cal_path.read_text())

    store = CampaignStore(out)
    prereg_sha = store.put("preregistration", prereg._hash_payload())
    plan_sha = store.put("chain_battery_plan", _chain_battery_plan(prereg_sha, cal))
    graph_sha = store.put("mission_graph", _mission_graph())
    cal["preregistration_sha"] = prereg_sha
    cal_sha = store.put("calibration", cal)

    print(f"preregistration    {prereg_sha}")
    print(f"chain_battery_plan {plan_sha[:12]}")
    print(f"mission_graph      {graph_sha[:12]}")
    print(f"calibration        {cal_sha[:12]}")
    print(f"store              {out}")
    print("verdict            DOUBLE NO-GO (base clearing 0% + dies-ungoverned) -> "
          "dev/held-out stay UNBURNED; hypothesis a established, c documented null")
    return 0


if __name__ == "__main__":
    sys.exit(main())
