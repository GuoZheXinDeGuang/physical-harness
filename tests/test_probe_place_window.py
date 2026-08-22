"""Round 92: place-window probe classifiers, all fakes -- no robosuite.

The pure parts of scripts/probe_place_window.py: schedule geometry, the id
collision detector (the root cause), chain-order fire attribution, the held/
empty gripper band, per-episode classification, and the recovery x flip
cross-tab. `run_window_probe` (real rollouts) is exercised by the orchestrator.
"""

import numpy as np

import plugins.embodiment_robosuite.features  # noqa: F401  registers the feature catalog
from plugins.rsi.governed import RecoverySpec, Rule
from plugins.rsi.stats.search import Trigger
from scripts.probe_place_window import (
    attribute_fire,
    classify_fire,
    crosstab,
    held_at_fire,
    id_collisions,
    phase_at,
)


def _regrasp(rid="g1"):
    return Rule(rid, Trigger("observable.finger_gap", "lt", 0.001, 1, 58, "value"),
                RecoverySpec(name="regrasp", sensor_sd=0.02))


def _replace(rid="g1"):
    return Rule(rid, Trigger("privileged.stack_xy_residual", "gt", 0.033809, 3, 108, "value"),
                RecoverySpec(name="replace", sensor_sd=0.02, max_invocations=1))


# --- schedule geometry -------------------------------------------------------
def test_phase_at_maps_steps_to_the_sealed_schedule():
    assert phase_at(0) == "above"
    assert phase_at(55) == "close"        # 50..62
    assert phase_at(88) == "lift"         # 62..92
    assert phase_at(110) == "over_b"      # 92..117
    assert phase_at(130) == "place"       # 117..142
    assert phase_at(145) == "release"     # 142..152
    assert phase_at(1000) == "past_end"


# --- the root cause: shared rule_id ------------------------------------------
def test_id_collisions_flags_the_shared_g1():
    # The sealed candidate: parent grasp rule + appended place rule, BOTH "g1".
    assert id_collisions((_regrasp("g1"), _replace("g1"))) == {"g1": 2}
    # De-collided: distinct ids -> no collision.
    assert id_collisions((_regrasp("g1"), _replace("g1-place"))) == {}


# --- chain-order fire attribution --------------------------------------------
def test_attribute_fire_prefers_the_chain_first_matching_rule():
    rules = (_regrasp("g1"), _replace("g1"))
    # A grasp-drop step: finger_gap collapsed, xy also high. Both triggers hold,
    # but the chain-first regrasp owns the fire -- exactly the sealed behaviour.
    trace = {"observable.finger_gap": np.array([0.0004]),
             "privileged.stack_xy_residual": np.array([0.20])}
    idx, rule = attribute_fire(0, trace, rules)
    assert idx == 0 and rule.recovery.name == "regrasp"

    # A held-cube high-residual step: only the place trigger holds -> replace.
    trace2 = {"observable.finger_gap": np.array([0.04]),
              "privileged.stack_xy_residual": np.array([0.20])}
    idx2, rule2 = attribute_fire(0, trace2, rules)
    assert idx2 == 1 and rule2.recovery.name == "replace"


def test_attribute_fire_returns_none_when_nothing_matches():
    trace = {"observable.finger_gap": np.array([0.04]),
             "privileged.stack_xy_residual": np.array([0.01])}
    assert attribute_fire(0, trace, (_regrasp(), _replace())) == (None, None)


# --- held / empty gripper band -----------------------------------------------
def test_held_at_fire_bands():
    held = {"observable.finger_gap": np.array([0.04])}
    dropped = {"observable.finger_gap": np.array([0.0002])}   # closed on nothing
    released = {"observable.finger_gap": np.array([0.078])}   # opened gripper
    assert held_at_fire(held, 0)
    assert not held_at_fire(dropped, 0)
    assert not held_at_fire(released, 0)


# --- per-episode classification ----------------------------------------------
def _result(seed, fire_step, fg, xy, success=False):
    n = max(fire_step + 1, 5)
    fgv = np.full(n, 0.04)
    xyv = np.full(n, 0.01)
    fgv[fire_step] = fg
    xyv[fire_step] = xy
    return {"seed": seed, "success": success,
            "fires": [{"rule_id": "g1", "step": fire_step, "env_step": fire_step}],
            "trace": {"observable.finger_gap": fgv, "privileged.stack_xy_residual": xyv}}


def test_classify_fire_records_a_grasp_drop_as_regrasp():
    rules = (_regrasp("g1"), _replace("g1"))
    rec = classify_fire(_result(7, fire_step=88, fg=0.0003, xy=0.02), rules)
    assert rec["seed"] == 7 and rec["fire_step"] == 88
    assert rec["phase"] == "lift"
    assert rec["recovery"] == "regrasp"
    assert rec["held"] is False          # dropped, not held
    assert rec["rule_index"] == 0


def test_classify_fire_returns_none_without_a_fire():
    r = {"seed": 1, "success": True, "fires": [],
         "trace": {"observable.finger_gap": np.zeros(5),
                   "privileged.stack_xy_residual": np.zeros(5)}}
    assert classify_fire(r, (_regrasp(), _replace())) is None


# --- recovery x flip cross-tab -----------------------------------------------
def test_crosstab_buckets_recovery_against_flips():
    records = [
        {"seed": 1, "recovery": "regrasp"},
        {"seed": 2, "recovery": "regrasp"},
        {"seed": 3, "recovery": "replace"},
    ]
    tab = crosstab(records, flipped_seeds={3})
    assert tab["regrasp"] == {"flip": 0, "no_flip": 2}
    assert tab["replace"] == {"flip": 1, "no_flip": 0}
