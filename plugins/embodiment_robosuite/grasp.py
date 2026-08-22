"""Robosuite depth -> point cloud for learned 6-DoF grasp inference.

`scene_cloud(obs, sim)` turns a camera_depths=True observation (see
`env.camera_make_env`) plus the live `env.sim` into an (N, 3) point cloud in the
OpenCV camera OPTICAL frame (x right, y down, z forward), in meters, together
with the 4x4 optical->world transform so poses the grasp backend returns in the
observation frame can be lifted back to world.

Three robosuite traps, all handled here (docs/anygrasp-scout.md risk #2 --
each returns "reasonable" garbage if skipped):

1. robosuite's depth obs is NORMALIZED [0, 1], not meters:
   `get_real_depth_map` converts it using the sim's near/far planes.
2. robosuite's default IMAGE_CONVENTION is "opengl", so obs row 0 is the image
   BOTTOM; the optical pinhole model wants row 0 = TOP, so depth is flipped
   `[::-1]` before backprojection. (Verified: without the flip the table plane
   comes out tilted with ~72cm z-spread instead of flat at its known height.)
3. MuJoCo's camera frame is -z forward / +y up; `get_camera_extrinsic_matrix`
   already folds the MuJoCo->OpenCV axis correction (diag(1,-1,-1)) into the
   pose it returns, so it IS the optical->world transform -- no extra flip.

Cross-checked in sim against privileged ground truth in
tests/test_grasp_geometry.py (cube-segment centroid ~1cm from obs cube_pos;
table plane flat at its known height).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from typing import NamedTuple

import numpy as np

CAMERA = "agentview"
#: Label attached to the cloud's frame; echoed back by the grasp backend's
#: `infer(frame=...)`. Poses come back in this frame -> lift with world_from_cam.
FRAME = "camera_optical"


class SceneCloud(NamedTuple):
    points: np.ndarray  # (N, 3) float32, camera optical frame, meters
    bounds: np.ndarray  # (2, 3) float32, optical-frame [min; max] +- margin
    world_from_cam: np.ndarray  # (4, 4), optical -> world homogeneous transform


def backproject(depth_m: np.ndarray, K: np.ndarray, world_from_cam: np.ndarray,
                margin: float = 0.05) -> SceneCloud:
    """Pinhole-backproject a metric depth image (row 0 = TOP) into a SceneCloud.

    Pure numpy, no robosuite: `depth_m` is (H, W) meters, `K` the 3x3 intrinsics,
    `world_from_cam` the 4x4 optical->world transform. Split out from
    `scene_cloud` so the pinhole math is unit-testable against synthetic depth.
    """
    h, w = depth_m.shape
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    us, vs = np.meshgrid(np.arange(w), np.arange(h))
    points = np.stack(
        ((us - cx) * depth_m / fx, (vs - cy) * depth_m / fy, depth_m), axis=-1
    ).reshape(-1, 3).astype(np.float32)
    bounds = np.stack((points.min(0) - margin, points.max(0) + margin)).astype(np.float32)
    return SceneCloud(points, bounds, np.asarray(world_from_cam))


def _camera_utils():
    """robosuite.utils.camera_utils imports h5py at top for dataset I/O we never
    touch; stub the module IF ABSENT so the mandated pinhole helpers still import
    on the harness's minimal (h5py-less) venv. find_spec (not `import h5py`)
    keeps the plugin's declared import surface honest and never clobbers a real
    h5py -- robosuite stays the only third-party dependency this reaches for."""
    if "h5py" not in sys.modules and importlib.util.find_spec("h5py") is None:
        sys.modules["h5py"] = types.ModuleType("h5py")
    from robosuite.utils import camera_utils

    return camera_utils


def scene_cloud(obs, sim, camera: str = CAMERA, margin: float = 0.05) -> SceneCloud:
    """obs (camera_depths=True) + env.sim -> optical-frame metric point cloud."""
    cu = _camera_utils()
    depth_m = cu.get_real_depth_map(sim, np.asarray(obs[f"{camera}_depth"]))
    depth_m = depth_m[::-1, :, 0]  # opengl row-0-bottom -> optical row-0-top
    K = cu.get_camera_intrinsic_matrix(sim, camera, *depth_m.shape)
    world_from_cam = cu.get_camera_extrinsic_matrix(sim, camera)
    return backproject(depth_m, K, world_from_cam, margin)
