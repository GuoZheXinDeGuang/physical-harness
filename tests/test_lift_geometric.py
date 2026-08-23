"""The geometric-grasp card (round 97): its from-scratch planner and the
GraspPoseDriver-on-Lift policy factory.

The planner + factory-wiring tests use FAKES only (no sim, base lane): the
planner is pure, and the factory's grasp source is a ref, monkeypatched to a
canned pose so the wiring (dict -> GraspPose -> GraspPoseDriver) is checked
without spinning an env. One robosuite-marked test drives the whole card seam on
a real camera env.
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.spec import EpisodeSpec
from plugins.policies.drivers import GraspPoseDriver
from plugins.skill_geometric_grasp.planner import CATALOGUE, ORACLES, provider
from plugins.task.validate import validate_plan

# --- planner (pure) ----------------------------------------------------------

def test_planner_emits_a_valid_single_node_lift_plan():
    plan = provider().plan({"task": "lift_geometric"})
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert ok, msg                                      # admitted by the real validator
    assert [n["skill"] for n in plan["nodes"]] == ["grasp"]
    assert plan["verify"][0]["predicate"] == "lifted"


def test_planner_is_deterministic_and_task_scoped():
    p = provider()
    assert p.plan({"task": "lift_geometric"}) == p.plan({"task": "lift_geometric"})
    with pytest.raises(ValueError):
        p.plan({"task": "stack"})                       # only plans its own task


# --- policy factory (fake grasp source, no sim) ------------------------------

def test_make_driver_locks_onto_the_ref_resolved_grasp(monkeypatch):
    """make_driver resolves the grasp by ref and locks a GraspPoseDriver onto it,
    ignoring the privileged object percept -- the wiring, without a camera env."""
    from plugins import policies

    class _StubGrasper:
        def grasp_pose(self, spec):
            return {"position": [0.10, 0.20, 0.83], "yaw": 0.3, "width": 0.05}

    monkeypatch.setattr(policies, "load_provider", lambda ref: _StubGrasper())
    driver = policies.lift_geometric_provider().make_driver(
        EpisodeSpec(seed=1, task="lift"))
    assert isinstance(driver, GraspPoseDriver)
    target = driver.observe_once(
        {"robot0_eef_pos": np.zeros(3), "cube_pos": np.array([5.0, 5.0, 5.0])})
    assert np.allclose(target, [0.10, 0.20, 0.83])      # the grasp, not the cube
    assert driver.grasp.width == 0.05


# --- whole card seam on a real camera env ------------------------------------

@pytest.mark.robosuite
def test_lift_geometric_factory_builds_a_driver_from_a_real_camera_env():
    """End-to-end: the factory resolves the embodiment grasp provider by ref,
    computes a zero-privilege pose from a fresh camera env, and locks a
    GraspPoseDriver onto it -- the card's whole execution seam in one spawn."""
    from plugins import policies

    spec = EpisodeSpec(seed=90210, task="lift")          # scratch block, unburned
    driver = policies.lift_geometric_provider().make_driver(spec)
    assert isinstance(driver, GraspPoseDriver)
    assert driver.grasp.position.shape == (3,)
    assert np.all(np.isfinite(driver.grasp.position))
    assert driver.grasp.width > 0.0
    assert driver.grasp.position[2] > 0.5                # above the ground plane
