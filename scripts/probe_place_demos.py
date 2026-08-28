#!/usr/bin/env python3
"""Gate 1 of the pi0.5 segment campaign: is there a real signal to learn from?

kitchen_thaw's `place` node scores 0/22. Before spending a fine-tune on it, two
readings of that number have to be told apart:

    H-driver     the scripted driver cannot seat the object -- a real capability
                 wall, and exactly what a learned executor is for.
    H-oracle     `obj_in_microwave` is near-always-FALSE, so nothing could ever
                 score. Then 0/22 says nothing about any policy, and a fine-tune
                 would chase an unreachable predicate.

This repo has scar tissue on the mirror image of H-oracle -- a grasp check that
was near-always-TRUE -- so the predicate is audited before it is trusted, and
before any weights are trained on data it filtered. A wrong rule is merely
useless; data filtered by a wrong rule bakes the lie into weights, where it is
far harder to find.

Two probes, run in order of cost:

  --controls  Synthetic, no demonstration data needed. Teleport the meat to the
              microwave's interior-region centre and ask. A predicate that
              stays False there is unsatisfiable by ANY policy. Two negative
              controls (scene reset, and 40cm above the microwave) guard the
              other direction -- that it is not simply True once teleported.

  --replay    The real thing. Every human demo of MicrowaveThawingFridge
              succeeds by construction, so the predicate must go False -> True
              in each one, and must not already be True at t=0.

--replay also answers gate 1's second question. The demos carry no segment
labels -- one instruction spans all ~1300 frames -- so the `place` boundaries
are derived from the same oracle the gate scores against: from the last grasp
onset to the first frame the meat is inside. That interval is what a segment
executor would be trained on, and printing its size is how "enough data?"
stops being a guess.

Read-only. Replays sealed states, seals nothing, burns no seed.

    scripts/probe_place_demos.py --controls
    scripts/probe_place_demos.py --replay --episodes 10
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path

import numpy as np

#: Content-addressed demonstration root. Data, not evidence -- it never enters
#: the session-log chain; the training prereg references it by digest.
DEMOS = Path(os.environ.get(
    "PH_DEMOS_ROOT", Path.home() / "Desktop/datasets/robocasa/lerobot",
))

#: MicrowaveThawingFridge registers the graspable food under this name and its
#: own _check_success reads it, so this card and the free oracle agree.
FOOD = "meat"
ENV_NAME = "MicrowaveThawingFridge"
ROBOT = "PandaOmron"

#: 20 fps; a segment boundary does not need frame precision, and a stride of 5
#: turns a ~1300-frame replay into ~260 predicate evaluations.
STRIDE = 5


def _env(seed: int = 0):
    os.environ.setdefault("MUJOCO_GL", "egl")
    from robocasa.utils.env_utils import create_env
    return create_env(env_name=ENV_NAME, robots=ROBOT, seed=seed)


def _teleport(env, pos) -> None:
    """Put the meat at `pos`, upright, and settle kinematics only (no dynamics)."""
    obj = env.objects[FOOD]
    env.sim.data.set_joint_qpos(
        obj.joints[0], np.concatenate([pos, [1.0, 0.0, 0.0, 0.0]]),
    )
    env.sim.forward()


def controls(seeds: list[int]) -> bool:
    """Positive + negative controls on the predicate, with no demonstration data."""
    import robocasa.utils.object_utils as OU

    rows = []
    for seed in seeds:
        env = _env(seed)
        env.reset()
        at_reset = bool(OU.obj_inside_of(env, FOOD, env.microwave))

        regions = env.microwave.get_int_sites(relative=False)
        name, (p0, px, py, pz) = next(iter(regions.items()))
        p0, px, py, pz = map(np.asarray, (p0, px, py, pz))
        centre = p0 + ((px - p0) + (py - p0) + (pz - p0)) / 2.0

        _teleport(env, centre)
        at_centre = bool(OU.obj_inside_of(env, FOOD, env.microwave))
        _teleport(env, centre + np.array([0.0, 0.0, 0.40]))
        at_above = bool(OU.obj_inside_of(env, FOOD, env.microwave))

        span = np.array([np.linalg.norm(px - p0), np.linalg.norm(py - p0),
                         np.linalg.norm(pz - p0)])
        pts = np.asarray(env.objects[FOOD].get_bbox_points(
            trans=np.zeros(3), rot=np.array([0.0, 0.0, 0.0, 1.0])))
        meat = pts.max(axis=0) - pts.min(axis=0)

        rows.append((at_reset, at_centre, at_above))
        print(f"seed {seed}  reset={at_reset!s:5} centre={at_centre!s:5} "
              f"above={at_above!s:5}  region '{name}' span={np.round(span, 3)} "
              f"meat={np.round(meat, 3)}", flush=True)
        env.close()

    pos = sum(r[1] for r in rows)
    print(f"\npositive (centre) {pos}/{len(rows)}   want all")
    print(f"negative (reset)  {sum(r[0] for r in rows)}/{len(rows)}   want 0")
    print(f"negative (above)  {sum(r[2] for r in rows)}/{len(rows)}   want 0")
    return pos == len(rows) and not any(r[0] or r[2] for r in rows)


def _load(idx: int):
    d = DEMOS / "extras" / f"episode_{idx:06d}"
    with gzip.open(d / "model.xml.gz", "rt") as f:
        model = f.read()
    return np.load(d / "states.npz")["states"], model, (d / "ep_meta.json").read_text()


def replay(n: int) -> bool:
    """Replay real demos through the predicate; derive the place segment."""
    from robocasa.scripts.dataset_scripts.playback_dataset import reset_to
    import robocasa.utils.object_utils as OU

    env = _env()
    rows = []
    for idx in range(n):
        states, model, ep_meta = _load(idx)
        reset_to(env, {"model": model, "ep_meta": ep_meta, "states": states[0]})

        grasped, inside = [], []
        for t in range(0, len(states), STRIDE):
            env.sim.set_state_from_flattened(states[t])
            env.sim.forward()
            grasped.append(bool(OU.check_obj_grasped(env, FOOD)))
            inside.append(bool(OU.obj_inside_of(env, FOOD, env.microwave)))
        g, i = np.array(grasped), np.array(inside)

        # Place runs from the LAST grasp onset before the meat is first inside:
        # earlier onsets are regrasps, and the segment we want is the approach
        # that actually ended in a seat.
        end = int(np.argmax(i)) if i.any() else -1
        start = -1
        if end > 0 and g[:end].any():
            gg = g[:end]
            onsets = np.flatnonzero((~gg[:-1]) & gg[1:]) + 1
            start = int(onsets[-1]) if len(onsets) else int(np.argmax(gg))

        length = (end - start) * STRIDE if 0 <= start < end else 0
        rows.append({"episode": idx, "n_frames": int(len(states)),
                     "inside_at_t0": bool(i[0]), "inside_ever": bool(i.any()),
                     "place_start": start * STRIDE if start >= 0 else -1,
                     "place_end": end * STRIDE if end >= 0 else -1,
                     "place_len": int(length)})
        print(f"ep {idx:3d} n={len(states):5d}  inside t0={i[0]!s:5} ever={i.any()!s:5}"
              f"  place=[{rows[-1]['place_start']},{rows[-1]['place_end']}]"
              f" len={length}", flush=True)

    ever = sum(r["inside_ever"] for r in rows)
    t0 = sum(r["inside_at_t0"] for r in rows)
    lens = [r["place_len"] for r in rows if r["place_len"] > 0]
    print(f"\ninside_ever  {ever}/{len(rows)}   want all (every demo succeeds)")
    print(f"inside_at_t0 {t0}/{len(rows)}   want 0   (else the predicate is free)")
    if lens:
        share = np.mean(lens) / np.mean([r["n_frames"] for r in rows])
        print(f"place segment: median {int(np.median(lens))} frames "
              f"[{min(lens)}, {max(lens)}], {share * 100:.0f}% of an episode")
    print(json.dumps(rows))
    return ever == len(rows) and t0 == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--controls", action="store_true", help="synthetic controls, no demos needed")
    ap.add_argument("--replay", action="store_true", help="replay real demos through the predicate")
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--seeds", type=int, nargs="+", default=[420011, 420012, 420013, 420014, 420015],
                    help="scratch seeds; controls burn no ledger block")
    a = ap.parse_args()
    if not (a.controls or a.replay):
        ap.error("pick --controls and/or --replay")

    ok = True
    if a.controls:
        print("=== controls ===")
        ok &= controls(a.seeds)
    if a.replay:
        print("\n=== replay ===")
        ok &= replay(a.episodes)
    print("\nDISCRIMINATES" if ok else "\nDOES NOT DISCRIMINATE -- gate 1 stops the campaign")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
