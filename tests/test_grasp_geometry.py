"""The grasp geometry chain: pinhole backprojection cross-checked against
privileged ground truth in sim, plus the vendored client's fail-closed
validation exercised through an injected fake transport (no ZMQ, no server).

The sim-render test is the one that makes the rung real (local-archive/docs/retired-from-public/anygrasp-scout.md
risk #2): a wrong depth scale or a wrong image-flip / frame conversion still
yields a "reasonable"-looking cloud, so it is checked against the obs cube_pos
and the known table height, not eyeballed.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

# Exercises the embodiment card's grasp geometry; skip when it is unplugged (R2).
pytest.importorskip("plugins.embodiment_robosuite.grasp")

from plugins.embodiment_robosuite.grasp import FRAME, backproject, scene_cloud
from plugins.embodiment_robosuite.grasp_client import (
    GRASP_CONVENTION,
    PROTOCOL_VERSION,
    GraspInferenceClient,
    GraspInferenceConfig,
    GraspInferenceProtocolError,
    GraspInferenceUnavailable,
)

# Prefer a headless GL backend so the offscreen render works under the plain
# `pytest tests/` command (no MUJOCO_GL in the env); harmless if already set.
# Safe here: nothing above creates a GL context (grasp lazy-imports robosuite).
os.environ.setdefault("MUJOCO_GL", "egl")


# --- offline: vendored client fail-closed validation via fake transport ------

class _FakeTransport:
    """Records calls and replays a canned response (or raises) per operation."""

    def __init__(self, response):
        self.response = response
        self.calls: list[tuple] = []

    def request(self, operation, payload, *, timeout_s):
        self.calls.append((operation, dict(payload), timeout_s))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _valid_grasps(n=2):
    g = np.zeros((n, 4, 4))
    for i in range(n):
        g[i, :3, :3] = np.eye(3)
        g[i, :3, 3] = (0.1 * i, 0.0, 0.5)
        g[i, 3, 3] = 1.0
    return g


def _ok_infer(frame=FRAME, n=2):
    return {
        "protocol": PROTOCOL_VERSION,
        "status": "ok",
        "provider": "learned",
        "model": "anygrasp",
        "model_version": "1.0",
        "frame": frame,
        "convention": GRASP_CONVENTION,
        "grasps": _valid_grasps(n),
        "scores": np.linspace(0.1, 0.9, n),
        "widths": np.full(n, 0.05),
    }


def _client(response, *, max_grasps=128):
    cfg = GraspInferenceConfig(provider="learned", endpoint="ipc://test", max_grasps=max_grasps)
    transport = _FakeTransport(response)
    client = GraspInferenceClient(cfg, transport=transport, monotonic_fn=lambda: 0.0)
    return client, transport


_GOOD_POINTS = np.array([[0.0, 0.0, 0.5], [0.02, 0.0, 0.5]], dtype=np.float32)
_GOOD_BOUNDS = np.array([[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]], dtype=np.float32)


def test_infer_happy_path_returns_validated_result():
    client, transport = _client(_ok_infer(n=3))
    result = client.infer(object_points=_GOOD_POINTS, scene_bounds=_GOOD_BOUNDS, frame=FRAME)
    assert result.grasps.shape == (3, 4, 4)
    assert result.frame == FRAME and result.convention == GRASP_CONVENTION
    assert transport.calls and transport.calls[0][0] == "infer"


def _mut(**changes):
    r = _ok_infer()
    r.update(changes)
    return r


def _reflect():
    g = _valid_grasps(2)
    g[0, :3, :3] = np.diag([1.0, 1.0, -1.0])  # orthonormal but det = -1
    return _mut(grasps=g)


def _non_orthonormal():
    g = _valid_grasps(2)
    g[0, :3, :3] = 2.0 * np.eye(3)
    return _mut(grasps=g)


def _bad_bottom_row():
    g = _valid_grasps(2)
    g[0, 3, :] = (1.0, 0.0, 0.0, 1.0)
    return _mut(grasps=g)


@pytest.mark.parametrize("response, match", [
    ({**_ok_infer(), "object_pose": [1, 2, 3]}, "forbidden pose fields"),
    (_mut(protocol="other.v9"), "expected protocol"),
    (_mut(frame="wrong_frame"), "expected observation frame"),
    (_mut(convention="x_y_z"), "convention"),
    (_mut(provider="someone_else"), "answered as"),
    (_reflect(), "left-handed reflections"),
    (_non_orthonormal(), "not orthonormal"),
    (_bad_bottom_row(), "homogeneous bottom rows"),
    (_mut(scores=np.array([1.5, 0.2])), "scores must be normalized"),
    (_mut(widths=np.array([0.0, 0.05])), "widths must be positive"),
    (_mut(grasps=np.zeros((2, 4, 3))), "grasps must have shape"),
    (_mut(grasps=np.zeros((0, 4, 4))), "no grasp candidates"),
    (_mut(scores=np.array([0.1, 0.2, 0.3])), "align one-to-one"),
])
def test_infer_fail_closed_on_bad_response(response, match):
    client, _ = _client(response)
    with pytest.raises(GraspInferenceProtocolError, match=match):
        client.infer(object_points=_GOOD_POINTS, scene_bounds=_GOOD_BOUNDS, frame=FRAME)


def test_infer_server_reported_failure_is_unavailable():
    client, _ = _client(_mut(status="error", error="boom"))
    with pytest.raises(GraspInferenceUnavailable, match="boom"):
        client.infer(object_points=_GOOD_POINTS, scene_bounds=_GOOD_BOUNDS, frame=FRAME)


def test_infer_rejects_too_many_grasps():
    client, _ = _client(_ok_infer(n=5), max_grasps=2)
    with pytest.raises(GraspInferenceProtocolError, match="at most 2"):
        client.infer(object_points=_GOOD_POINTS, scene_bounds=_GOOD_BOUNDS, frame=FRAME)


@pytest.mark.parametrize("points, bounds, match", [
    (np.array([[9.0, 0.0, 0.5]]), _GOOD_BOUNDS, "outside scene_bounds"),
    (np.array([1.0, 2.0, 3.0]), _GOOD_BOUNDS, r"shape \(N, 3\)"),
    (_GOOD_POINTS, np.array([[1.0, 1.0, 1.0], [-1.0, -1.0, -1.0]]), "minima must be below maxima"),
    (np.array([[np.nan, 0.0, 0.5]]), _GOOD_BOUNDS, "non-finite"),
])
def test_infer_request_validation_never_hits_transport(points, bounds, match):
    client, transport = _client(_ok_infer())
    with pytest.raises(GraspInferenceProtocolError, match=match):
        client.infer(object_points=points, scene_bounds=bounds, frame=FRAME)
    assert transport.calls == []  # fail-closed before any wire traffic


def test_empty_frame_rejected_before_transport():
    client, transport = _client(_ok_infer())
    with pytest.raises(GraspInferenceProtocolError, match="frame must be a non-empty string"):
        client.infer(object_points=_GOOD_POINTS, scene_bounds=_GOOD_BOUNDS, frame="  ")
    assert transport.calls == []


@pytest.mark.parametrize("kwargs, match", [
    ({"provider": " "}, "provider must be non-empty"),
    ({"endpoint": ""}, "endpoint must be non-empty"),
    ({"timeout_s": 0.0}, "timeout_s must be finite and positive"),
    ({"max_grasps": 0}, "max_grasps must be positive"),
    ({"convention": "bogus"}, "unsupported grasp convention"),
])
def test_config_validation(kwargs, match):
    base = {"provider": "learned", "endpoint": "ipc://x"}
    with pytest.raises(ValueError, match=match):
        GraspInferenceConfig(**{**base, **kwargs})


# --- offline: pure pinhole backprojection against synthetic depth -------------

def test_backproject_synthetic_pinhole_is_exact():
    h = w = 100
    fx = fy = 100.0
    cx = cy = 50.0
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
    depth = np.full((h, w), 2.0)  # flat plane 2 m in front, meters
    cloud = backproject(depth, K, np.eye(4), margin=0.1)
    pts = cloud.points.reshape(h, w, 3)
    # principal-point pixel projects onto the optical axis; z is exactly depth.
    assert np.allclose(pts[50, 50], (0.0, 0.0, 2.0), atol=1e-6)
    # +25 px right / +25 px down -> +0.5 m x / +0.5 m y at 2 m and f = 100.
    assert np.allclose(pts[50, 75], (0.5, 0.0, 2.0), atol=1e-6)
    assert np.allclose(pts[75, 50], (0.0, 0.5, 2.0), atol=1e-6)
    assert cloud.points.dtype == np.float32
    assert np.allclose(cloud.bounds[0], cloud.points.min(0) - 0.1)
    assert np.allclose(cloud.bounds[1], cloud.points.max(0) + 0.1)


def test_backproject_round_trip_through_intrinsics():
    K = np.array([[120.0, 0, 64.0], [0, 120.0, 48.0], [0, 0, 1]])
    depth = np.zeros((96, 128))
    depth[30, 90] = 1.7  # one lit pixel
    cloud = backproject(depth, K, np.eye(4))
    p = cloud.points.reshape(96, 128, 3)[30, 90]
    # reproject p back to pixel coords via K and recover (u, v) = (90, 30).
    u = K[0, 0] * p[0] / p[2] + K[0, 2]
    v = K[1, 1] * p[1] / p[2] + K[1, 2]
    assert np.allclose((u, v), (90.0, 30.0), atol=1e-4)


# --- sim: cross-check the full chain against privileged ground truth ----------

def _camera_reset(seed=90100):
    """Build the camera env headless and return (env, obs, sim) or skip."""
    try:
        from harness.spec import EpisodeSpec
        from plugins.embodiment_robosuite.env import camera_make_env
        from plugins.embodiment_robosuite.grasp import _camera_utils

        _camera_utils()  # ensure robosuite.utils.camera_utils imports (stubs h5py)
        env = camera_make_env(EpisodeSpec(seed=seed, task="lift", arm_noise=0.0))
        obs = env.reset()
    except Exception as error:  # noqa: BLE001 -- any render/GL failure -> skip, not fail
        pytest.skip(f"camera env unavailable: {type(error).__name__}: {error}")
    return env, obs, env.sim


def _world(cloud):
    homog = np.c_[cloud.points, np.ones(len(cloud.points))]
    return (cloud.world_from_cam @ homog.T).T[:, :3]


def test_scene_cloud_matches_privileged_ground_truth():
    env, obs, sim = _camera_reset()
    try:
        from plugins.embodiment_robosuite.grasp import _camera_utils

        cu = _camera_utils()
        cloud = scene_cloud(obs, sim)
        world = _world(cloud)
        h, w = np.asarray(obs["agentview_depth"]).shape[:2]

        # cloud is in the OPTICAL frame (all points in front of the camera).
        assert np.all(cloud.points[:, 2] > 0.0)
        assert cloud.points.shape == (h * w, 3)

        # segmentation (row 0 = top) aligns with the flipped depth raster.
        seg = cu.get_camera_segmentation(sim, "agentview", h, w)[..., 1].reshape(-1)
        cube_geoms = [sim.model.geom_name2id(g) for g in env.cube.contact_geoms]
        cube_geoms += [sim.model.geom_name2id(g) for g in getattr(env.cube, "visual_geoms", [])]
        cube_mask = np.isin(seg, cube_geoms)
        assert cube_mask.sum() > 50, "cube must be visible to the camera"

        # frame check: world centroid of the cube's pixels lands on obs cube_pos.
        centroid = world[cube_mask].mean(0)
        cube_pos = np.asarray(obs["cube_pos"])
        err = float(np.linalg.norm(centroid - cube_pos))
        assert err < 0.03, f"cube centroid {centroid} vs gt {cube_pos}: {err:.4f} m off"

        # metric check: the table plane is flat at its known height. A wrong
        # depth scale or a wrong image flip fails this (flip error -> ~0.72 m
        # z-spread instead of a few mm), which the near-center cube alone cannot.
        table_z = float(env.model.mujoco_arena.table_offset[2])
        table_geoms = [
            g for g in range(sim.model.ngeom)
            if (sim.model.geom_id2name(g) or "").startswith("table")
        ]
        table_mask = np.isin(seg, table_geoms)
        table_world_z = world[table_mask][:, 2]
        assert abs(table_world_z.mean() - table_z) < 0.02, (
            f"table plane at {table_world_z.mean():.4f} m, expected {table_z:.4f} m"
        )
        assert table_world_z.std() < 0.01, (
            f"table plane not flat (std {table_world_z.std():.4f} m): frame/flip error"
        )
    finally:
        env.close()


def test_zmq_transport_unavailable_without_optional_deps():
    """The default transport fails closed (not ImportError) when zmq is absent."""
    from plugins.embodiment_robosuite.grasp_client import ZmqMsgpackTransport

    def _no_deps(name):
        raise ModuleNotFoundError(name)

    transport = ZmqMsgpackTransport("ipc://x", importer=_no_deps)
    with pytest.raises(GraspInferenceUnavailable, match="pyzmq"):
        transport.request("infer", {}, timeout_s=1.0)
