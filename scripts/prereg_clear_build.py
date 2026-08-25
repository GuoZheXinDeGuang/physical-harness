#!/usr/bin/env python3
"""Seal the clear_build preregistration (docs/long-horizon-design.md §5).

Seals, into ONE new campaign store, the pre-committed evidence plan BEFORE any
dev/held-out burn:

  * ``preregistration`` -- a ``Preregistration`` (task="stack") for the n4 GOVERNED
    evidence, copied field-for-field from place-g2's sealed prereg but on the
    clear_build blocks and seeded from place-g2's both-families bundle (the bundle
    that governs the stack node). This is "the n4 governed evidence" the design
    §5 names.
  * ``chain_battery_plan`` -- the 3-arm chain battery preregistered ALONGSIDE:
    hypotheses, the baseline/governed/evolved mount configs + their skill lineage,
    all five seed blocks + roles + n, the paired same-seed McNemar gate on the
    chain boolean, and the §4 go/no-go criteria.
  * ``calibration`` -- the sealed calibration read the go/no-go was decided on
    (scripts/probe_clear_build.py output on the calibration block).

Sealing only; it burns no dev/held-out (calibration blocks never gate). Provider
triple is stamped from a place-style mount so the sealed sha is the one a real
run would seal (place_campaign._stamped_sha pattern).

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/prereg_clear_build.py \
        --out runs/clear-build-cal
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness.config import Mount, Patch, resolve_plan
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.kernel import Kernel
from plugins.embodiment_robosuite.env import stack_stages
from plugins.rsi.campaign import CampaignStore, Preregistration
from profiles import base_profile

#: The both-families bundle that governs the stack node (n4). place-g2 seeds from
#: stack-g1 (regrasp) and grows the two replace rules -> its final bundle is the
#: 3-rule n4 governance the chain battery mounts. This prereg seeds from it.
PARENT_STORE = str(REPO / "runs/place-g2")
PARENT_FINAL_SHA = "b026831c833aa6d8c47ea2270f670074aa7e0ffca126788a7718472c203bc2c9"
STACK_G1_FINAL_SHA = "2f5f3756f23c51aaff93685412b760ae42132b72e9e9229d31140c1a9d562c1a"

#: The three sealed SkillRecords the governed arm mounts (E2): stack-g1's regrasp
#: + place-g2's two replaces, all task="stack", all judgement-established.
GOVERNED_SKILLS = [
    (REPO / "runs/stack-g1/skills/57162e40d2bd4a0d59973d8c51d19f7267b682ba582c7b5c84568b334f02d41d.json", "stack-g1", "regrasp"),
    (REPO / "runs/place-g2/skills/adc5578932681b6607737cdee40164c472e1bde277b0637a3b2c02623a3c4440.json", "place-g2", "replace"),
    (REPO / "runs/place-g2/skills/eb46481a88b93cf9db9e774734fdde063725557d83f1abffe3033cd33a45a40f.json", "place-g2", "replace"),
]

#: The design's block allocation (docs/long-horizon-design.md §4; reserved in
#: STATUS.md 区块预算). Calibration is burned (measured); the rest reserved.
BLOCKS = {
    "calibration": {"lo": 48900, "hi": 49049, "n": 150, "gates": False,
                    "role": "measures q_pre / chain base rate / first-death; never a gate"},
    "dev": {"lo": 49050, "hi": 49349, "n": 300, "gates": True,
            "role": "ordered power-scaled prefix per generation"},
    "heldout_1": {"lo": 49350, "hi": 49549, "n": 200, "gates": True, "role": "scored once"},
    "heldout_2": {"lo": 49550, "hi": 49749, "n": 200, "gates": True, "role": "scored once"},
    "heldout_3": {"lo": 49750, "hi": 49949, "n": 200, "gates": True,
                  "role": "scored once; headline repro block #3"},
    "reserve": {"lo": 50000, "hi": None, "n": None, "gates": False,
                "role": "Phase-2 inter-node surface (gated, not built on spec)"},
}


def clear_build_prereg() -> Preregistration:
    """The n4 governed-evidence prereg: place-g2's fields on the clear_build dev/
    held-out blocks, seeded from place-g2's both-families bundle. dev is the 300-
    seed reservoir (49050-49349), held-out #1 is 49350-49549 (200); #2/#3 stay
    reserved for the headline repro (sealed in chain_battery_plan)."""
    return Preregistration(
        dev=tuple(range(49050, 49350)), heldout=tuple(range(49350, 49550)),
        percept_noise=0.012,
        task="stack", policy="scripted", critic_budget=1, action_budget=0,
        recovery_sensor_sd=0.020, max_generations=2, scale_dev_by_power=True,
        stages=stack_stages(), terminal_label=True, require_judgement=True,
        prior_discordance_yield=0.15, prior_judgement_yield=0.25,
        recovery_name="replace",
        parent_store=PARENT_STORE, parent_final_sha=PARENT_FINAL_SHA,
    )


def _mount(out: Path) -> Kernel:
    """base_profile + stack scripted driver + the governed skills root (E2) --
    the place_campaign._mount pattern; only used to STAMP the provider triple."""
    plan = resolve_plan(base_profile(), patches=(
        Patch("clear_build_prereg", override=(
            Mount("policy.driver", "plugins.policies:stack_scripted_provider"),
            Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                  {"root": str(out / "skills")}),)),))
    kernel = Kernel(CAPABILITIES, log=SessionLog(out / "session-log"))
    kernel.mount(plan)
    return kernel


def _stamped(prereg: Preregistration, kernel: Kernel) -> Preregistration:
    return dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"))


def _chain_battery_plan(prereg_sha: str) -> dict:
    """The 3-arm battery, preregistered alongside the prereg (design §3a/§4)."""
    return {
        "preregistration_sha": prereg_sha,
        "task": "clear_build", "planner": "clear_build_planner@v2",
        "hypotheses": {
            "a_chain_success": "chain rate = prod(node rates); delta(governed-baseline) "
                "= (n4 delta) x q_pre; report 3 arms on >=2 held-out blocks, paired "
                "same-seed McNemar on the chain boolean.",
            "b_attribution": "first-death histogram + per-node rate over the block "
                "answers WHERE chains die before any evolution.",
            "c_inter_node": "Phase-2 inter-node recovery rule, GATED on (b) proving "
                "chains die at n4 in a way an inter-node route fixes; not built on spec (§6.3).",
        },
        "arms": {
            "baseline": {"skills_root": None, "n4": "ungoverned"},
            "governed": {"skills_root": "runs/clear-build-cal/skills",
                         "n4": "stack-g1 regrasp + place-g2 replace x2 (both families)",
                         "skill_records": [{"digest": p.name[:-5], "source": src, "recovery": rec}
                                           for p, src, rec in GOVERNED_SKILLS]},
            "evolved": {"skills_root": None,
                        "note": "+ any Phase-2 promoted rule; not built here (gated on b)"},
        },
        "blocks": BLOCKS,
        "gate": {
            "method": "exact-McNemar enumeration on the CHAIN boolean, paired same-seed",
            "alpha": 0.05, "min_fixed": 3, "power_target": 0.8,
            "power_fix_share": 0.8, "scale_dev_by_power": True,
            "dilution": "chain discordance = q_pre x n4 discordance; per-gen power-scaling essential",
        },
        "bundle_lineage_seeds": {
            "stack_g1_final_sha": STACK_G1_FINAL_SHA,
            "place_g2_final_sha": PARENT_FINAL_SHA,
            "n4_governance_parent": "place-g2 (both-families bundle, 3 rules)",
        },
        "go_no_go": {
            "c1_abort": "q_pre < 0.30 -> reorder stack earlier or abort",
            "c2_null": "chain base rate >= 0.90 -> honest null, no burn",
            "c3_pivot": "chains die at an ungoverned node, not n4 -> pivot to attribution, "
                "demand a grasp/pick campaign, do NOT run Phase-2",
            "c4_stop": "baseline 0% or 100% -> STOP, no gate can learn",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "runs/clear-build-cal")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="calibration.json from probe_clear_build.py (default <out>/calibration.json)")
    args = ap.parse_args()
    out = args.out
    cal_path = args.calibration or (out / "calibration.json")

    if (out / "index.jsonl").exists():
        print(f"{out} already holds a store; refusing to re-seal", file=sys.stderr)
        return 2

    # E2 mount: copy the three sealed SkillRecords under one skills/ root so the
    # governed arm is byte-reproducible from this store.
    (out / "skills").mkdir(parents=True, exist_ok=True)
    for src, _store, _rec in GOVERNED_SKILLS:
        shutil.copy2(src, out / "skills" / src.name)

    kernel = _mount(out)
    prereg = _stamped(clear_build_prereg(), kernel)
    store = CampaignStore(out)
    prereg_sha = store.put("preregistration", prereg._hash_payload())
    plan_sha = store.put("chain_battery_plan", _chain_battery_plan(prereg_sha))

    cal_sha = None
    if cal_path.exists():
        cal = json.loads(cal_path.read_text())
        cal["preregistration_sha"] = prereg_sha
        cal_sha = store.put("calibration", cal)

    print(f"preregistration   {prereg_sha}")
    print(f"chain_battery_plan {plan_sha[:12]}")
    print(f"calibration        {cal_sha[:12] if cal_sha else '(none)'}")
    print(f"store              {out}")
    print(f"seeded-from        place-g2 @ {PARENT_FINAL_SHA[:12]} (n4 both-families bundle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
