#!/usr/bin/env python3
"""Seal the grasp-cube DISCOVERY-campaign preregistration (M6 §3b c3 pivot).

The M6 ``inventory_build`` calibration (round 105) found the chain's dominant
UNGOVERNED killer is the ``grasp-cube`` node: 33/150 first-deaths at calibration,
unmoved by the M5 governed bundle (chain battery grasp-cube first-death 78=78 /
48=48, by design -- the bundle is task="stack", grasp-cube is task="lift"). §4 c3
therefore FORBADE decide-rule evolution and DEMANDED this node's own campaign
(design §3b: "die at an ungoverned grasp/pick -> pivot to attribution, demand
that campaign"). This is that campaign.

WHICH EVIDENCE MACHINERY (the decision, sealed into `campaign_plan`)
-------------------------------------------------------------------
The grasp-cube node dispatches skill "grasp" -> SKILL_SPECS: task="lift",
percept_noise=0.012, terminal_label=True, stages=pick_stages (one grasp stage:
observable.finger_gap gt 0.01), under the chain's mounted providers
(embodiment_robosuite env + clear_build_provider policy, which routes lift -> the
four-phase scripted driver). That is the phase-1/2 Lift task -- the SAME task the
demo Lift campaign (scripts/demo_campaign.py, runs/demo -> demo-r1/r2/r3, three
judgement-established observable.finger_gap regrasp rules) was built on.

Two options: (A) reuse the demo Lift LINEAGE (parent_store=runs/demo-r3, seed the
new generations onto its established bundle); (B) reuse only the demo Lift
MACHINERY (run_campaign + Preregistration, the task-agnostic engine) and run
FRESH from-scratch at the grasp-cube node's EXACT operating point.

Decision: **(B) fresh from-scratch.** Reasons:
  1. The engine is identical either way (run_campaign defaults to task="lift" /
     policy="scripted"); reusing it is not the question -- reusing the LINEAGE is.
  2. The demo lineage was earned at a materially DIFFERENT operating point:
     percept_noise 0.02 (not 0.012), STAGELESS with terminal_label=False (not the
     grasp-stage + terminal `lifted` predicate the M6 node scores), at base_rate
     0.4286 (demo-r3 dev_gate) vs the grasp-cube node's ~0.78. A rule's trigger
     threshold is calibrated to its residual distribution (demo-r3 fires on
     finger_gap < 0.001787, tuned to the 0.02-noise / 43%-base tail). Seeding that
     bundle into the 0.012-noise / 78%-base regime imports a mis-calibrated
     threshold and confounds the null against a claim it never tested. The
     parent_final_sha assertion would seal a bundle earned on the wrong criterion.
  3. The c3 pivot demanded a CLEAN attribution of THIS node's residual: "does a
     learnable recovery exist for grasp-cube failures AT THIS operating point."
     From-scratch answers exactly that; a warm start answers a different question
     ("can the demo bundle be extended").

So the prereg is the demo Lift campaign field-for-field, changed ONLY by the four
facts that DEFINE the grasp-cube node's operating point (percept_noise 0.012,
stages=pick_stages, terminal_label=True, provider triple stamped from the chain
mount) and moved to fresh blocks from the 50850+ reserve. parent_store=None.

Sealed artifacts (mirrors prereg_inventory_build.py):
  * preregistration -- the from-scratch grasp-cube Preregistration.
  * campaign_plan   -- hypothesis (a learnable regrasp recovery exists for
    grasp-cube failures), the gates (paired same-seed McNemar on the grasp
    boolean + blind twin + min_fixed), blocks + roles + n, the §4 go/no-go read
    off the isolated-node calibration, and this machinery decision.
  * calibration     -- the isolated-node baseline the go/no-go was decided on
    (scripts/probe_grasp_cube.py output).

Sealing only; burns no dev/held-out (calibration never gates).

    PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/prereg_grasp_cube.py \
        --out runs/grasp-cube-cal
"""

from __future__ import annotations

import argparse
import dataclasses
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

TASK = "inventory_build"
NODE = "grasp-cube"

#: The grasp-cube node's operating point, read from the SKILL_SPECS "grasp"
#: binding + the chain mount (so the campaign reproduces the node exactly).
#: dev = 300-seed reservoir; held-out #1 = 200; both from the 50850+ reserve.
DEV = tuple(range(51000, 51300))
HELDOUT = tuple(range(51300, 51500))

BLOCKS = {
    "calibration": {"lo": 50850, "hi": 50999, "n": 150, "gates": False,
                    "role": "isolated grasp-cube base rate + grasp-stage attribution; never a gate"},
    "dev": {"lo": 51000, "hi": 51299, "n": 300, "gates": True,
            "role": "ordered power-scaled prefix per generation"},
    "heldout_1": {"lo": 51300, "hi": 51499, "n": 200, "gates": True,
                  "role": "scored ONCE, only on a dev promotion"},
    "reserve": {"lo": 51500, "hi": None, "n": None, "gates": False, "role": "future"},
}


def grasp_cube_prereg() -> Preregistration:
    """The demo Lift campaign field-for-field, changed ONLY by the grasp-cube
    node's operating point (percept_noise 0.012, pick_stages, terminal_label) and
    fresh blocks. parent_store=None -> from-scratch (see module docstring)."""
    return Preregistration(
        dev=DEV, heldout=HELDOUT,
        percept_noise=0.012,            # grasp-cube SKILL_SPECS point (demo used 0.02)
        task="lift", policy="scripted",
        critic_budget=0, action_budget=0,   # observable.finger_gap is zero-privilege
        recovery_sensor_sd=0.02,        # demo's recovery operating point, unchanged
        max_generations=2, scale_dev_by_power=True,
        stages=pick_stages(),           # grasp-cube scores the grasp stage ...
        terminal_label=True,            # ... plus the terminal lifted() label
        recovery_name="regrasp",        # the grasp repair (demo-r* rules are regrasp)
        require_judgement=True,
        # parent_store defaults to None -> from-scratch.
    )


