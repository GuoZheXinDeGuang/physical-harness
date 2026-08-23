"""Round 93 rung 2: the geometric-grasp driver retargeting and the probe's pure
helpers.

The driver tests use FAKE observations only -- no sim -- to prove that
GraspPoseDriver is ScriptedDriver retargeted: it drives the identical four-phase
vocabulary toward a supplied grasp position while IGNORING the privileged object
percept obs carries. The perception helper is checked on a synthetic shell cloud
that isolates the rung-2 de-bias (the single-view centroid z is high; the grasp z
must be the table/top midpoint).
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.spec import NOMINAL_SCHEDULE, PHASE_HEIGHT, EpisodeSpec

# Needs the embodiment card's grasp client; skip when it is unplugged (R2).
pytest.importorskip("plugins.embodiment_robosuite.grasp_client")

from plugins.embodiment_robosuite.grasp_client import GraspInferenceError
from plugins.policies.drivers import GraspPose, GraspPoseDriver, ScriptedDriver
from scripts.probe_geometric_grasp import (
    classify_failure,
    grasp_pose_from_cloud,
    summarize,
    workspace_bounds,
)


def _obs(eef, cube=(5.0, 5.0, 5.0)):
    """A fake obs: cube_pos is the PRIVILEGED percept the geometric driver must
    ignore; robot0_eef_pos is what FrozenPolicy.act servos from."""
    return {"robot0_eef_pos": np.asarray(eef, float), "cube_pos": np.asarray(cube, float)}


# --- driver retargeting (fakes) ----------------------------------------------

def test_observe_once_locks_onto_grasp_not_privileged_percept():
    grasp = GraspPose(position=np.array([0.10, 0.20, 0.83]), yaw=0.3, width=0.05)
    d = GraspPoseDriver(EpisodeSpec(seed=1, task="lift"), grasp)
    target = d.observe_once(_obs([0, 0, 0], cube=[5, 5, 5]))
    assert np.allclose(target, [0.10, 0.20, 0.83])          # the grasp, not the cube
    assert np.allclose(d.inner.target, [0.10, 0.20, 0.83])
    # a copy, so mutating the returned target does not reach back into the pose
    target[0] = 99.0
    assert d.grasp.position[0] == 0.10


def test_act_drives_toward_grasp_position_through_phases():
    grasp = GraspPose(position=np.array([0.10, 0.20, 0.83]))
    spec = EpisodeSpec(seed=1, task="lift")
    d = GraspPoseDriver(spec, grasp)
    d.observe_once(_obs([0, 0, 0]))
    # step 0 -> phase "above": goal is above the grasp, eef starts far below/aside
    a = d.act(_obs([0.10, 0.20, 0.0]))
    assert a.shape == (7,)
    assert a[2] > 0.0                    # servo upward toward grasp_z + above height
    assert a[6] == -1.0                  # gripper open while approaching
    assert d.k == 1
    # at the grasp xy and correct height for "above", the xy deltas vanish
    goal_z = 0.83 + PHASE_HEIGHT["above"]
    a2 = d.act(_obs([0.10, 0.20, goal_z]))
    assert abs(a2[0]) < 1e-9 and abs(a2[1]) < 1e-9


def test_gripper_closes_in_close_and_lift_phases():
    d = GraspPoseDriver(EpisodeSpec(seed=1, task="lift"),
                        GraspPose(position=np.array([0.0, 0.0, 0.83])))
    d.observe_once(_obs([0, 0, 0]))
    # NOMINAL_SCHEDULE = above25, descend25, close12, lift38
    d.k = 50                              # first "close" step
    assert d.act(_obs([0, 0, 0]))[6] == 1.0
    d.k = 70                              # within "lift"
    assert d.act(_obs([0, 0, 0]))[6] == 1.0
    d.k = 10                              # "above"
    assert d.act(_obs([0, 0, 0]))[6] == -1.0


def test_exhausted_and_identity():
    d = GraspPoseDriver(EpisodeSpec(seed=1, task="lift"),
                        GraspPose(position=np.zeros(3)))
    total = sum(dur for _, dur in NOMINAL_SCHEDULE)
    d.k = total - 1
    assert not d.exhausted
    d.k = total
    assert d.exhausted
    assert d.identity == "grasp_pose@v1"


def test_retarget_and_handback_inherited():
    d = GraspPoseDriver(EpisodeSpec(seed=1, task="lift"),
                        GraspPose(position=np.zeros(3)))
    d.observe_once(_obs([0, 0, 0]))
    d.retarget(np.array([1.0, 2.0, 3.0]))
    assert np.allclose(d.inner.target, [1.0, 2.0, 3.0])
    d.k = 10                              # mid "above" (0..25)
    d.on_handback()
    assert d.k == 25                      # supersedes the interrupted phase


def test_geometric_driver_matches_scripted_when_given_the_privileged_pose():
    """The retargeting is a pure target swap: fed the exact privileged percept a
    ScriptedDriver would read (noise-free), the two emit identical actions."""
    spec = EpisodeSpec(seed=7, task="lift", percept_noise=0.0)
    cube = np.array([0.1, -0.2, 0.83])
    scripted = ScriptedDriver(spec)
    scripted.observe_once(_obs([0, 0, 0], cube=cube))
    geo = GraspPoseDriver(spec, GraspPose(position=cube.copy()))
    geo.observe_once(_obs([0, 0, 0], cube=cube))
    for eef in ([0.0, 0.0, 0.0], [0.1, -0.2, 0.9], [0.05, 0.05, 0.85]):
        assert np.allclose(scripted.act(_obs(eef, cube=cube)),
                           geo.act(_obs(eef, cube=cube)))


# --- perception assembly (synthetic cloud) -----------------------------------

def _shell_cloud():
    """A single-view-like cloud: a dense top plate at z=0.85 plus a sparse strip
    down to z=0.81. Its centroid z is high (top-dominated); the object centre by
    observed geometry is (table_z + top_z) / 2."""
    xs = np.linspace(-0.02, 0.02, 9)
    ys = np.linspace(-0.015, 0.015, 7)
    plate = np.array([[x, y, 0.85] for x in xs for y in ys])       # top footprint
    strip = np.array([[0.02, 0.0, z] for z in np.linspace(0.81, 0.85, 5)])
    return np.vstack([plate, strip])


def test_grasp_pose_from_cloud_debiases_z_to_table_top_midpoint():
    cloud = _shell_cloud()
    table_z = 0.80
    pose = grasp_pose_from_cloud(cloud, table_z, bounds=None)
    raw_centroid_z = float(cloud[:, 2].mean())
    top_z = float(cloud[:, 2].max())
    assert pose.position[2] == pytest.approx(0.5 * (table_z + top_z))   # 0.825
    assert pose.position[2] < raw_centroid_z - 0.005                    # actually de-biased
    assert abs(pose.position[0]) < 0.01 and abs(pose.position[1]) < 0.01  # xy on the plate
    assert pose.width > 0.0
    assert np.isfinite(pose.yaw)


def test_grasp_pose_from_cloud_fails_closed_on_degenerate_cloud():
    # nothing above the table -> empty segment -> the client raises; the probe
    # catches GraspInferenceError (the base) and records a proposal failure.
    with pytest.raises(GraspInferenceError):
        grasp_pose_from_cloud(np.zeros((3, 3)), table_z=0.80, bounds=None)


# --- pure probe helpers ------------------------------------------------------

def test_workspace_bounds_centres_on_table_not_object():
    b = workspace_bounds(np.array([0.5, -0.1, 0.8]), 0.80, half_extent=0.20, height=0.15)
    assert b.shape == (2, 3)
    assert np.allclose(b[0], [0.30, -0.30, 0.80])
    assert np.allclose(b[1], [0.70, 0.10, 0.95])


def test_classify_failure_splits_proposal_from_execution():
    assert classify_failure(0.05) == "proposal_wrong"     # aimed off the object
    assert classify_failure(0.01) == "execution_miss"     # good pose, missed anyway


def test_summarize_counts_rates_pairs_and_modes():
    records = [
        {"seed": 1, "a_success": True,  "b_success": True,  "pos_err": 0.010, "proposal_failed": False},
        {"seed": 2, "a_success": True,  "b_success": False, "pos_err": 0.008, "proposal_failed": False},  # exec miss
        {"seed": 3, "a_success": False, "b_success": True,  "pos_err": 0.009, "proposal_failed": False},  # B fixed
        {"seed": 4, "a_success": False, "b_success": False, "pos_err": 0.060, "proposal_failed": False},  # proposal wrong
        {"seed": 5, "a_success": True,  "b_success": False, "pos_err": None,  "proposal_failed": True},   # proposal failed
    ]
    s = summarize(records)
    assert s["n"] == 5
    assert s["arm_a_success"] == 3 and s["arm_b_success"] == 2
    assert s["b_over_a_fixed"] == 1 and s["a_over_b_broken"] == 2
    assert s["b_failure_modes"] == {"proposal_failed": 1, "proposal_wrong": 1, "execution_miss": 1}
    assert s["pos_err_m"]["n"] == 4        # the None (proposal_failed) is excluded
    assert 0.0 <= s["mcnemar_p"] <= 1.0
