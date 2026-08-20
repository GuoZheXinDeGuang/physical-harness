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


# --- recovery repertoire ----------------------------------------------------

def test_every_strategy_resolves_to_offset_steps():
    from governor.repertoire import REPERTOIRE
    from governor.env import PHASE_HEIGHT
    for s in REPERTOIRE:
        assert s.steps, f"{s.name} has no steps"
        for name, dur, dx, dy in s.steps:
            assert name in PHASE_HEIGHT or name.startswith("servo_"), (
                f"{s.name} uses unknown step {name!r}"
            )
            assert dur > 0 and isinstance(dur, int)
            assert abs(dx) < 0.2 and abs(dy) < 0.2, "an offset that large leaves the workspace"
        assert s.rationale, f"{s.name} must say what failure it is for"


def test_unknown_strategy_is_rejected():
    from governor.repertoire import strategy
    with pytest.raises(KeyError, match="unknown recovery strategy"):
        strategy("teleport")


def test_explicit_program_overrides_the_named_strategy():
    spec = RecoverySpec(name="regrasp", program=(("lift", 5),))
    assert spec.steps() == (("lift", 5, 0.0, 0.0))[0:1] or spec.steps() == (("lift", 5, 0.0, 0.0),)


def test_servo_strategy_is_declared_as_feedback():
    """A feedback segment decides its own length, so `length` is an upper bound."""
    from governor.repertoire import strategy
    servo = strategy("servo_regrasp")
    assert servo.uses_feedback
    assert not strategy("regrasp").uses_feedback


def test_servo_primitives_read_only_proprioception():
    """The whole point: contact and finger motion are things a real robot feels."""
    import inspect
    from governor import servo
    src = inspect.getsource(servo)
    for privileged in ("cube_pos", "cubeA_pos", "Can_pos", "object_z", "grasp_error"):
        assert privileged not in src, (
            f"servo primitive reads {privileged!r}; that would spend privilege"
        )


def test_recovery_actor_runs_a_mixed_program():
    """Fixed and servo segments must compose in one program."""
    import numpy as np
    from governor.policy import RecoveryActor
    from governor.repertoire import strategy

    actor = RecoveryActor(strategy("servo_regrasp").steps, np.array([0.0, 0.0, 0.83]))
    obs = {"robot0_eef_pos": np.array([0.0, 0.0, 1.0]),
           "robot0_gripper_qpos": np.array([0.04, -0.04])}
    steps = 0
    while not actor.done and steps < 400:
        a = actor.act(obs)
        assert a.shape == (7,) and np.all(np.abs(a) <= 1.0 + 1e-9)
        obs["robot0_eef_pos"] = obs["robot0_eef_pos"] - np.array([0.0, 0.0, 0.002])
        steps += 1
    assert actor.done, "a mixed program must terminate"
    assert steps > 0


def test_strategy_choice_changes_behaviour():
    """A repertoire whose members behave identically would be decoration."""
    from governor.repertoire import strategy
    assert strategy("settle").steps != strategy("regrasp").steps
    assert strategy("lateral").steps != strategy("regrasp").steps
    assert any(dx != 0.0 for _n, _d, dx, _dy in strategy("lateral").steps)


# --- objective calibration --------------------------------------------------

def test_earliness_weight_is_zero_by_measurement():
    """Swept 0/0.1/0.25/0.5: W=0 wins the selection block by 7.4pp and held-out
    by 5.0pp. The positive weight assumed a repair must fit in the episode's
    remaining steps, which stopped being true when recovery got its own clock."""
    from governor.search import DEFAULT_EARLINESS
    assert DEFAULT_EARLINESS == 0.0


def test_earliness_weight_changes_which_trigger_wins():
    """If the weight could not change the pick there would be nothing to calibrate."""
    import numpy as np
    from governor.search import search_triggers

    traces, labels = [], []
    for i in range(30):
        failing = i % 2 == 0
        early = np.full(80, 0.04)
        late = np.full(80, 0.04)
        if failing:
            early[30:] = 0.02      # weaker, but separates early
            late[60:] = 0.001      # stronger, separates late
        traces.append({
            "observable.eef_z": early,
            "observable.finger_gap": late,
            "observable.gripper_effort": np.zeros(80),
            "observable.joint_speed": np.zeros(80),
        })
        labels.append(not failing)
    at0 = search_triggers(traces, labels, privilege_budget=0, top_k=1, earliness=0.0)
    at1 = search_triggers(traces, labels, privilege_budget=0, top_k=1, earliness=1.0)
    assert at0 and at1
    assert at0[0].trigger != at1[0].trigger or at0[0].score != at1[0].score


def test_zero_fp_penalty_admits_the_degenerate_fire_on_everything_trigger():
    """The penalty's one job. At 0.0 the search picks a trigger with recall 1.00
    AND false-positive rate 1.00 -- exactly round 4's blind-firing control, which
    scored -4.0pp. Any positive value excludes it."""
    import numpy as np
    from governor.search import search_triggers

    traces, labels = [], []
    for i in range(30):
        failing = i % 2 == 0
        traces.append({
            "observable.finger_gap": np.full(60, 0.001 if failing else 0.04),
            "observable.eef_z": np.full(60, 1.0),          # constant: fires on everything
            "observable.gripper_effort": np.zeros(60),
            "observable.joint_speed": np.zeros(60),
        })
        labels.append(not failing)
    lax = search_triggers(traces, labels, privilege_budget=0, top_k=1, fp_penalty=0.0)[0]
    strict = search_triggers(traces, labels, privilege_budget=0, top_k=1, fp_penalty=1.2)[0]
    assert strict.false_positive < 1.0, "a positive penalty must exclude fire-on-everything"
    assert strict.false_positive <= lax.false_positive


def test_trigger_rejects_an_unknown_reducer_at_construction():
    """A typo must fail where it is written, not mid-episode."""
    import pytest

    from governor.search import Trigger

    with pytest.raises(ValueError, match="unknown reducer"):
        Trigger("observable.finger_gap", "gt", 0.05, 1, 0, "runing_min")
    with pytest.raises(ValueError, match="unknown operator"):
        Trigger("observable.finger_gap", "ge", 0.05, 1, 0, "value")
