#!/usr/bin/env python3
"""Audit the GRASP oracle the way gate 1 audited the place one.

``plugins/embodiment_robocasa/predicates.py:obj_grasped`` wraps robocasa's

    check_obj_grasped(env, obj, threshold=0.035)
        -> env.check_contact(gripper, obj) and gripper_closed

Contact plus fingers closed. No lift, no force, no displacement. Structurally it
cannot tell "holding" from "touching": a gripper closed AROUND an object that is
still resting on the shelf satisfies both terms. CLAUDE.md's first page names
this exact family of scar ("a near-always-true grasp check"), and gate 1
(``scripts/probe_place_demos.py``, commit 3f02334) audited ``obj_in_microwave``
rigorously -- while the un-audited ``obj_grasped`` sat beside it, scoring every
grasp number this project has reported.

Same two probes, same order of cost:

  --controls  Synthetic. Teleport the ROBOT (base slides + torso + a damped
              least-squares arm IK on the 7 arm joints) so the closed gripper
              sits exactly at the meat's resting pose, and ask.
                * FALSE-POSITIVE case: meat untouched at its resting pose, still
                  supported by the shelf/container, gripper closed on it. A
                  predicate that means "holding" must read False here.
                * TRUE-POSITIVE case: the same closed gripper and the meat
                  together at resting_z + LIFT_DZ, clear of every support geom.
                  Must read True.
              The pair differs in ONE thing -- whether the object left its
              support -- so the confusion matrix isolates the missing term.

  --replay    The real thing: the 100 human demos, every frame the predicate
              reads True scored against the object's own z relative to its
              resting z at t=0 and against whether it is still touching a
              non-robot geom. The share of True-frames spent NOT RISEN AND STILL
              SUPPORTED is the size of the lie, and it is a measurement, not an
              adjective. The same replay scores the FIXED predicate
              (``predicates:obj_grasped_secure``, the latch conjoined with
              GraspDriver.SECURE_DZ of rise off z0): a fix has to keep firing on
              real human grasps, or it is merely a stricter lie.

Read-only. Replays sealed states, seals nothing, scratch seeds only.

    scripts/probe_grasp_predicate.py --controls
    scripts/probe_grasp_predicate.py --replay --episodes 100
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `plugins` on path

#: Same demonstration root gate 1 replayed (data, never evidence).
DEMOS = Path(os.environ.get(
    "PH_DEMOS_ROOT", Path.home() / "Desktop/datasets/robocasa/lerobot",
))

FOOD = "meat"
ENV_NAME = "MicrowaveThawingFridge"
ROBOT = "PandaOmron"
STRIDE = 5  # gate 1's stride: ~1300 frames -> ~260 predicate evaluations

#: "Has not risen" for the audit's accounting. NOT a success threshold -- the
#: fix's threshold is GraspDriver.SECURE_DZ, read from the driver. 20 mm is the
#: generous reading of "the object never left the shelf": anything under it is
#: settle/jitter, and counting it as risen only makes the lie look smaller.
UNRISEN_DZ = 0.020


def _env(seed: int = 0):
    os.environ.setdefault("MUJOCO_GL", "egl")
    from robocasa.utils.env_utils import create_env
    return create_env(env_name=ENV_NAME, robots=ROBOT, seed=seed)


def _obj_z(env, name: str = FOOD) -> float:
    return float(np.asarray(env.sim.data.body_xpos[env.obj_body_id[name]])[2])


def _gripper_contact(env, name: str = FOOD) -> bool:
    """check_obj_grasped's first conjunct on its own -- the fingers really touch
    the object. A control state that fails this was never built."""
    return bool(env.check_contact(env.robots[0].gripper["right"], env.objects[name]))


def _support_contacts(env, name: str = FOOD) -> list[str]:
    """Geoms OTHER than the robot's that the object currently touches.

    "Still supported" in the physical sense: something that is not the hand is
    holding this object up. Independent of any z reading, so it corroborates the
    displacement term rather than restating it.
    """
    m, d = env.sim.model, env.sim.data
    own = set(env.objects[name].contact_geoms)
    ids = {i for i in range(m.ngeom) if m.geom_id2name(i) in own}
    out = []
    for i in range(d.ncon):
        c = d.contact[i]
        for a, b in ((c.geom1, c.geom2), (c.geom2, c.geom1)):
            if a in ids and b not in ids:
                other = m.geom_id2name(b) or f"geom{b}"
                if not other.startswith(("robot0", "gripper0")):
                    out.append(other)
    return sorted(set(out))


# ── synthetic controls ────────────────────────────────────────────────────────

def _set_base(env, xy) -> None:
    """Teleport the mobile base to world `xy` (forward/side are parent-frame
    slides, so a world delta is a qpos delta; verified by read-back below)."""
    from plugins.embodiment_robocasa import drivers as D
    m, d = env.sim.model, env.sim.data
    cur, _ = D._base_pose(env)
    delta = np.asarray(xy, float) - cur
    for j, dv in (("mobilebase0_joint_mobile_forward", delta[0]),
                  ("mobilebase0_joint_mobile_side", delta[1])):
        adr = m.jnt_qposadr[m.joint_name2id(j)]
        d.qpos[adr] += dv
    env.sim.forward()


def _set_torso(env, q: float) -> None:
    m, d = env.sim.model, env.sim.data
    d.qpos[m.jnt_qposadr[m.joint_name2id("mobilebase0_joint_torso_height")]] = q
    env.sim.forward()


def _ik_eef(env, target, iters: int = 300, damp: float = 0.05) -> float:
    """Damped least squares on the 7 arm joints until the eef site reaches
    `target`. Returns the residual so a control that did not converge is
    reported rather than silently scored."""
    from plugins.embodiment_robocasa import drivers as D
    m, d = env.sim.model, env.sim.data
    jids = [m.joint_name2id(f"robot0_joint{i}") for i in range(1, 8)]
    qadr = np.array([m.jnt_qposadr[j] for j in jids])
    dadr = np.array([m.jnt_dofadr[j] for j in jids])
    site = env.robots[0].eef_site_id["right"]
    name = m.site_id2name(site)
    target = np.asarray(target, float)
    for _ in range(iters):
        err = target - D._eef(env)
        if np.linalg.norm(err) < 1e-4:
            break
        J = np.asarray(d.get_site_jacp(name)).reshape(3, -1)[:, dadr]
        dq = J.T @ np.linalg.solve(J @ J.T + damp ** 2 * np.eye(3), err)
        d.qpos[qadr] += np.clip(dq, -0.1, 0.1)
        env.sim.forward()
    return float(np.linalg.norm(target - D._eef(env)))


def _close_fingers(env) -> None:
    """Both finger joints below check_obj_grasped's 0.035 rad `gripper_closed`
    threshold -- the state a real close ends in."""
    m, d = env.sim.model, env.sim.data
    for j in ("gripper0_right_finger_joint1", "gripper0_right_finger_joint2"):
        d.qpos[m.jnt_qposadr[m.joint_name2id(j)]] = 0.02
    env.sim.forward()


def _teleport(env, pos, name: str = FOOD) -> None:
    env.sim.data.set_joint_qpos(
        env.objects[name].joints[0],
        np.concatenate([np.asarray(pos, float), [1.0, 0.0, 0.0, 0.0]]))
    env.sim.forward()


def _pose_gripper_at(env, target) -> float:
    """Park the base within reach of `target`, raise the torso onto it, IK the
    eef there, close the fingers. The driver's own park geometry is reused so
    the synthetic pose is one the real approach could stand in."""
    from plugins.embodiment_robocasa import drivers as D
    G = D.GraspDriver
    _, psi = D._base_pose(env)
    _set_torso(env, float(np.clip(target[2] - G.WORK_Z, 0.0, G.TORSO_MAX)))
    fwd, lat = G.FWD_MIN, G.LAT
    _set_base(env, np.asarray(target[:2]) - np.array(
        [np.cos(psi) * fwd - np.sin(psi) * lat,
         np.sin(psi) * fwd + np.cos(psi) * lat]))
    res = _ik_eef(env, target)
    _close_fingers(env)
    return res


def controls(seeds: list[int]) -> bool:
    """Positive + negative control on the grasp predicate, no demos needed."""
    import robocasa.utils.object_utils as OU

    from plugins.embodiment_robocasa import drivers as D
    from plugins.embodiment_robocasa.predicates import obj_grasped_secure

    lift = D.GraspDriver.LIFT_DZ
    secure = obj_grasped_secure(FOOD)
    rows = []
    for seed in seeds:
        env = _env(seed)
        env.reset()
        rest = D._obj_pos(env, FOOD)

        # -- FALSE-POSITIVE case: closed gripper AT the resting pose ----------
        res_fp = _pose_gripper_at(env, rest)
        fp = {"contact": _gripper_contact(env),
              "latch": bool(OU.check_obj_grasped(env, FOOD)),
              "fixed": bool(secure(env, rest[2])),
              "dz": round(_obj_z(env) - rest[2], 4),
              "support": _support_contacts(env),
              "ik_res": round(res_fp, 4)}

        # -- TRUE-POSITIVE case: the same closure, object lifted clear --------
        up = rest + np.array([0.0, 0.0, lift])
        _teleport(env, up)
        res_tp = _pose_gripper_at(env, up)
        _teleport(env, up)  # the IK march must not leave the meat behind
        tp = {"contact": _gripper_contact(env),
              "latch": bool(OU.check_obj_grasped(env, FOOD)),
              "fixed": bool(secure(env, rest[2])),
              "dz": round(_obj_z(env) - rest[2], 4),
              "support": _support_contacts(env),
              "ik_res": round(res_tp, 4)}

        rows.append({"seed": seed, "rest_z": round(float(rest[2]), 4),
                     "held_and_lifted": tp, "closed_but_resting": fp})
        print(f"seed {seed}  rest_z={rest[2]:.3f}\n"
              f"    held+lifted    touch={tp['contact']!s:5} latch={tp['latch']!s:5}"
              f" fixed={tp['fixed']!s:5}"
              f" dz={tp['dz']:+.3f} support={tp['support']} ik={tp['ik_res']:.4f}\n"
              f"    closed+resting touch={fp['contact']!s:5} latch={fp['latch']!s:5}"
              f" fixed={fp['fixed']!s:5}"
              f" dz={fp['dz']:+.3f} support={fp['support']} ik={fp['ik_res']:.4f}",
              flush=True)
        env.close()

    # A control only counts if the STATE it built is the state it claims: the
    # fingers must actually touch the meat in BOTH cases (a teleport that ended
    # with the slab between the pads but touching neither measures the IK, not
    # the predicate), the positive must be off its support, the negative on it.
    valid = [r for r in rows
             if r["held_and_lifted"]["contact"] and r["closed_but_resting"]["contact"]
             and not r["held_and_lifted"]["support"] and r["closed_but_resting"]["support"]]
    print(f"\nconstructible controls {len(valid)}/{len(rows)}   (fingers touching"
          " the meat in both; positive clear of support, negative still on it)")
    for label, key in (("bare latch", "latch"), ("fixed (SECURE_DZ)", "fixed")):
        tp = sum(r["held_and_lifted"][key] for r in valid)
        fp = sum(r["closed_but_resting"][key] for r in valid)
        n = len(valid)
        print(f"\n{label} confusion matrix over {n} controls")
        print("                       pred True   pred False")
        print(f"  held and lifted      {tp:^9d}   {n - tp:^10d}   (want all True)")
        print(f"  closed, still resting{fp:^9d}   {n - fp:^10d}   (want all False)")
    print(json.dumps(rows))
    return bool(valid) and all(r["held_and_lifted"]["latch"]
                               and not r["closed_but_resting"]["latch"] for r in valid)


# ── real demo replay ──────────────────────────────────────────────────────────

def _load(idx: int):
    d = DEMOS / "extras" / f"episode_{idx:06d}"
    with gzip.open(d / "model.xml.gz", "rt") as f:
        model = f.read()
    return np.load(d / "states.npz")["states"], model, (d / "ep_meta.json").read_text()


def replay(n: int) -> bool:
    """Every frame of every demo: what the latch says vs what the meat did."""
    import robocasa.utils.object_utils as OU
    from robocasa.scripts.dataset_scripts.playback_dataset import reset_to

    from plugins.embodiment_robocasa.predicates import obj_grasped_secure

    secure = obj_grasped_secure(FOOD)
    env = _env()
    rows, tot = [], {"latch": 0, "unrisen": 0, "unrisen_supported": 0,
                     "supported": 0, "fixed": 0, "frames": 0}
    for idx in range(n):
        states, model, ep_meta = _load(idx)
        reset_to(env, {"model": model, "ep_meta": ep_meta, "states": states[0]})
        z0 = _obj_z(env)  # the resting z the mission's survey node would seal

        latch = unrisen = unrisen_sup = sup = fixed = 0
        frames = 0
        dz_at_latch: list[float] = []
        for t in range(0, len(states), STRIDE):
            env.sim.set_state_from_flattened(states[t])
            env.sim.forward()
            frames += 1
            if not OU.check_obj_grasped(env, FOOD):
                continue
            latch += 1
            dz = _obj_z(env) - z0
            dz_at_latch.append(dz)
            on_support = bool(_support_contacts(env))
            unrisen += dz < UNRISEN_DZ
            sup += on_support
            unrisen_sup += (dz < UNRISEN_DZ) and on_support
            fixed += bool(secure(env, z0))

        rows.append({"episode": idx, "n_frames": len(states),
                     "rest_z": round(z0, 4), "latch_frames": latch,
                     "unrisen_frames": unrisen, "supported_frames": sup,
                     "unrisen_supported_frames": unrisen_sup,
                     "fixed_frames": fixed,
                     "max_dz_at_latch": round(max(dz_at_latch), 4) if dz_at_latch else None})
        for k, v in (("latch", latch), ("unrisen", unrisen), ("supported", sup),
                     ("unrisen_supported", unrisen_sup), ("fixed", fixed),
                     ("frames", frames)):
            tot[k] += v
        r = rows[-1]
        print(f"ep {idx:3d} n={len(states):5d} latchT={latch:4d}"
              f"  unrisen={unrisen:4d} supported={sup:4d} both={unrisen_sup:4d}"
              f"  fixedT={fixed:4d} max_dz={r['max_dz_at_latch']}", flush=True)

    L = tot["latch"] or 1
    demos_fixed = sum(r["fixed_frames"] > 0 for r in rows)
    demos_latch = sum(r["latch_frames"] > 0 for r in rows)
    print(f"\n--- {len(rows)} demos, {tot['frames']} evaluated frames "
          f"(stride {STRIDE}) ---")
    print(f"frames the BARE LATCH reads True            {tot['latch']}")
    print(f"  ... with the meat NOT risen (<{UNRISEN_DZ*1000:.0f} mm)      "
          f"{tot['unrisen']}  ({tot['unrisen']/L*100:.1f}%)")
    print(f"  ... still touching a non-robot support    "
          f"{tot['supported']}  ({tot['supported']/L*100:.1f}%)")
    print(f"  ... BOTH (not risen AND still supported)  "
          f"{tot['unrisen_supported']}  ({tot['unrisen_supported']/L*100:.1f}%)")
    print(f"frames the FIXED predicate reads True       "
          f"{tot['fixed']}  ({tot['fixed']/L*100:.1f}% of latch frames)")
    print(f"\ndemos where the bare latch ever fires   {demos_latch}/{len(rows)}")
    print(f"demos where the FIXED pred ever fires   {demos_fixed}/{len(rows)}"
          "   <- post-fix true-positive rate on real human grasps")
    print(json.dumps(rows))
    return demos_fixed == len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--episodes", type=int, default=100)
    ap.add_argument("--seeds", type=int, nargs="+",
                    default=[420901, 420902, 420903, 420904, 420905],
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
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
