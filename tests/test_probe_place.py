"""Round 90 rung 2: probe classification/geometry, all fakes -- no robosuite.

The pure parts of scripts/probe_place.py: schedule geometry, place-failure and
observable-tell classification, the diagnostic-bundle shape, and the paired
conversion/broken arithmetic. `run_probes` (real rollouts) is exercised by the
orchestrator, not here.
"""

import numpy as np

import plugins.embodiment_robosuite.features  # noqa: F401  registers the feature catalog for declared_privilege
from harness.spec import STACK_SCHEDULE
from plugins.rsi.governed import Bundle, RecoverySpec, Rule
from plugins.rsi.stats.search import Trigger
from scripts.probe_place import (
    PLACE_WINDOW,
    diagnostic_bundle,
    failure_mode,
    has_observable_tell,
    is_place_failure,
    pair_outcomes,
    schedule_step,
)


def _gen1() -> Bundle:
    # A grasp-shaped stand-in for the sealed gen1 rule; only its canonical shape
    # matters for the diagnostic-bundle parity check below.
    return Bundle(rules=(Rule("g1", Trigger("observable.finger_gap", "lt", 0.001, 1, 58, "value"),
                              RecoverySpec(name="regrasp", sensor_sd=0.02)),),
                  critic_budget=0, action_budget=0)


def _stage(name, success, reached=True):
    return {"name": name, "success": success, "reached": reached,
            "entered_step": 0, "exited_step": 1, "privilege_used": 1}


def _result(grasp, place, place_reached=True, fg=None, seed=1, success=False, trace=None):
    r = {"seed": seed, "success": success,
         "stages": [_stage("grasp", grasp), _stage("place", place, place_reached)],
         "fired_rules": [], "trace": trace or {}}
    if fg is not None:
        r["trace"]["observable.finger_gap"] = np.asarray(fg)
    return r


# --- schedule geometry -------------------------------------------------------
def test_place_window_is_over_b_to_release():
    # over_b onset .. release onset on the sealed schedule: (92, 142).
    assert schedule_step("over_b") == 92
    assert schedule_step("release") == 142
    assert PLACE_WINDOW == (92, 142)
    # And it is genuinely derived from the schedule, not hard-coded.
    acc = {}
    c = 0
    for name, dur in STACK_SCHEDULE:
        acc[name] = c
        c += dur
    assert PLACE_WINDOW == (acc["over_b"], acc["release"])


# --- place-failure classification --------------------------------------------
def test_is_place_failure_needs_grasp_held_and_place_failed():
    assert is_place_failure(_result(grasp=True, place=False))          # the target case
    assert not is_place_failure(_result(grasp=False, place=False))     # grasp failure, not ours
    assert not is_place_failure(_result(grasp=True, place=True))       # placed fine
    assert not is_place_failure(_result(grasp=True, place=False, place_reached=False))  # never reached


# --- observable-tell classification ------------------------------------------
def test_tell_only_counts_a_collapse_inside_the_window():
    held = [0.04] * 177
    assert not has_observable_tell(_result(True, False, fg=held))

    dropped = list(held)
    dropped[120] = 0.001  # inside [92, 142): a real mid-place collapse
    assert has_observable_tell(_result(True, False, fg=dropped))

    # A dip at release onset (142) is the INTENDED open, not a dropped cube.
    at_release = list(held)
    at_release[142] = 0.0
    assert not has_observable_tell(_result(True, False, fg=at_release))

    # A short trace that never reaches the window raises no error and reads False.
    assert not has_observable_tell(_result(True, False, fg=[0.04] * 50))


# --- diagnostic bundle shape -------------------------------------------------
def test_diagnostic_bundle_appends_privileged_place_trigger():
    gen1 = _gen1()
    d = diagnostic_bundle(gen1)
    assert d.critic_budget == 1 and d.action_budget == 0
    # gen1 rules preserved byte-for-byte; exactly one rule appended.
    assert len(d.rules) == len(gen1.rules) + 1
    assert d.rules[0].canonical() == gen1.rules[0].canonical()
    place = d.rules[-1]
    assert place.rule_id == "place-bringup"
    assert place.trigger.feature == "privileged.stack_xy_residual"
    assert place.trigger.op == "gt" and place.trigger.threshold == 0.025
    assert place.trigger.arm_after == schedule_step("over_b")  # armed at place-window start
    assert place.recovery.name == "replace" and place.recovery.sensor_sd == 0.020
    # A privileged trigger: the whole reason critic_budget must be 1.
    assert d.declared_privilege() >= 1


# --- paired conversion/broken arithmetic -------------------------------------
def test_pair_outcomes_splits_conversions_from_broken():
    gen1 = {1: False, 2: False, 3: True, 4: True}
    diag = {1: True, 2: False, 3: False, 4: True}
    out = pair_outcomes(gen1, diag)
    assert out["conversions"] == [1]  # failed->success
    assert out["broken"] == [3]       # success->failed (2 stayed failed, 4 stayed success)


# --- failure-mode diagnosis --------------------------------------------------
def test_failure_mode_buckets_by_final_residual():
    def r(seed, xy, z):
        return {"seed": seed, "fired_rules": ["place-bringup"],
                "trace": {"privileged.stack_xy_residual": np.array([1.0, xy]),
                          "privileged.stack_z_residual": np.array([0.5, z])}}
    assert failure_mode(r(1, 0.09, 0.0))["mode"] == "xy_off"
    assert failure_mode(r(2, 0.01, 0.10))["mode"] == "z_high"
    assert failure_mode(r(3, 0.01, -0.10))["mode"] == "z_low_dropped"
    assert failure_mode(r(4, 0.01, 0.0))["mode"] == "within_tol_terminal_false"
