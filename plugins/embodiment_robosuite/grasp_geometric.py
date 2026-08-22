"""Traditional geometric top-down grasp proposals, obeying the learned contract.

AnyGrasp is unavailable (license fingerprint drift), so the grasp backend falls
back to classical geometry: segment the object off the table, fit a top-down
parallel-jaw pose from its top-surface footprint, and grasp across the narrow
principal axis. There is deliberately NO new pose contract -- proposals are
handed back through the vendored ``GraspInferenceClient`` (grasp_client.py) as if
a model server produced them, so the identical fail-closed validation (poses
(N, 4, 4), scores [0, 1], widths > 0, orthonormal R with det +1, convention
``x_closing_y_binormal_z_approach``) guards geometric and learned poses alike.
The seam is the client's injectable transport: ``_GeometricTransport`` answers
the ``infer`` operation with a geometric proposal instead of ZMQ/msgpack.

Frame: this operates on WORLD points (grasp.scene_cloud lifts the optical cloud
with ``world_from_cam``); the returned pose is world too, approach = world -z.

Cross-checked in sim against privileged cube ground truth and offline on
synthetic clouds in tests/test_grasp_geometric.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from plugins.embodiment_robosuite.grasp_client import (
    GRASP_CONVENTION,
    PROTOCOL_VERSION,
    GraspInferenceClient,
    GraspInferenceConfig,
    GraspInferenceProtocolError,
    GraspInferenceResult,
    _array_from_wire,
)

PROVIDER = "geometric"
MODEL = "pca_topdown"
MODEL_VERSION = "1"

#: Top-down grasp cares about the object's TOP surface: on the angled agentview
#: the side faces the camera also sees inflate the xy footprint (measured: full
#: cloud gives a 7cm minor extent at 38deg -- viewing geometry, not the cube),
#: so orientation and width come from a thin band below the highest point.
#: ponytail: fixed metres, tuned for cm-scale table-top objects; make it a
#: fraction of the cloud's z-span if very tall or very flat objects show up.
TOP_BAND = 0.01
#: Added to the measured closing-axis extent so the fingers clear the object.
#: ponytail: constant slack; widen per-gripper if a real jaw needs more.
CLEARANCE = 0.015
#: Below this the cloud is too sparse to fit a stable pose -> fail closed.
MIN_POINTS = 10


def segment_object(
    cloud_world: np.ndarray,
    table_z: float,
    *,
    margin: float = 0.01,
    bounds: np.ndarray | None = None,
) -> np.ndarray:
    """Isolate graspable object points: above the table plane, within bounds.

    Plane + bounds, NOT get_camera_segmentation: geometric grasping's premise is
    working from observed geometry without object identity, and segmentation
    needs per-geom ground-truth ids -- privileged, and the same class of input
    the grasp backend is contractually forbidden from consuming. Anything
    resting above ``table_z + margin`` is a candidate from the cloud alone.
    ``table_z`` is passed, not fitted: the caller knows the arena height
    (env.model.mujoco_arena.table_offset[2]); RANSAC-ing the plane back out of
    the cloud would be strictly more code for the same number.
    ponytail: no clustering -- Lift/pick scenes hold one object on the table;
    add DBSCAN only if a multi-object scene actually lands.
    """
    pts = np.asarray(cloud_world, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1:] != (3,):
        raise GraspInferenceProtocolError("cloud_world must have shape (N, 3)")
    keep = pts[:, 2] > table_z + margin
    if bounds is not None:
        lo, hi = np.asarray(bounds, dtype=np.float64)
        keep &= np.all((pts >= lo) & (pts <= hi), axis=1)
    return pts[keep]


def _error(detail: str, frame: str, convention: str) -> dict[str, Any]:
    """A wire response the vendored client turns into GraspInferenceUnavailable."""
    return {
        "protocol": PROTOCOL_VERSION,
        "status": "error",
        "error": detail,
        "provider": PROVIDER,
        "model": MODEL,
        "model_version": MODEL_VERSION,
        "frame": frame,
        "convention": convention,
    }


def propose_geometric(
    points_world: np.ndarray,
    *,
    frame: str = "world",
    convention: str = GRASP_CONVENTION,
    top_band: float = TOP_BAND,
    clearance: float = CLEARANCE,
    min_points: int = MIN_POINTS,
) -> dict[str, Any]:
    """World object points -> one top-down grasp, shaped like a server reply.

    Position = full-cloud centroid (the whole visible surface pins the object
    centre best). Orientation + width = PCA over the TOP-BAND xy footprint:
    approach = world -z, closing axis = MINOR principal axis (grasp the narrow
    way), binormal completes a right-handed frame, width = the object's extent
    along the closing axis + clearance. Degenerate clouds fail closed with an
    error response -- never a fabricated pose.
    """
    pts = np.asarray(points_world, dtype=np.float64)
    n = len(pts) if pts.ndim == 2 and pts.shape[1:] == (3,) else 0
    if n < min_points:
        return _error(f"degenerate cloud: {n} points < {min_points}", frame, convention)

    centroid = pts.mean(0)
    top = pts[pts[:, 2] > pts[:, 2].max() - top_band]
    if len(top) < min_points:
        return _error(f"top band too sparse: {len(top)} points", frame, convention)

    xy = top[:, :2] - top[:, :2].mean(0)
    cov = xy.T @ xy / len(xy)
    evals, evecs = np.linalg.eigh(cov)  # ascending: column 0 is the minor axis
    if evals[1] < 1e-8:  # essentially a point/vertical line -> no footprint
        return _error("no measurable footprint", frame, convention)

    minor = evecs[:, 0]
    closing = np.array([minor[0], minor[1], 0.0])
    closing /= np.linalg.norm(closing)
    approach = np.array([0.0, 0.0, -1.0])
    binormal = np.cross(approach, closing)  # right-handed: det(R) == +1
    pose = np.eye(4)
    pose[:3, :3] = np.column_stack([closing, binormal, approach])
    pose[:3, 3] = centroid

    proj = xy @ closing[:2]
    width = float(proj.max() - proj.min()) + clearance
    score = float(np.clip(len(pts) / 200.0, 0.0, 1.0))
    return {
        "protocol": PROTOCOL_VERSION,
        "status": "ok",
        "provider": PROVIDER,
        "model": MODEL,
        "model_version": MODEL_VERSION,
        "frame": frame,
        "convention": convention,
        "grasps": pose[None, :, :],
        "scores": np.array([score]),
        "widths": np.array([width]),
    }


class _GeometricTransport:
    """Client transport that answers ``infer`` with a geometric proposal.

    Zero new contract: GraspInferenceClient validates the request, packs the
    object points onto the wire, and runs every fail-closed response check; this
    just decodes those points and returns a proposal in the server's shape.
    """

    def __init__(
        self,
        *,
        top_band: float = TOP_BAND,
        clearance: float = CLEARANCE,
        min_points: int = MIN_POINTS,
    ) -> None:
        self.top_band = top_band
        self.clearance = clearance
        self.min_points = min_points

    def request(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_s: float,
    ) -> Mapping[str, Any]:
        if operation != "infer":
            raise GraspInferenceProtocolError(
                f"geometric transport supports only 'infer', not {operation!r}",
            )
        points = _array_from_wire(payload["object_points"], "object_points")
        return propose_geometric(
            points,
            frame=payload["frame"],
            convention=payload["convention"],
            top_band=self.top_band,
            clearance=self.clearance,
            min_points=self.min_points,
        )


def propose(
    points_world: np.ndarray,
    *,
    scene_bounds: np.ndarray | None = None,
    frame: str = "world",
    top_band: float = TOP_BAND,
    clearance: float = CLEARANCE,
    min_points: int = MIN_POINTS,
) -> GraspInferenceResult:
    """Geometric grasp, validated through the vendored client's fail-closed path.

    ``scene_bounds`` defaults to the cloud's own extent (+5cm margin) so the
    request-validation check that points lie inside the scene is satisfied.
    """
    pts = np.asarray(points_world, dtype=np.float64)
    if scene_bounds is None:
        if pts.ndim == 2 and pts.shape[1:] == (3,) and len(pts) >= 1:
            scene_bounds = np.stack([pts.min(0) - 0.05, pts.max(0) + 0.05])
        else:  # let the client raise the canonical shape error
            scene_bounds = np.array([[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]])
    client = GraspInferenceClient(
        GraspInferenceConfig(provider=PROVIDER, endpoint="local://geometric"),
        transport=_GeometricTransport(
            top_band=top_band, clearance=clearance, min_points=min_points,
        ),
        monotonic_fn=lambda: 0.0,  # local compute; the wall-clock deadline is moot
    )
    return client.infer(object_points=pts, scene_bounds=scene_bounds, frame=frame)
