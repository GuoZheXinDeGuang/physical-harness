#!/usr/bin/env python3
"""Seal the ``kitchen_thaw`` calibration preregistration (RSI on RoboCasa, step 1).

Sibling of ``scripts/prereg_clear_workspace.py`` with ONE deliberate ordering
difference: this store is sealed BEFORE the calibration block burns (m7 §3
"Prereg BEFORE this burn"), so the seal carries the partition + hypotheses +
mission graph + provider triple, and the calibration record is APPENDED to the
same store afterwards (``--seal-calibration``; CampaignStore is append-only).

  * ``preregistration`` -- the frozen seed partition (calibration 52150-52299,
    dev reservoir 52300-52599, held-out #1 52600-52799) + the kitchen operating
    point, provider triple stamped from the card's own binding (second sim:
    env/percept refs included). FROM-SCRATCH (parent_store=None): no bundle
    governs any kitchen segment. Sealing fixes the partition; it authorizes NO
    campaign -- dev/held-out burn only on a main-session go.
  * ``chain_battery_plan`` -- hypotheses a (baseline thaw rate on the ONE
    persistent 15-node kitchen episode), b (per-node first-death attribution:
    node x mechanism x scene layout/style), c (in-episode recovery evolution,
    GATED on b proving a governable residual). Verdict field stays PENDING at
    seal time; the go/no-go decision is the main session's.
  * ``mission_graph`` -- the 15-node manifest read from the card's OWN plan +
    PREDICATES (no hardcode).
  * ``calibration`` -- appended after the probe (scripts/probe_kitchen_thaw.py)
    finishes, stamped with the prereg sha.

    cd $REPO && PYTHONPATH=. .venv/bin/python scripts/prereg_kitchen_thaw.py \
        --out runs/kitchen-thaw-cal                 # pre-burn seal
    ... run probe_kitchen_thaw.py --out runs/kitchen-thaw-cal/calibration.json ...
    PYTHONPATH=. .venv/bin/python scripts/prereg_kitchen_thaw.py \
        --out runs/kitchen-thaw-cal --seal-calibration   # post-burn append
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
from plugins.rsi.campaign import CampaignStore, Preregistration
from profiles import base_profile

TASK = "kitchen_thaw"

#: Frontier alloc (scripts/alloc_seeds.py 150 --floor 51500 -> 52150; M7 reserved
#: through 52149). Reserved in STATUS.md 区块预算 the same commit as this seal.
BLOCKS = {
    "calibration": {"lo": 52150, "hi": 52299, "n": 150, "gates": False,
                    "role": "measures baseline thaw rate / per-node first-death "
                            "(node x mechanism x scene) / horizon-exhaust / "
                            "wall-clock; never a gate"},
    "dev": {"lo": 52300, "hi": 52599, "n": 300, "gates": True,
            "role": "ordered power-scaled prefix -- ONLY on a main-session go "
                    "after the calibration read"},
    "heldout_1": {"lo": 52600, "hi": 52799, "n": 200, "gates": True,
                  "role": "scored once, only on a promotion"},
    "reserve": {"lo": 52800, "hi": None, "n": None, "gates": False,
                "role": "future kitchen recovery surfaces (grasp-enclosure / "
                        "cavity-entry / door-arc -- carry-probe.md residual order)"},
}


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def kitchen_thaw_prereg() -> Preregistration:
    """The frozen partition + kitchen operating point. FROM-SCRATCH: no governed
    bundle exists for any kitchen segment (the six stage drivers run ungoverned).
    task/policy name the world this partition is for; no campaign is authorized
    until the main session reads the calibration."""
    return Preregistration(
        dev=tuple(range(52300, 52600)), heldout=tuple(range(52600, 52800)),
        percept_noise=0.012,
        task=TASK, policy="scripted", critic_budget=0, action_budget=0,
        recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
        terminal_label=False, require_judgement=True,
        recovery_name="kitchen_recover",
    )


def _mount(out: Path) -> Kernel:
    """base_profile + the card's planner/policy AND its second-sim env/percept
    refs (harness_runtime._mount_plan's exact overlay) -- so the stamped provider
    triple is the one a real kitchen_thaw run seals."""
    binding = discover().task_bindings[TASK]
    override = [Mount("task.planner", binding["planner"]),
                Mount("policy.driver", binding["policy"])]
    for cap, key in (("embodiment.env", "env"), ("percept.model", "percept")):
        if key in binding:
            override.append(Mount(cap, binding[key]))
    plan = resolve_plan(base_profile(), patches=(
        Patch("kitchen_thaw_prereg", override=tuple(override)),))
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
    """The 15-node persistent-mission manifest, read from the card's OWN plan +
    PREDICATES. A segment node's terminal oracle is its own stage driver's done()
    (kitchen_driver segment_success); every verify reads a robocasa live-state
    primitive on the persistent world."""
    binding = discover().task_bindings[TASK]
    planner = _load_attr(binding["planner"])()
    predicates = _load_attr(binding["predicates"])
    plan = planner.plan({"task": TASK})
    from collections import Counter
    kinds = Counter(n.get("kind", "manipulate") for n in plan["nodes"])
    nodes = []
    for n in plan["nodes"]:
        kind = n.get("kind", "manipulate")
        nodes.append({
            "id": n["id"], "kind": kind, "skill": n["skill"],
            "args": n.get("args") or {}, "after": n["after"],
            "predicate_ref": predicates.get(n["skill"])
                if kind in ("perceive", "decide", "verify") else None,
            "terminal_oracle": ("stage driver done() on the live env"
                                if kind == "segment" else None),
            "live_oracle": ("robocasa live-state primitive on the persistent env"
                            if kind == "verify" else None),
            "replan_edge": ("verify fail -> re-drive the SAME segment in the SAME "
                            "world (linear chain), bounded by max_replans=3"
                            if kind in ("segment", "verify") else None),
        })
    return {
        "task": TASK, "planner": planner.identity, "n_nodes": len(nodes),
        "episodic": True, "episode": _load_attr(binding["episode"]),
        "kinds": dict(kinds),
        "privilege": {"survey": ["privileged.object_z"],
                      "note": "verifies wrap robocasa's free oracle on the live "
                              "env; survey seals the get_ep_meta scene fingerprint"},
        "nodes": nodes,
    }


def _chain_battery_plan(prereg_sha: str) -> dict:
    """Hypotheses a/b/c, sealed BEFORE the calibration burn (verdict PENDING)."""
    return {
        "preregistration_sha": prereg_sha,
        "task": TASK, "planner": "kitchen_thaw_planner@v1",
        "calibration_verdict": (
            "PENDING -- sealed BEFORE the 52150-52299 burn. The calibration record "
            "is appended to this store after the probe; the go/no-go DECISION is "
            "the main session's (this step never gates, never burns dev/held-out)."),
        "hypotheses": {
            "a_baseline_rate": "the frozen kitchen driver chain (nav-fridge -> grasp "
                "-> loaded nav-micro -> place -> close -> press) has a measurable "
                "baseline thaw rate on the ONE persistent MicrowaveThawingFridge "
                "episode; 0% is a valid result (carry-probe.md predicts grasp "
                "enclosure + door-arc latch dominate).",
            "b_attribution": "per-node first-death attribution is layered: WHICH node "
                "(15-node canonical order) x WHAT mechanism (segment cap-burn = "
                "stall/enclosure/latch vs verify fail = live-state drop) x WHICH "
                "kitchen (get_ep_meta layout/style) -- the first belief-space "
                "difficulty axis on the 60-layout scene randomization.",
            "c_recovery_evolution": "an in-episode rule firing on a live-oracle miss "
                "and re-driving in the persistent kitchen is the evolvable surface; "
                "GATED on (b) proving a governable residual (a drop-shaped death a "
                "same-world re-drive can fix, not a capability the frozen driver "
                "lacks entirely). Not armed by this seal.",
        },
        "arms": {"baseline": {"skills_root": None,
                              "segments": "ungoverned (frozen stage drivers, "
                                          "carry-probe operating point)"}},
        "blocks": BLOCKS,
        "gate": {
            "method": "paired same-seed McNemar on the mission boolean -- IF a "
                       "governable residual is proven and the main session goes",
            "alpha": 0.05, "min_fixed": 3, "power_target": 0.8,
            "scale_dev_by_power": True,
            "status": "NOT ARMED -- pre-burn seal; decision deferred",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "runs/kitchen-thaw-cal")
    ap.add_argument("--seal-calibration", action="store_true",
                    help="append a calibration record to the already-sealed store")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="the probe JSON to seal (default <out>/calibration.json). "
                         "A RE-calibration of the same block is legitimate -- the "
                         "block never gates, so it stays re-measurable -- and lands "
                         "under kind calibration_r2/r3/... beside the first, never "
                         "over it (the store is append-only).")
    args = ap.parse_args()
    out = args.out
    store_exists = (out / "index.jsonl").exists()

    if args.seal_calibration:
        if not store_exists:
            print(f"{out} holds no store; seal the prereg first", file=sys.stderr)
            return 2
        index = [json.loads(l) for l in (out / "index.jsonl").read_text().splitlines()]
        n = sum(e["kind"].startswith("calibration") for e in index)
        kind = "calibration" if n == 0 else f"calibration_r{n + 1}"
        cal_path = args.calibration or (out / "calibration.json")
        if not cal_path.exists():
            print(f"no {cal_path}; run probe_kitchen_thaw.py first", file=sys.stderr)
            return 2
        store = CampaignStore(out)
        cal = json.loads(cal_path.read_text())
        cal["preregistration_sha"] = next(
            e["sha"] for e in index if e["kind"] == "preregistration")
        cal_sha = store.put(kind, cal)
        print(f"{kind:18s} {cal_sha[:12]}")
        return 0

    if store_exists:
        print(f"{out} already holds a store; refusing to re-seal", file=sys.stderr)
        return 2

    kernel = _mount(out)
    prereg = _stamped(kitchen_thaw_prereg(), kernel)
    store = CampaignStore(out)
    prereg_sha = store.put("preregistration", prereg._hash_payload())
    plan_sha = store.put("chain_battery_plan", _chain_battery_plan(prereg_sha))
    graph_sha = store.put("mission_graph", _mission_graph())
    print(f"preregistration    {prereg_sha}")
    print(f"chain_battery_plan {plan_sha[:12]}")
    print(f"mission_graph      {graph_sha[:12]}")
    print(f"store              {out}")
    print("sealed PRE-BURN: calibration block 52150-52299 may now burn; "
          "dev/held-out stay UNBURNED pending the main session's read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
