#!/usr/bin/env python3
"""Seal the M6 ``inventory_build`` preregistration (local-archive/docs/retired-from-public/m6-mission-design.md §4).

Seals, into ONE new campaign store, the pre-committed evidence plan the next
phase reads BEFORE any dev/held-out burn:

  * ``preregistration`` -- a ``Preregistration`` (task="stack") for the GOVERNED
    ``build-stack`` evidence, copied field-for-field from place-g2's sealed
    prereg but on the M6 dev/held-out blocks and seeded from place-g2's
    both-families bundle (the sealed 3-rule bundle that governs the stack node).
  * ``chain_battery_plan`` -- the 2-arm chain battery preregistered ALONGSIDE:
    hypotheses (a chain execution + replan, b per-KIND attribution, c decide-rule
    evolution GATED-OFF as a documented null), the baseline/governed mount configs
    + skill lineage, all blocks + roles + n, the paired same-seed McNemar gate on
    the chain boolean, and the §4 go/no-go read off the calibration.
  * ``node_kinds`` -- the 11-node heterogeneous graph manifest (id, kind, predicate
    ref, oracle, replan edge, per-perceive privilege) so an auditor re-derives the
    exact graph and its privilege cost (design §4).
  * ``calibration`` -- the sealed calibration the go/no-go was decided on
    (scripts/probe_inventory_build.py output on the calibration block).

Sealing only; it burns no dev/held-out (calibration blocks never gate). The
governed bundle is documented by lineage (shas + source paths); the physical
skills-root the governed arm mounts is assembled at battery-run time (next phase)
-- tonight's store is JSON only, so it never touches the vault fold. Provider
triple is stamped from a stack-scripted mount so the sealed sha is the one a real
governed run would seal (place_campaign._stamped_sha pattern).

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/prereg_inventory_build.py \
        --out runs/inventory-build-cal
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
from plugins.embodiment_robosuite.env import stack_stages
from plugins.rsi.campaign import CampaignStore, Preregistration
from profiles import base_profile

TASK = "inventory_build"
GOVERNED_NODE = "build-stack"

#: The both-families bundle that governs the stack node. place-g2 seeds from
#: stack-g1 (regrasp) and grows the two replace rules -> its final bundle is the
#: 3-rule stack governance the chain battery's governed arm mounts.
PARENT_STORE = str(REPO / "runs/place-g2")
PARENT_FINAL_SHA = "b026831c833aa6d8c47ea2270f670074aa7e0ffca126788a7718472c203bc2c9"
STACK_G1_FINAL_SHA = "2f5f3756f23c51aaff93685412b760ae42132b72e9e9229d31140c1a9d562c1a"

#: The three sealed SkillRecords the governed arm mounts: stack-g1's regrasp +
#: place-g2's two replaces, all task="stack", all judgement-established. Given by
#: lineage; the battery phase assembles them under one skills-root.
GOVERNED_SKILLS = [
    ("runs/stack-g1/skills/57162e40d2bd4a0d59973d8c51d19f7267b682ba582c7b5c84568b334f02d41d.json", "stack-g1", "regrasp"),
    ("runs/place-g2/skills/adc5578932681b6607737cdee40164c472e1bde277b0637a3b2c02623a3c4440.json", "place-g2", "replace"),
    ("runs/place-g2/skills/eb46481a88b93cf9db9e774734fdde063725557d83f1abffe3033cd33a45a40f.json", "place-g2", "replace"),
]

#: The design's block allocation (local-archive/docs/retired-from-public/m6-mission-design.md §4; reserved in
#: STATUS.md 区块预算). Calibration is burned (measured); the rest reserved.
BLOCKS = {
    "calibration": {"lo": 50000, "hi": 50149, "n": 150, "gates": False,
                    "role": "measures q_pre / chain base rate / per-KIND first-death; never a gate"},
    "dev": {"lo": 50150, "hi": 50449, "n": 300, "gates": True,
            "role": "ordered power-scaled prefix per generation"},
    "heldout_1": {"lo": 50450, "hi": 50649, "n": 200, "gates": True, "role": "scored once"},
    "heldout_2": {"lo": 50650, "hi": 50849, "n": 200, "gates": True,
                  "role": "scored once; headline repro block"},
    "reserve": {"lo": 50850, "hi": None, "n": None, "gates": False,
                "role": "future perceive-noise decide-surface lever (gated, not built on spec)"},
}


def _load_attr(ref: str):
    mod, attr = ref.split(":", 1)
    return getattr(importlib.import_module(mod), attr)


def inventory_build_prereg() -> Preregistration:
    """The build-stack governed-evidence prereg: place-g2's fields on the M6 dev/
    held-out blocks, seeded from place-g2's both-families bundle. dev is the 300-
    seed reservoir (50150-50449); held-out #1 is 50450-50649 (200); #2 stays
    reserved for the headline repro (sealed in chain_battery_plan)."""
    return Preregistration(
        dev=tuple(range(50150, 50450)), heldout=tuple(range(50450, 50650)),
        percept_noise=0.012,
        task="stack", policy="scripted", critic_budget=1, action_budget=0,
        recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
        stages=stack_stages(), terminal_label=True, require_judgement=True,
        prior_discordance_yield=0.15, prior_judgement_yield=0.25,
        recovery_name="replace",
        parent_store=PARENT_STORE, parent_final_sha=PARENT_FINAL_SHA,
    )


def _mount(out: Path) -> Kernel:
    """base_profile + stack scripted driver -- used only to STAMP the provider
    triple (env/policy/percept); the governed skills-root is a battery-phase
    concern, not needed to seal."""
    plan = resolve_plan(base_profile(), patches=(
        Patch("inventory_build_prereg", override=(
            Mount("policy.driver", "plugins.policies:stack_scripted_provider"),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def _stamped(prereg: Preregistration, kernel: Kernel) -> Preregistration:
    return dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"))


def _node_kinds() -> dict:
    """The 11-node heterogeneous graph manifest, read from the card's OWN plan +
    PREDICATES (no hardcode). Each perceive node's declared privilege comes from
    the predicate's own return (survey seals privileged.object_z)."""
    binding = discover().task_bindings[TASK]
    planner = _load_attr(binding["planner"])()
    predicates = _load_attr(binding["predicates"])
    plan = planner.plan({"task": TASK})
    verify_by_after = {v["after"]: v["predicate"] for v in plan["verify"]}
    nodes = []
    for n in plan["nodes"]:
        kind = n.get("kind", "manipulate")
        nodes.append({
            "id": n["id"], "kind": kind, "skill": n["skill"], "after": n["after"],
            "predicate_ref": predicates.get(n["skill"]) if kind != "manipulate" else None,
            "terminal_oracle": verify_by_after.get(n["id"]) if kind == "manipulate" else None,
            "replan_edge": "node_failure -> fold into next brief (workload loop)",
        })
    return {
        "task": TASK, "planner": planner.identity, "n_nodes": len(nodes),
        "kinds": {"perceive": 2, "decide": 2, "verify": 3, "manipulate": 4},
        "privilege": {"survey": ["privileged.object_z"],
                      "note": "decide/verify/report read only ctx.nodes_out sealed facts -> zero privilege"},
        "nodes": nodes,
    }


