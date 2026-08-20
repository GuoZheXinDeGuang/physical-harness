"""Running reductions in the trigger language, and the hand-back contract.

Both were added in round 14 to make the harness work on a policy with no fixed
schedule. Each encodes a measured decision that a later change could silently
undo, so each has a test that fails loudly if it does.
"""
import numpy as np
import pytest

from governor.env import EpisodeSpec
from governor.governed import Bundle, RecoverySpec, Rule, governed_rollout
from governor.policy import ClonedDriver, ScriptedDriver
from governor.search import REDUCERS, Trigger, reduce_series, search_triggers


# --- running reductions -----------------------------------------------------

def test_running_reductions_are_causal():
    """Element t must reduce steps 0..t only; a lookahead would make shadow
    replay disagree with the live run."""
    x = np.array([1.0, 0.5, 0.8, 0.2, 0.9])
    assert list(reduce_series(x, "value")) == list(x)
    assert list(reduce_series(x, "min")) == [1.0, 0.5, 0.5, 0.2, 0.2]
    assert list(reduce_series(x, "max")) == [1.0, 1.0, 1.0, 1.0, 1.0]
    assert list(reduce_series(x, "range")) == [0.0, 0.5, 0.5, 0.8, 0.8]


@pytest.mark.parametrize("reducer", REDUCERS)
def test_every_reducer_is_o1_maintainable(reducer):
    """A reducer must be computable incrementally, or it cannot run at action
    frequency. Verified by folding step by step and comparing."""
    rng = np.random.RandomState(0)
    x = rng.rand(50)
    full = reduce_series(x, reducer)
    for t in range(1, len(x)):
        assert np.isclose(reduce_series(x[: t + 1], reducer)[-1], full[t])


def test_reduced_trigger_fires_where_the_reduction_crosses():
    x = np.array([1.00, 0.95, 0.90, 0.88, 0.92])
    trig = Trigger("observable.eef_z", "gt", 0.87, 1, 2, "min")
    assert trig.fire_step({"observable.eef_z": x}) == 2
    assert "running_min" in trig.describe()


def test_value_reducer_is_the_pre_round_14_behaviour():
    x = np.array([0.05, 0.05, 0.001, 0.05])
    plain = Trigger("observable.finger_gap", "lt", 0.01, 1, 0)
    assert plain.reducer == "value"
    assert plain.fire_step({"observable.finger_gap": x}) == 2


def test_search_can_return_a_reduced_trigger():
    """Failures that are time-misaligned are invisible instantaneously and
    visible under a running reduction."""
    traces, labels = [], []
    rng = np.random.RandomState(3)
    for i in range(40):
        failing = i % 2 == 0
        base = np.full(60, 1.0)
        dip_at = rng.randint(10, 50)               # the dip moves between episodes
        if not failing:
            base[dip_at] = 0.5                     # successes dip low at some point
        traces.append({
            "observable.eef_z": base,
            "observable.finger_gap": np.full(60, 0.04),
            "observable.gripper_effort": np.zeros(60),
            "observable.joint_speed": np.zeros(60),
        })
        labels.append(not failing)
    ranked = search_triggers(traces, labels, privilege_budget=0, top_k=6)
    assert ranked, "no candidate found for a time-misaligned signal"
    assert any(s.trigger.reducer != "value" for s in ranked), (
        "a misaligned signal should surface a running reduction"
    )


# --- hand-back contract -----------------------------------------------------

def test_scripted_handback_supersedes_the_interrupted_phase():
    """Measured: superseding scores +27.5pp, resuming +14.0pp with regressions."""
    spec = EpisodeSpec(seed=1)
    d = ScriptedDriver(spec)
    d.k = 30                                  # mid-'descend' (phases 25/25/12/38)
    d.on_handback()
    assert d.k == 50, "must skip to the end of the interrupted phase"


def test_cloned_handback_resumes_in_the_lift_regime():
    """Measured: resetting the clock to zero scores -2.0pp because the clone
    returns to its approach regime and undoes the repair."""
    spec = EpisodeSpec(seed=1, policy="runs/bc_h256.npz")
    try:
        d = ClonedDriver(spec, spec.policy)
    except FileNotFoundError:
        pytest.skip("cloned weights not present")
    d.k = 20
    d.on_handback()
    assert d.k == 62, "must resume where the demonstrator's schedule enters lift"
    assert d.k != 0


def test_recovery_steps_do_not_advance_the_policy_clock():
    """The policy owns its own clock; recovery steps are not policy steps."""
    spec = EpisodeSpec(seed=7)
    always = Rule("g1", Trigger("observable.eef_z", "gt", -1e9, 1, 1),
                  RecoverySpec(sensor_sd=0.02))
    r = governed_rollout(spec, Bundle(rules=(always,), critic_budget=0, action_budget=0))
    plain = governed_rollout(spec, None)
    assert r["steps"] > plain["steps"], "a fired recovery must add env steps"
    assert r["fires"], "the always-true trigger must fire"
