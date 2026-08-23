#!/usr/bin/env python3
"""Round 93 rung 2: the first perception-driven grasp in sim.

Two paired arms on the Lift task, same seeds, same camera env, same success
predicate (embodiment ``lifted()``):

ARM A -- scripted baseline. The existing :class:`ScriptedDriver` acting on the
    privileged cube percept (``cube_pos`` + the spec's percept noise). This is
    the number the harness has always produced.

ARM B -- geometric-grasp-driven. One grasp proposed ONCE at t=0 from the camera
    cloud (segment off the table plane within a fixed workspace box, then
    ``grasp_geometric.propose``'s top-down PCA pose), fed to
    :class:`GraspPoseDriver`, which drives the identical four-phase vocabulary
    toward it. No privileged object pose is ever in the control loop.

Honest expectations
-------------------
A top-down geometric grasp on a cube should land CLOSE to the privileged
baseline -- the value is the pipeline, not the number. Each failure is
classified from the proposed-vs-privileged position error logged per episode:
a WRONG PROPOSAL (the pose was far from the true cube) versus an EXECUTION MISS
(the pose was right, the open-loop descend/close still missed).

The single-view centroid trap (rung-2 finding)
----------------------------------------------
``propose_geometric`` returns the full-cloud centroid as the grasp position. On
a single camera view the cloud is a SHELL -- the far and bottom faces are
occluded -- so its centroid z sits ~1cm above the true object centre. That 1cm
passes rung-1's 2cm cross-check but is fatal to the open-loop descend, which
closes at a fixed height over the target: arm B lands 0% on the raw centroid.
``grasp_pose_from_cloud`` keeps the proposal's (excellent) xy and yaw and width
but replaces the biased z with the object centre height inferred from OBSERVED
geometry only -- halfway between the known table plane and the observed top
surface. No privileged object pose; only the top-band z and the arena height
segment_object already uses.

Like scripts/probe_place.py, the pure helpers are unit-tested against synthetic
clouds and fakes (tests/test_probe_geometric_grasp.py); only ``run_probe`` and
``_run_arm`` touch a real sim.

Usage::

    MUJOCO_GL=egl python scripts/probe_geometric_grasp.py --out runs/round93-geo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Runnable as `python scripts/probe_geometric_grasp.py ...` without PYTHONPATH gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from harness.spec import EpisodeSpec
from plugins.embodiment_robosuite.grasp_client import GraspInferenceError
from plugins.embodiment_robosuite.grasp_geometric import (
    WS_HALF_EXTENT,
    WS_HEIGHT,
    debias_topdown_pose,
    propose,
    segment_object,
    workspace_bounds,
)
from plugins.policies.drivers import GraspPose, GraspPoseDriver, ScriptedDriver
from plugins.rsi.campaign import CampaignStore
from plugins.rsi.gate import exact_mcnemar

#: A proposed grasp within this of the privileged cube centre is "on the object"
#: (cube half-width ~2cm + margin); a failure there is execution, not proposal.
#: ponytail: fixed metres; scale by object size if non-cube objects show up.
PROPOSAL_ERR_THRESH = 0.03


# --- pure: the cloud -> executable grasp pose --------------------------------
# workspace_bounds + the de-bias now live in the embodiment card (carded round
# 97, so a plugin provider can reach them too); imported above and wrapped here
# so this script's public surface is unchanged.
def grasp_pose_from_cloud(
    world_points: np.ndarray,
    table_z: float,
    bounds: np.ndarray | None,
) -> GraspPose:
    """World cloud -> ONE executable :class:`GraspPose`, observed geometry only.

    Segment the object off the table plane (within ``bounds``), take ``propose``'s
    validated top-down pose (fail-closed through the vendored client), then
    de-bias the depth (``debias_topdown_pose``). Raises GraspInferenceError on a
    degenerate cloud (empty segment or too sparse to fit) -- the caller treats
    that as a proposal miss.
    """
    obj = segment_object(world_points, table_z, margin=0.01, bounds=bounds)
    position, yaw, width = debias_topdown_pose(propose(obj), obj, table_z)
    return GraspPose(position=position, yaw=yaw, width=width)


# --- pure: per-failure classification & summary ------------------------------
def classify_failure(pos_err: float, thresh: float = PROPOSAL_ERR_THRESH) -> str:
    """A B-arm failure: the proposal aimed wrong, or execution missed a good pose."""
    return "proposal_wrong" if pos_err > thresh else "execution_miss"


def summarize(records: list[dict]) -> dict:
    """Paired A/B rates, delta, McNemar, and B's per-failure classification."""
    n = len(records)
    a = sum(r["a_success"] for r in records)
    b = sum(r["b_success"] for r in records)
    fixed = sum(1 for r in records if r["b_success"] and not r["a_success"])   # B won, A lost
    broken = sum(1 for r in records if r["a_success"] and not r["b_success"])  # A won, B lost
    b_fail = [r for r in records if not r["b_success"]]
    modes: dict[str, int] = {"proposal_failed": 0, "proposal_wrong": 0, "execution_miss": 0}
    for r in b_fail:
        if r.get("proposal_failed"):
            modes["proposal_failed"] += 1
        else:
            modes[classify_failure(r["pos_err"])] += 1
    proposed = [r["pos_err"] for r in records if r["pos_err"] is not None]
    return {
        "n": n,
        "arm_a_success": a,
        "arm_b_success": b,
        "arm_a_rate": a / n if n else 0.0,
        "arm_b_rate": b / n if n else 0.0,
        "delta": (b - a) / n if n else 0.0,
        "b_over_a_fixed": fixed,
        "a_over_b_broken": broken,
        "mcnemar_p": exact_mcnemar(fixed, broken),
        "b_failure_modes": modes,
        "pos_err_m": {
            "mean": float(np.mean(proposed)) if proposed else None,
            "max": float(np.max(proposed)) if proposed else None,
            "n": len(proposed),
        },
    }


