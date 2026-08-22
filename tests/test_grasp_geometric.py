"""Geometric top-down grasp: exact offline geometry + a sim cross-check against
privileged cube ground truth, all funnelled through the vendored client's
fail-closed validation (so geometric poses obey the learned pose contract).

The sim test is the one that makes the rung real: on the angled agentview the
naive PCA-over-the-whole-cloud grasp is dominated by the side faces the camera
sees (a ~7cm minor extent at ~38deg -- viewing geometry, not the cube), so the
top-band footprint is checked against obs cube_pos, the cube's half-sizes, and
its yaw, not eyeballed.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from plugins.embodiment_robosuite.grasp_client import (
    GRASP_CONVENTION,
    GraspInferenceResult,
    GraspInferenceUnavailable,
)
from plugins.embodiment_robosuite.grasp_geometric import (
    CLEARANCE,
    propose,
    propose_geometric,
    segment_object,
)

# Headless GL so the sim render works under a plain `pytest tests/` (see rung 1).
os.environ.setdefault("MUJOCO_GL", "egl")


# --- offline: exact geometry on a synthetic box -------------------------------

def _solid_box(center, half, n=11):
    """Dense axis-aligned grid filling a box: centroid == center exactly, and
    the xy footprint's minor principal axis is the box's short horizontal side."""
    axes = [np.linspace(-h, h, n) + c for c, h in zip(center, half)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    return grid


def test_propose_geometric_box_exact_centroid_axes_width():
    center = np.array([0.5, -0.2, 0.9])
    half = np.array([0.04, 0.015, 0.03])  # short horizontal side is y
    resp = propose_geometric(_solid_box(center, half))

    assert resp["status"] == "ok"
    pose = resp["grasps"][0]
    R = pose[:3, :3]
    closing, binormal, approach = R[:, 0], R[:, 1], R[:, 2]

    assert np.allclose(pose[:3, 3], center, atol=1e-9)           # centroid
    assert np.allclose(approach, (0.0, 0.0, -1.0), atol=1e-12)   # top-down
    assert np.allclose(np.abs(closing), (0.0, 1.0, 0.0), atol=1e-9)  # minor = y
    # width = extent along the closing (minor) axis + clearance = 2*0.015 + slack
    assert resp["widths"][0] == pytest.approx(2 * half[1] + CLEARANCE, abs=1e-9)
    assert 0.0 <= resp["scores"][0] <= 1.0
    # a valid right-handed frame (the client re-checks this; assert it here too)
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-9)
    assert np.linalg.det(R) == pytest.approx(1.0, abs=1e-9)
    assert np.allclose(binormal, np.cross(approach, closing), atol=1e-12)


def test_propose_routes_box_through_client_validation():
    center = np.array([0.5, -0.2, 0.9])
    half = np.array([0.04, 0.015, 0.03])
    result = propose(_solid_box(center, half))

    assert isinstance(result, GraspInferenceResult)
    assert result.grasps.shape == (1, 4, 4)
    assert result.frame == "world" and result.convention == GRASP_CONVENTION
    assert result.provider == "geometric"
    # the client packs object_points to float32 on the wire, so exactness here is
    # float32, not float64 (the direct propose_geometric call above stays exact).
    assert np.allclose(result.grasps[0][:3, 3], center, atol=1e-6)
    assert result.widths[0] == pytest.approx(2 * half[1] + CLEARANCE, abs=1e-6)


@pytest.mark.parametrize("cloud, why", [
    (np.zeros((5, 3)), "too few points"),
    (np.zeros((0, 3)), "empty"),
    (np.c_[np.zeros(20), np.zeros(20), np.full(20, 0.9)], "no xy footprint"),
])
def test_propose_geometric_fails_closed_on_degenerate(cloud, why):
    resp = propose_geometric(cloud)
    assert resp["status"] == "error", why
    assert "grasps" not in resp  # never a fabricated pose


def test_propose_fail_closed_surfaces_as_unavailable():
    # a valid-shaped but too-sparse cloud clears request validation, then the
    # geometric backend reports failure -> the client raises, never returns.
    with pytest.raises(GraspInferenceUnavailable, match="degenerate cloud"):
        propose(np.zeros((5, 3)))


# --- offline: plane + bounds segmentation -------------------------------------

def test_segment_object_keeps_above_table_drops_rest():
    rng = np.random.default_rng(0)
    table_z = 0.80
    box = np.c_[rng.uniform(-0.02, 0.02, 100), rng.uniform(-0.02, 0.02, 100),
                np.full(100, 0.85)]                       # on the table, keep
    on_table = np.c_[rng.uniform(-1, 1, 50), rng.uniform(-1, 1, 50),
                     np.full(50, table_z)]                # at plane, drop
    below = np.c_[np.zeros(20), np.zeros(20), np.full(20, 0.78)]  # under, drop
    cloud = np.vstack([box, on_table, below])

    kept = segment_object(cloud, table_z, margin=0.01)
    assert len(kept) == 100
    assert np.all(kept[:, 2] > table_z + 0.01)

    # bounds also excludes an out-of-region stray above the table
    stray = np.array([[5.0, 5.0, 0.90]])
    bounds = np.array([[-1.0, -1.0, 0.0], [1.0, 1.0, 2.0]])
    kept_b = segment_object(np.vstack([box, stray]), table_z, margin=0.01, bounds=bounds)
    assert len(kept_b) == 100


# --- sim: cross-check the grasp against privileged cube ground truth -----------

def _cube_cloud(seed=90200):
    """Build the camera env headless, isolate the cube cloud via segmentation
    (privileged, ground-truth isolation -- like rung 1), return world points and
    the privileged truth to check against, or skip on any render/GL failure."""
    try:
        from harness.spec import EpisodeSpec
        from plugins.embodiment_robosuite.env import camera_make_env
        from plugins.embodiment_robosuite.grasp import _camera_utils, scene_cloud

        cu = _camera_utils()
        env = camera_make_env(EpisodeSpec(seed=seed, task="lift", arm_noise=0.0))
        obs = env.reset()
    except Exception as error:  # noqa: BLE001 -- render/GL failure -> skip, not fail
        pytest.skip(f"camera env unavailable: {type(error).__name__}: {error}")
    try:
        sim = env.sim
        cloud = scene_cloud(obs, sim)
        homog = np.c_[cloud.points, np.ones(len(cloud.points))]
        world = (cloud.world_from_cam @ homog.T).T[:, :3]
        h, w = np.asarray(obs["agentview_depth"]).shape[:2]
        seg = cu.get_camera_segmentation(sim, "agentview", h, w)[..., 1].reshape(-1)
        cube_geoms = [sim.model.geom_name2id(g) for g in env.cube.contact_geoms]
        cube_geoms += [sim.model.geom_name2id(g) for g in getattr(env.cube, "visual_geoms", [])]
        cube_world = world[np.isin(seg, cube_geoms)]
        table_z = float(env.model.mujoco_arena.table_offset[2])
        return {
            "cube_world": cube_world,
            "world": world,
            "cube_pos": np.asarray(obs["cube_pos"]),
            "cube_quat": np.asarray(obs["cube_quat"]),  # (x, y, z, w)
            "half": np.asarray(env.cube.size)[:2],
            "table_z": table_z,
        }
    finally:
        env.close()


def _yaw_from_quat(q):
    x, y, z, w = q
    return np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def test_geometric_grasp_matches_privileged_cube():
    g = _cube_cloud()
    assert len(g["cube_world"]) > 50, "cube must be visible to the camera"

    result = propose(g["cube_world"])
    best = int(np.argmax(result.scores))
    pose = result.grasps[best]

    # position: within 2cm of the privileged cube centre
    pos_err = float(np.linalg.norm(pose[:3, 3] - g["cube_pos"]))
    assert pos_err < 0.02, f"grasp {pose[:3, 3]} vs cube {g['cube_pos']}: {pos_err:.4f} m"

    # approach: essentially straight down (world -z)
    approach_dot = float(pose[:3, 2] @ np.array([0.0, 0.0, -1.0]))
    assert approach_dot > 0.95, f"approach not top-down: dot={approach_dot:.3f}"

    # width: opens past the cube and not absurdly wide
    cube_size = float(2 * g["half"].max())
    width = float(result.widths[best])
    assert cube_size <= width <= cube_size + 0.03, f"width {width:.4f} vs cube {cube_size:.4f}"

    # yaw: closing axis aligned to a cube face, mod 90deg (a cube is 4-fold
    # symmetric). This is what fails if the top-band fix is dropped and the
    # grasp swings to the ~38deg viewing-geometry axis instead.
    closing = pose[:3, 0]
    grasp_yaw = np.degrees(np.arctan2(closing[1], closing[0]))
    cube_yaw = np.degrees(_yaw_from_quat(g["cube_quat"]))
    d = abs(grasp_yaw - cube_yaw) % 90.0
    d = min(d, 90.0 - d)
    assert d < 20.0, f"closing axis {d:.1f}deg off the cube face (mod 90)"


def test_segment_object_runs_on_the_real_cloud():
    g = _cube_cloud()
    kept = segment_object(g["world"], g["table_z"], margin=0.01)
    assert len(kept) > 0
    assert np.all(kept[:, 2] > g["table_z"] + 0.01)
