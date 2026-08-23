"""Adversarial tests for the two runtime invariants.

GOAL.md acceptance #4 requires that the reconstruction invariant actually blows
up: a test must deliberately violate it and be caught. `test_injection_is_caught`
is that test, and `test_cached_digest_would_not_catch_injection` documents why
the naive formulation fails, so the mechanism cannot be quietly reverted.
"""
import numpy as np
import pytest

from harness.features import REGISTRY
from harness.invariant import (
    InvariantViolation,
    assert_privilege_budget,
    assert_view_reconstructable,
    record_view,
)
from harness.percept import PrivilegePolicy, project

OBS = {
    "robot0_gripper_qpos": np.array([0.02, -0.02]),
    "robot0_eef_pos": np.array([0.0, 0.0, 1.0]),
    "robot0_joint_vel": np.zeros(7),
    "robot0_gripper_qvel": np.zeros(2),
    "cube_pos": np.array([0.0, 0.0, 0.83]),
    "gripper_to_cube_pos": np.array([0.01, 0.0, 0.1]),
}


def zero_budget_view(step=7, episode="ep-test"):
    return project(OBS, PrivilegePolicy(critic_budget=0).critic_names(), step=step, episode=episode)


# --- I1: critic-visible IFF logged ------------------------------------------

def test_untampered_view_passes():
    v = zero_budget_view()
    assert_view_reconstructable(v, record_view(v))


def test_injection_is_caught():
    """A wrapper that adds a feature AFTER the view was logged must be rejected."""
    v = zero_budget_view()
    logged = record_view(v)
    v._values["privileged.object_z"] = 0.83      # the injection
    with pytest.raises(InvariantViolation, match="diverges from the durable log"):
        assert_view_reconstructable(v, logged)


def test_value_tampering_is_caught():
    v = zero_budget_view()
    logged = record_view(v)
    v._values["observable.finger_gap"] = 999.0
    with pytest.raises(InvariantViolation):
        assert_view_reconstructable(v, logged)


def test_cached_digest_would_not_catch_injection():
    """Why digest() is a method: the cached formulation is f(x) == f(x)."""
    v = zero_budget_view()
    cached = v.digest()                         # what a naive implementation stores
    v._values["privileged.object_z"] = 0.83
    assert cached != v.digest(), "live digest must move when contents move"
    # the naive check compares the cached value to itself and passes:
    assert cached == cached  # noqa: PLR0124  the self-comparison IS the point


# --- attestation: Mapping, not dict -----------------------------------------

def test_plain_getitem_attests():
    v = zero_budget_view()
    _ = v["observable.finger_gap"]
    assert v.reads == frozenset({"observable.finger_gap"})


@pytest.mark.parametrize("accessor", [
    lambda v: v.get("observable.finger_gap"),
    lambda v: dict(v),
    lambda v: {**v},
    lambda v: list(v.values()),
    lambda v: list(v.items()),
])
def test_bulk_accessors_cannot_hide_a_read(accessor):
    """A dict subclass serves these in C and never reaches __getitem__."""
    v = zero_budget_view()
    accessor(v)
    assert v.reads, "an accessor served a value without attesting it"


def test_missing_key_is_not_a_read():
    v = zero_budget_view()
    with pytest.raises(KeyError):
        v["observable.finger_gap_typo"]
    assert v.reads == frozenset()


# --- I2: privilege budget ---------------------------------------------------

def test_zero_budget_view_cannot_even_see_privileged_features():
    v = zero_budget_view()
    assert not any(n.startswith("privileged.") for n in v)
    with pytest.raises(KeyError):
        v["privileged.object_z"]


def test_privileged_read_over_budget_is_caught():
    v = project(OBS, PrivilegePolicy(critic_budget=1).critic_names(), step=1, episode="e")
    _ = v["privileged.object_z"]
    with pytest.raises(InvariantViolation, match="privilege budget"):
        assert_privilege_budget(v, budget=0, role="critic")


def test_bulk_read_over_attests_toward_safety():
    """{**view} charges for everything: a candidate is never undercharged."""
    v = project(OBS, PrivilegePolicy(critic_budget=1).critic_names(), step=1, episode="e")
    _ = {**v}
    with pytest.raises(InvariantViolation):
        assert_privilege_budget(v, budget=0, role="critic")


# --- worker-process containment ---------------------------------------------