# --- impure: real sim rollouts -----------------------------------------------
def _world_cloud(obs, sim) -> np.ndarray:
    """Lift the optical scene cloud into world coordinates."""
    from plugins.embodiment_robosuite.grasp import scene_cloud

    c = scene_cloud(obs, sim)
    homog = np.c_[c.points, np.ones(len(c.points))]
    return (c.world_from_cam @ homog.T).T[:, :3]


def _run_arm(spec: EpisodeSpec, arm: str) -> dict:
    """One episode of arm 'A' (scripted/privileged) or 'B' (geometric grasp)."""
    from plugins.embodiment_robosuite.env import camera_make_env, lifted, object_key

    env = camera_make_env(spec)
    out: dict = {"pos_err": None, "proposal_failed": False}
    try:
        obs = env.reset()
        start_z = float(np.asarray(obs[object_key(spec)])[2])
        priv = np.asarray(obs[object_key(spec)]).copy()
        if arm == "A":
            driver = ScriptedDriver(spec)
        else:
            table_z = float(env.model.mujoco_arena.table_offset[2])
            bounds = workspace_bounds(env.model.mujoco_arena.table_offset, table_z)
            try:
                grasp = grasp_pose_from_cloud(_world_cloud(obs, env.sim), table_z, bounds)
            except GraspInferenceError as error:
                out.update(success=False, proposal_failed=True, detail=str(error))
                return out
            out["pos_err"] = float(np.linalg.norm(grasp.position - priv))
            out["pos_err_xy"] = float(np.linalg.norm(grasp.position[:2] - priv[:2]))
            out["width"] = grasp.width
            out["yaw"] = grasp.yaw
            driver = GraspPoseDriver(spec, grasp)
        driver.observe_once(obs)
        ok = False
        for _ in range(spec.horizon):
            obs, _r, done, _i = env.step(np.asarray(driver.act(obs), dtype=float))
            if lifted(obs, spec, start_z):
                ok = True
                break
            if done or driver.exhausted:
                break
        out["success"] = ok
        return out
    finally:
        env.close()


