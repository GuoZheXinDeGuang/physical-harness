#!/usr/bin/env python3
"""Offline difficulty calibration for the Stack baseline policy.

Two passes, run in order (docs/difficulty-calibration.md discipline):

  pass A  fix percept sd, sweep the place/release height knob to seat cubeA
          reliably BEFORE difficulty is measured (a wrong seat height makes
          every sd read 0% and the band meaningless). Seeds 40000+.
  pass B  with the seat knob fixed, sweep percept sd to locate the 40-60%
          baseline band. Seeds 40200+.

    PYTHONPATH=. .venv/bin/python scripts/calibrate_stack.py --pass A
    PYTHONPATH=. .venv/bin/python scripts/calibrate_stack.py --pass B --n 100

This is an OFFLINE harness in the demos.collect_one mould: it builds its own
Stack env, drives StackScriptedDriver to exhaustion, and reads RAW ground
truth (cubeA/cubeB poses, finger gap, env._check_success()) to score and to
classify the failing stage. Ground truth for offline difficulty labelling is
legitimate -- nothing here touches governed_rollout or any privilege budget.
Seed blocks are burn-once: 40000-40199 belongs to pass A, 40200-40599 to
pass B (STATUS ledger); calibrated blocks never become gate or held-out seeds.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV_REF = "plugins.embodiment_robosuite:provider"
POLICY_REF = "plugins.policies:stack_scripted_provider"

#: cubeA seated on cubeB: half-widths 0.02 + 0.025 (matches features._STACK_SEAT).
SEAT = 0.045
#: Holding band for the grasp classifier: finger gap above the round-35
#: dividing line (vs <0.005 closed-on-nothing) while cubeA is a lift margin up.
HOLD_GAP, LIFT_MARGIN = 0.02, 0.04
#: Fingers wider than the 4cm cube they would otherwise still be pinching.
RELEASE_GAP = 0.05


def run_one(args: tuple) -> dict:
    """One ground-truth-scored episode. Flat tuple: it crosses a Pool boundary."""
    import numpy as np

    from governor.env import make_env
    from harness.spec import EpisodeSpec
    from plugins.policies.drivers import make_driver

    seed, sd, place_height = args
    spec = EpisodeSpec(seed=seed, task="stack", percept_noise=sd,
                       env_provider=ENV_REF, policy_provider=POLICY_REF)
    env = make_env(spec)
    obs = env.reset()
    driver = make_driver(spec)
    driver.observe_once(obs)
    if place_height is not None:
        # Offline knob sweep: shadow the class-level height table on THIS
        # instance only. place and release move in lockstep -- both encode
        # the same seat geometry (harness/spec_tabletop.py).
        driver._HEIGHT = {**type(driver)._HEIGHT,
                          "place": place_height, "release": place_height}
    start_z = float(np.asarray(obs["cubeA_pos"])[2])
    grasped = False
    for _ in range(spec.horizon):
        if driver.exhausted:
            break
        obs, _r, done, _i = env.step(driver.act(obs))
        gap = float(abs(obs["robot0_gripper_qpos"][0] - obs["robot0_gripper_qpos"][1]))
        if gap > HOLD_GAP and float(np.asarray(obs["cubeA_pos"])[2]) > start_z + LIFT_MARGIN:
            grasped = True
        if done:
            break
    success = bool(env._check_success())
    a, b = np.asarray(obs["cubeA_pos"]), np.asarray(obs["cubeB_pos"])
    gap = float(abs(obs["robot0_gripper_qpos"][0] - obs["robot0_gripper_qpos"][1]))
    env.close()
    return {
        "seed": seed, "sd": sd, "place_height": place_height,
        "bucket": "success" if success else ("place_fail" if grasped else "grasp_fail"),
        # place-miss geometry, meaningful for the place_fail bucket
        "xy_miss": float(np.linalg.norm(a[:2] - b[:2])),
        "seat_err": float(a[2] - (b[2] + SEAT)),
        "released": gap > RELEASE_GAP,
    }


def _sweep(tag: str, jobs: list[tuple], workers: int) -> None:
    """Run one knob setting's episodes and print its bucket row."""
    from harness.executor import LocalPoolExecutor

    rows = LocalPoolExecutor().map(run_one, jobs, workers=workers)
    n = len(rows)
    pct = {b: 100.0 * sum(r["bucket"] == b for r in rows) / n
           for b in ("success", "grasp_fail", "place_fail")}
    misses = [r for r in rows if r["bucket"] == "place_fail"]
    geom = ""
    if misses:
        import numpy as np

        geom = (f"  | place miss: xy {np.median([m['xy_miss'] for m in misses]):.3f}m"
                f" seat {np.median([m['seat_err'] for m in misses]):+.3f}m"
                f" released {100.0 * sum(m['released'] for m in misses) / len(misses):.0f}%")
    print(f"{tag}  n={n:<4d} success {pct['success']:5.1f}%  "
          f"grasp_fail {pct['grasp_fail']:5.1f}%  place_fail {pct['place_fail']:5.1f}%{geom}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pass", dest="which", choices=("A", "B"), required=True,
                   help="A: sweep place height at fixed sd; B: sweep sd")
    p.add_argument("--heights", type=float, nargs="+",
                   default=[0.040, 0.045, 0.050, 0.055, 0.060],
                   help="pass A: place/release heights above cubeB centre to sweep")
    p.add_argument("--sd", type=float, default=0.010,
                   help="pass A: the fixed percept sd the height sweep runs at")
    p.add_argument("--sds", type=float, nargs="+", default=[0.005, 0.010, 0.015, 0.020],
                   help="pass B: percept sds to sweep")
    p.add_argument("--height", type=float, default=None,
                   help="pass B: pin the pass-A winner; None uses STACK_PHASE_HEIGHT as-is")
    p.add_argument("--n", type=int, default=40, help="episodes per knob setting")
    p.add_argument("--first-seed", type=int, default=None,
                   help="override the block start (default: 40000 pass A, 40200 pass B)")
    p.add_argument("--workers", type=int, default=10)
    args = p.parse_args()

    block_start = 40000 if args.which == "A" else 40200
    first = args.first_seed if args.first_seed is not None else block_start
    block_end = 40200 if args.which == "A" else 40600
    if args.first_seed is None and first + args.n > block_end:
        p.error(f"--n {args.n} overruns the pass-{args.which} seed block "
                f"[{first}, {block_end}); seeds are burn-once (STATUS ledger)")

    seeds = range(first, first + args.n)
    if args.which == "A":
        # Same seeds across heights: a paired sweep of one knob.
        for h in args.heights:
            _sweep(f"height {h:.3f}  sd {args.sd:.3f}",
                   [(s, args.sd, h) for s in seeds], args.workers)
    else:
        for sd in args.sds:
            _sweep(f"sd {sd:.3f}" + (f"  height {args.height:.3f}" if args.height else ""),
                   [(s, sd, args.height) for s in seeds], args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