def _worker_probe(budget):
    """Runs in a fresh process.

    Round 96 (R2) made the feature catalog base-owned (harness.feature_catalog,
    populated on harness import), retiring the round-69 inversion where the
    registry started EMPTY until a card import happened to run. A spawn worker
    now gets the privileged extractors from ``import harness`` alone -- no card,
    no provider ref -- so containment, not registry emptiness, is the boundary:
    the extractors DO exist in every worker; the VIEW is what withholds them.
    """
    import numpy as np

    from harness.percept import PrivilegePolicy, project
    obs = {
        "robot0_gripper_qpos": np.array([0.02, -0.02]),
        "robot0_eef_pos": np.array([0.0, 0.0, 1.0]),
        "robot0_joint_vel": np.zeros(7),
        "robot0_gripper_qvel": np.zeros(2),
        "cube_pos": np.array([0.0, 0.0, 0.83]),
        "gripper_to_cube_pos": np.array([0.01, 0.0, 0.1]),
    }
    v = project(obs, PrivilegePolicy(critic_budget=budget).critic_names(), step=0, episode="w")
    return sorted(REGISTRY), sorted(v)


def test_containment_holds_in_a_fresh_worker_process():
    """A fresh worker imports harness, so the base catalog's privileged extractors
    exist there no matter what the parent did. Containment is what makes that
    harmless: the VIEW, not the registry, is the boundary."""
    from multiprocessing import Pool
    with Pool(1) as pool:
        registry, view_keys = pool.apply(_worker_probe, (0,))
    assert any(n.startswith("privileged.") for n in registry), (
        "precondition: the worker registry does contain privileged extractors"
    )
    assert not any(n.startswith("privileged.") for n in view_keys), (
        "containment breached: a zero-budget view exposed a privileged feature in a worker"
    )


def test_a_blind_twin_fires_the_moment_it_arms():
    """The judgement gate depends on the twin actually being unconditional."""
    import numpy as np

    from plugins.rsi.campaign import _ALWAYS
    from plugins.rsi.stats.search import Trigger

    twin = Trigger("observable.finger_gap", "gt", -_ALWAYS, 1, 5, "value")
    trace = {"observable.finger_gap": np.zeros(20)}
    assert twin.fire_step(trace) == 5, "the blind twin did not fire at its arm step"
    trace_neg = {"observable.finger_gap": np.full(20, -1e6)}
    assert twin.fire_step(trace_neg) == 5, "a large negative value defeated the twin"


def test_a_blind_bundle_mirrors_every_rule_it_twins():
    """The held-out judgement check twins the WHOLE chain, not just its head."""
    from plugins.rsi.campaign import _ALWAYS
    from plugins.rsi.governed import Bundle, RecoverySpec, Rule
    from plugins.rsi.stats.search import Trigger

    rules = tuple(Rule(f"g{i}", Trigger("observable.finger_gap", "gt", 0.05, 1, 10 * i, "value"),
                       RecoverySpec(sensor_sd=0.01)) for i in (1, 2))
    bundle = Bundle(rules=rules, critic_budget=0, action_budget=0)
    blind = Bundle(rules=tuple(Rule(f"{r.rule_id}-blind",
                                    Trigger(r.trigger.feature, "gt", -_ALWAYS, 1,
                                            r.trigger.arm_after, "value"), r.recovery)
                               for r in bundle.rules),
                   critic_budget=0, action_budget=0)
    assert len(blind.rules) == len(bundle.rules)
    for a, b in zip(bundle.rules, blind.rules):
        assert a.trigger.arm_after == b.trigger.arm_after, "the twin moved the arm step"
        assert a.recovery == b.recovery, "the twin changed the recovery"


def test_blind_twin_matches_the_inline_construction():
    """`blind_twin` is now the ONE constructor both campaign and beam gate
    against, so it must reproduce the round-45 inline shape field for field --
    any drift here moves every sealed blind_gate/heldout_vs_blind number."""
    from plugins.rsi.campaign import _ALWAYS, blind_twin
    from plugins.rsi.governed import RecoverySpec, Rule
    from plugins.rsi.stats.search import Trigger

    rule = Rule("g3", Trigger("observable.finger_gap", "lt", 0.0018, 3, 12, "min"),
                RecoverySpec(sensor_sd=0.015))
    twin = blind_twin(rule)
    hand = Rule("g3-blind",
                Trigger(rule.trigger.feature, "gt", -_ALWAYS, 1,
                        rule.trigger.arm_after, "value"),
                rule.recovery)
    assert twin.canonical() == hand.canonical(), "blind_twin drifted from the inline form"
    assert twin.rule_id == "g3-blind"
    assert twin.recovery is rule.recovery, "the twin must carry the recovery unchanged"
    trace = {"observable.finger_gap": np.zeros(30)}
    assert twin.trigger.fire_step(trace) == 12, "the twin is no longer unconditional"