def _run_pair(seed: int, task: str, percept_noise: float | None) -> dict:
    """Both arms on one seed -- paired in one worker so the env spawn matches."""
    kw = {} if percept_noise is None else {"percept_noise": percept_noise}
    spec = EpisodeSpec(seed=seed, task=task, **kw)
    a = _run_arm(spec, "A")
    b = _run_arm(spec, "B")
    return {
        "seed": seed,
        "a_success": bool(a["success"]),
        "b_success": bool(b["success"]),
        "pos_err": b["pos_err"],
        "pos_err_xy": b.get("pos_err_xy"),
        "width": b.get("width"),
        "yaw": b.get("yaw"),
        "proposal_failed": b["proposal_failed"],
    }


def run_probe(
    out_dir: str | Path,
    *,
    block: int = 90200,
    n: int = 60,
    task: str = "lift",
    percept_noise: float | None = None,
    workers: int = 6,
    verbose: bool = True,
) -> dict:
    """Run both arms over the scratch seed block, store one diagnostic artifact."""
    out_root = Path(out_dir)
    if out_root.exists():
        raise FileExistsError(f"{out_root} already exists; a probe run writes a fresh store")

    seeds = list(range(block, block + n))
    args = [(s, task, percept_noise) for s in seeds]
    if workers <= 1:
        records = [_run_pair(*a) for a in args]
    else:
        from plugins.rsi.parallel import default_executor

        records = default_executor().map(_star_run_pair, args, workers=workers)

    summary = summarize(records)
    if verbose:
        s = summary
        print(f"n={s['n']} arm A (privileged)={s['arm_a_rate']:.1%} "
              f"arm B (geometric)={s['arm_b_rate']:.1%} delta={s['delta']:+.1%}")
        print(f"paired: B-fixed-A={s['b_over_a_fixed']} A-broke-B={s['a_over_b_broken']} "
              f"mcnemar_p={s['mcnemar_p']:.4f}")
        print(f"B failure modes: {s['b_failure_modes']}")
        print(f"proposed-vs-privileged pos err: mean={s['pos_err_m']['mean']:.4f}m "
              f"max={s['pos_err_m']['max']:.4f}m")

    payload = {
        "grade": "diagnostic",
        "note": ("first perception-driven grasp: arm A is the scripted baseline on the "
                 "privileged percept, arm B drives GraspPoseDriver toward one geometric "
                 "grasp proposed at t=0 from the camera cloud (no privileged object pose "
                 "in the control loop). the value is the pipeline, not the number."),
        "task": task,
        "seeds": {"block": block, "n": n},
        "percept_noise": percept_noise if percept_noise is not None
        else EpisodeSpec(seed=block, task=task).percept_noise,
        "workspace_bounds": {"half_extent": WS_HALF_EXTENT, "height": WS_HEIGHT},
        "proposal_err_thresh": PROPOSAL_ERR_THRESH,
        "summary": summary,
        "episodes": records,
    }
    digest = CampaignStore(out_root).put("geometric_grasp_probe", payload)
    if verbose:
        print(f"geometric_grasp_probe {digest[:12]} -> {out_root}")
    payload["_artifact_sha"] = digest
    return payload


def _star_run_pair(args) -> dict:
    return _run_pair(*args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, required=True,
                        help="fresh store for the one diagnostic artifact; must not exist")
    parser.add_argument("--block", type=int, default=90200, help="first scratch seed")
    parser.add_argument("--n", type=int, default=60, help="paired episode count")
    parser.add_argument("--task", default="lift")
    parser.add_argument("--percept-noise", type=float, default=None,
                        help="override arm A's privileged-percept noise (default: spec default)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_probe(args.out, block=args.block, n=args.n, task=args.task,
              percept_noise=args.percept_noise, workers=args.workers, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
