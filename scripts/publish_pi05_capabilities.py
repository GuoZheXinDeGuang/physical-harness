#!/usr/bin/env python3
"""Emit the kitchen_thaw segment capability records from SEALED rollout numbers.

The composition graph a planner reads at startup should be DATA, not a hardcoded
chain: which executor can do which segment, under which live-state preconditions,
with which measured rate. This publishes one ``harness/skill_record.py`` capability
record per segment executor -- the scripted navigate/grasp/carry/place stages
(``plugins/embodiment_robocasa/kitchen_driver.py``) and the fine-tuned pi0.5 place
binding (``plugins/policy_vla_remote``) -- into an ``InMemorySkillGraph`` root, then
prints and seals ``skill_index()`` over them. No VLM planner, no inference: records
plus the pure set-containment index the schema already derives.

Every ``measured`` number is READ from the sealed round99 rollouts, never typed in,
so re-running this after a re-measured campaign moves the numbers with the evidence.
The GRASP ruler is the SECURE one (the driver's own ``done()`` requires the object
to rise SECURE_DZ; the probe binds ``obj_grasped_secure`` to the meat's rest z), so
these are grasp rates, not the audited-away contact latch.

    scripts/publish_pi05_capabilities.py \
        --scripted runs/pi05-campaign/round99_scripted \
        --handover runs/pi05-campaign/round99_handover \
        --root     runs/pi05-campaign/round99_skills
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.skill_record import skill_index
from plugins.graphs import InMemorySkillGraph

PRED = "plugins.embodiment_robocasa.predicates:"
FRIDGE = PRED + "fridge_is_open"
GRASPED = PRED + "obj_grasped_secure"   # the SECURE ruler, never the contact latch
INSIDE = PRED + "obj_in_microwave"
SCRIPTED_REF = "plugins.embodiment_robocasa.kitchen_driver:provider"
VLA_REF = "plugins.policy_vla_remote:provider"


def _eps(run_dir: Path, arm: str) -> list[dict]:
    return [json.loads(Path(f).read_text())
            for f in sorted(glob.glob(str(run_dir / f"ep_{arm}_*.json")))]


def _seg_success(ep: dict, seg: str) -> bool:
    for s in (ep.get("segments") or []):
        if s["segment"] == seg:
            return bool(s["success"])
    return False


def measure(scripted_dir: Path, handover_dir: Path) -> dict:
    """Fold the sealed rollouts into the per-executor (successes, n, seeds)."""
    sc = _eps(scripted_dir, "scripted")
    seeds = sorted(e["seed"] for e in sc)
    n = len(sc)

    # navigate: the robot is at the OPEN fridge (the mission's own v_at_fridge
    # verify is fridge_is_open); reached in stage_reached.
    nav_ok = sum(bool(e["stage_reached"].get("fridge_is_open")) for e in sc)
    # grasp: the driver's done() is SECURE (risen SECURE_DZ); segment success.
    grasp_ok = sum(_seg_success(e, "grasp") for e in sc)
    # carry: retained the secure grasp through transport -- grasp segment
    # succeeded AND the object is still held at the place hand-off moment.
    carry_ok = sum(_seg_success(e, "grasp") and bool((e.get("handover") or {}).get("grasped"))
                   for e in sc)
    # place (scripted): obj_in_microwave ever True.
    place_sc = sum(bool(e["stage_reached"].get("obj_in_microwave")) for e in sc)

    ho = _eps(handover_dir, "handover")
    place_vla = sum(bool(e["stage_reached"].get("obj_in_microwave")) for e in ho)
    n_ho = len(ho)
    return {
        "seeds": seeds, "n": n, "n_ho": n_ho,
        "navigate": nav_ok, "grasp": grasp_ok, "carry": carry_ok,
        "place_scripted": place_sc, "place_vla": place_vla,
    }


def records(m: dict, sha: str) -> list[dict]:
    seeds, n, n_ho = m["seeds"], m["n"], m["n_ho"]

    def scripted(skill, pre, eff, pred, ok):
        return {"kind": "capability", "skill": skill, "task": "kitchen_thaw",
                "binding": {"ref": SCRIPTED_REF},
                "preconditions": pre, "effects": eff,
                "measured": {"predicate": pred, "successes": ok, "n": n,
                             "seeds": seeds, "split": "train"}}

    return [
        scripted("navigate", [FRIDGE], [FRIDGE], FRIDGE, m["navigate"]),
        scripted("grasp", [FRIDGE], [GRASPED], GRASPED, m["grasp"]),
        scripted("carry", [GRASPED], [GRASPED], GRASPED, m["carry"]),
        scripted("place", [GRASPED], [INSIDE], INSIDE, m["place_scripted"]),
        {"kind": "capability", "skill": "place", "task": "kitchen_thaw",
         "binding": {"ref": VLA_REF, "checkpoint_sha": sha},
         "preconditions": [GRASPED], "effects": [INSIDE],
         "measured": {"predicate": INSIDE, "successes": m["place_vla"], "n": n_ho,
                      "seeds": seeds, "split": "train"}},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scripted", default="runs/pi05-campaign/round99_scripted")
    ap.add_argument("--handover", default="runs/pi05-campaign/round99_handover")
    ap.add_argument("--root", default="runs/pi05-campaign/round99_skills")
    ap.add_argument("--sha", default="ea09cb15589c8770ae4f75b3974623d993e98ab87b0966598666d884d0b9fe76")
    a = ap.parse_args()

    m = measure(Path(a.scripted), Path(a.handover))
    store = InMemorySkillGraph(root=a.root)
    for rec in records(m, a.sha):
        digest = store.publish(rec)   # validate_capability runs inside publish()
        b = rec["binding"]
        who = "pi0.5@" + b["checkpoint_sha"][:8] if "checkpoint_sha" in b else "scripted"
        print(f"published {rec['skill']:9s} [{who:16s}] "
              f"{rec['measured']['successes']}/{rec['measured']['n']}  {digest[:12]}")

    idx = skill_index(store.skills())
    (Path(a.root) / "skill_index.json").write_text(
        json.dumps(idx, indent=1, sort_keys=True))
    print("\nskill_index (the composition graph a planner reads):")
    print(json.dumps(idx, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