def _mount() -> Kernel:
    """base_profile + the chain's clear_build_provider policy -- the EXACT mount
    the grasp-cube node runs under (probe_inventory_build._probe_one), used to
    stamp the provider triple so the sealed sha is the one a real chain run seals."""
    binding = discover().task_bindings[TASK]
    plan = resolve_plan(base_profile(), patches=(
        Patch("prereg_grasp_cube", override=(
            Mount("policy.driver", binding["policy"]),)),))
    # In-memory log: this mount only resolves provider_refs for stamping, it runs
    # no episodes, so it must not claim an on-disk ledger (build_prereg is called
    # by the probe too). The CampaignStore in main() is the real on-disk artifact.
    kernel = Kernel(CAPABILITIES, log=SessionLog())
    kernel.mount(plan)
    return kernel


def _stamped(prereg: Preregistration, kernel: Kernel) -> Preregistration:
    return dataclasses.replace(
        prereg,
        env_provider=kernel.provider_ref("embodiment.env"),
        policy_provider=kernel.provider_ref("policy.driver"),
        percept_provider=kernel.provider_ref("percept.model"))


def build_prereg() -> Preregistration:
    """The stamped prereg -- shared by the probe (calibration) and the campaign so
    both build episodes from ONE spec definition."""
    return _stamped(grasp_cube_prereg(), _mount())


def _campaign_plan(prereg_sha: str, cal: dict) -> dict:
    go = cal["go_no_go"]
    return {
        "preregistration_sha": prereg_sha,
        "task": "lift", "node": f"{TASK}:{NODE}", "campaign": "grasp-cube-g1",
        "machinery_decision": (
            "FROM-SCRATCH (parent_store=None). Reuse the demo Lift campaign ENGINE "
            "(run_campaign/Preregistration), NOT its lineage. The demo bundle was "
            "earned at percept_noise 0.02 / stageless / terminal_label=False / "
            "base_rate 0.4286; the grasp-cube node scores pick_stages + terminal "
            "lifted at 0.012 / base_rate ~0.78. Seeding that bundle would import a "
            "threshold tuned to a different residual distribution. c3 demanded a "
            "clean attribution of THIS node's residual -> cold start at the node's "
            "exact operating point (docstring, prereg_grasp_cube.py)."),
        "operating_point": {
            "task": "lift", "policy": "scripted (via clear_build_provider, lift->four-phase)",
            "percept_noise": 0.012, "stages": "pick_stages (grasp: observable.finger_gap gt 0.01)",
            "terminal_label": True, "recovery_name": "regrasp",
            "env_provider": "plugins.embodiment_robosuite:provider",
            "policy_provider": "plugins.policies:clear_build_provider",
        },
        "hypothesis": "a learnable recovery (regrasp on the observable grasp state) "
            "exists for grasp-cube failures at the M6 operating point; a promoted "
            "rule lifts the isolated grasp boolean above its ungoverned base rate.",
        "gates": {
            "method": "exact same-seed McNemar on the grasp boolean (dev, paired vs "
                "parent) + blind-twin judgement (require_judgement) + min_fixed",
            "alpha": 0.05, "min_fixed": 3, "require_judgement": True,
            "power_target": 0.8, "power_fix_share": 0.8, "scale_dev_by_power": True,
            "promotion": "fixed>=3 AND p<0.05 AND fixed>broken AND beats its blind twin; "
                "else NULL (valid result). Held-out (51300-51499) scored ONCE, only "
                "if the bundle promotes >=1 rule (run_campaign scores it iff bundle.rules).",
        },
        "blocks": BLOCKS,
        "calibration_verdict": (
            f"isolated grasp-cube base rate {cal['base_rate']} "
            f"({cal['successes']}/{cal['n']}) at percept_noise 0.012. "
            f"§4 abort gates: 0%/100% degenerate={go['base_degenerate_0_or_100']}, "
            f">=0.90 null ceiling={go['c2_at_or_above_0.90']} -> "
            f"proceed={go['proceed']}. This is the ungoverned node c3 demanded; "
            f"q_pre/c3 do not apply to a single isolated node."),
        "go_no_go": go,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "runs/grasp-cube-cal")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="calibration.json from probe_grasp_cube.py (default <out>/calibration.json)")
    args = ap.parse_args()
    out = args.out
    cal_path = args.calibration or (out / "calibration.json")

    if (out / "index.jsonl").exists():
        print(f"{out} already holds a store; refusing to re-seal", file=sys.stderr)
        return 2
    if not cal_path.exists():
        print(f"no calibration at {cal_path}; run probe_grasp_cube.py first", file=sys.stderr)
        return 2

    prereg = build_prereg()
    cal = json.loads(cal_path.read_text())

    store = CampaignStore(out)
    prereg_sha = store.put("preregistration", prereg._hash_payload())
    plan_sha = store.put("campaign_plan", _campaign_plan(prereg_sha, cal))
    cal["preregistration_sha"] = prereg_sha
    cal_sha = store.put("calibration", cal)

    print(f"preregistration {prereg_sha}")
    print(f"campaign_plan   {plan_sha[:12]}")
    print(f"calibration     {cal_sha[:12]}")
    print(f"store           {out}")
    print(f"machinery       FROM-SCRATCH (parent_store=None) at the grasp-cube operating point")
    return 0


if __name__ == "__main__":
    sys.exit(main())
