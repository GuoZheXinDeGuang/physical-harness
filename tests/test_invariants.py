"""Adversarial tests for the two runtime invariants.

GOAL.md acceptance #4 requires that the reconstruction invariant actually blows
up: a test must deliberately violate it and be caught. `test_injection_is_caught`
is that test, and `test_cached_digest_would_not_catch_injection` documents why
the naive formulation fails, so the mechanism cannot be quietly reverted.
"""
import numpy as np
import pytest

from governor.features import REGISTRY
from governor.invariant import (
    InvariantViolation, assert_privilege_budget, assert_view_reconstructable, record_view,
)
from governor.percept import FeatureView, PrivilegePolicy, project

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
    v._values["privileged.cube_z"] = 0.83      # the injection
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
    v._values["privileged.cube_z"] = 0.83
    assert cached != v.digest(), "live digest must move when contents move"
    # the naive check compares the cached value to itself and passes:
    assert cached == cached


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
        v["privileged.cube_z"]


def test_privileged_read_over_budget_is_caught():
    v = project(OBS, PrivilegePolicy(critic_budget=1).critic_names(), step=1, episode="e")
    _ = v["privileged.cube_z"]
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
    """Runs in a fresh process: module-level register() re-runs on import."""
    import numpy as np
    from governor.features import REGISTRY
    from governor.percept import PrivilegePolicy, project
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
    """A worker re-imports governor.features, so module-level register() re-runs and
    the privileged extractors exist there no matter what the parent did. Containment
    is what makes that harmless: the VIEW, not the registry, is the boundary."""
    from multiprocessing import Pool
    with Pool(1) as pool:
        registry, view_keys = pool.apply(_worker_probe, (0,))
    assert any(n.startswith("privileged.") for n in registry), (
        "precondition: the worker registry does contain privileged extractors"
    )
    assert not any(n.startswith("privileged.") for n in view_keys), (
        "containment breached: a zero-budget view exposed a privileged feature in a worker"
    )