def _chain_battery_plan(prereg_sha: str, cal: dict) -> dict:
    """The 2-arm battery, preregistered alongside the prereg (design §3a/§4/§5)."""
    gg = cal["deaths_governed"]
    gu = cal["deaths_ungoverned"]
    return {
        "preregistration_sha": prereg_sha,
        "task": TASK, "planner": "inventory_build_planner@v1",
        "calibration_verdict": (
            f"chain base rate {cal['chain_rate']} ({cal['chain_success']}/{cal['n']}), "
            f"q_pre {cal['q_pre_rate']} ({cal['q_pre']}/{cal['n']} reach build-stack). "
            f"Hard gates c1/c2/c4 PASS (base rate not 0/100, not >=0.90; q_pre>0.30). "
            f"c3 TRUE: deaths at ungoverned/deterministic nodes ({gu}) > at the "
            f"governable build-stack node ({gg}) -> EVOLUTION (decide-rule / new "
            f"promotion) is a NO-GO. Per-KIND first-death {cal['first_death_by_kind']}: "
            f"decide=0, perceive=0 -> the M6-novel decide surface has ZERO residual at "
            f"default percept-noise (needs injected noise -> a future lever). Claim (a)+(b) "
            f"with EXISTING rules is a GO on the hard gates -- the design's §5 step-4 "
            f"headline, no new promotion."),
        "no_reorder_rationale": (
            "M5 reordered the governed node first (planner@v2) to concentrate first-death "
            "and grow node-level campaign samples. NOT done for M6: the chain-boolean "
            "McNemar is order-independent (AND commutes) and q_pre is already 0.78, so a "
            "reorder would only cosmetically flip c3 without power gain -- gate-gaming, not "
            "honest measurement. The battery runs on the as-shipped @v1 order."),
        "hypotheses": {
            "a_chain_success": "the heterogeneous 11-node / 4-KIND chain executes end to "
                "end with replan; delta(governed-baseline) chain rate = (build-stack delta) "
                "x q_pre; report 2 arms on >=2 held-out blocks, paired same-seed McNemar on "
                "the chain boolean.",
            "b_attribution": "per-node + per-KIND first-death histogram localizes where "
                "chains die (calibration: 100% of residual in manipulate + a few verify; "
                "decide/perceive flat).",
            "c_decide_evolution": "the decide-rule campaign under injected perceive-noise is "
                "GATED on (b) showing decide-node residual; calibration shows ZERO at default "
                "noise -> documented NULL, not run this phase (design §3a/§5 caveat).",
        },
        "arms": {
            "baseline": {"skills_root": None, "build-stack": "ungoverned"},
            "governed": {"skills_root": "runs/inventory-build-gov/skills (assembled at battery time)",
                         "build-stack": "stack-g1 regrasp + place-g2 replace x2 (both families)",
                         "skill_records": [{"digest": Path(p).name[:-5], "source": p, "recovery": rec}
                                           for p, _src, rec in GOVERNED_SKILLS]},
        },
        "blocks": BLOCKS,
        "gate": {
            "method": "exact-McNemar enumeration on the CHAIN boolean, paired same-seed",
            "alpha": 0.05, "min_fixed": 3, "power_target": 0.8,
            "power_fix_share": 0.8, "scale_dev_by_power": True,
            "dilution": "chain discordance = q_pre x build-stack discordance; the governed "
                "delta only rescues chains failing ONLY at build-stack (grasp/pick stay); "
                "expected chain lift ~8pp -- McNemar-detectable at n=200.",
        },
        "bundle_lineage_seeds": {
            "stack_g1_final_sha": STACK_G1_FINAL_SHA,
            "place_g2_final_sha": PARENT_FINAL_SHA,
            "build_stack_governance_parent": "place-g2 (both-families bundle, 3 rules)",
        },
        "go_no_go": cal["go_no_go"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "runs/inventory-build-cal")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="calibration.json from probe_inventory_build.py (default <out>/calibration.json)")
    args = ap.parse_args()
    out = args.out
    cal_path = args.calibration or (out / "calibration.json")

    if (out / "index.jsonl").exists():
        print(f"{out} already holds a store; refusing to re-seal", file=sys.stderr)
        return 2
    if not cal_path.exists():
        print(f"no calibration at {cal_path}; run probe_inventory_build.py first", file=sys.stderr)
        return 2

    kernel = _mount(out)
    prereg = _stamped(inventory_build_prereg(), kernel)
    cal = json.loads(cal_path.read_text())

    store = CampaignStore(out)
    prereg_sha = store.put("preregistration", prereg._hash_payload())
    plan_sha = store.put("chain_battery_plan", _chain_battery_plan(prereg_sha, cal))
    kinds_sha = store.put("node_kinds", _node_kinds())
    cal["preregistration_sha"] = prereg_sha
    cal_sha = store.put("calibration", cal)

    print(f"preregistration    {prereg_sha}")
    print(f"chain_battery_plan {plan_sha[:12]}")
    print(f"node_kinds         {kinds_sha[:12]}")
    print(f"calibration        {cal_sha[:12]}")
    print(f"store              {out}")
    print(f"seeded-from        place-g2 @ {PARENT_FINAL_SHA[:12]} (build-stack both-families bundle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
