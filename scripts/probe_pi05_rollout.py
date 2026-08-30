#!/usr/bin/env python3
"""Gate 2 smoke: drive MicrowaveThawingFridge with pi0.5 (or the scripted driver)
and record what the mission's own predicates say, frame by frame.

Two arms, ONE instrumentation path -- same env construction, same seeds, same
per-step predicate evaluation, same renderer -- so the paired numbers are
comparable by construction rather than by argument:

  --arm pi05      the fine-tuned executor behind ``scripts/serve_vla_openpi.py``,
                  reached through ``plugins/policy_vla_remote`` over the socket.
                  It is a WHOLE-TASK policy (trained on all ~126k frames of the
                  nav->open->grasp->place->close->press demos), so it drives one
                  continuous rollout; there is no segment to enter.
  --arm handover  scripted nav+grasp, then pi0.5 from the ``place`` segment on.
                  The whole-task rollout cannot measure pi0.5's place capability
                  -- it is bottlenecked by pi0.5's own grasp -- so this buys the
                  policy the scripted arm's grasp rate worth of attempts. Reads
                  only alongside the hand-off state it records (see
                  :func:`handover_stepper`).
  --arm scripted  ``plugins/embodiment_robocasa/kitchen_driver.py``, re-tasked
                  segment by segment exactly as the mission planner's
                  SEGMENT_SPECS does, each segment run to its own ``done`` or its
                  own step cap. NOT the governed mission: no replans, no verify
                  nodes, no first-death attribution. This is the cheap preview of
                  the paired comparison, never a gate-3 result.

**The image is flipped.** robosuite serves camera observations in the OpenGL
convention (bottom-up; ``macros.IMAGE_CONVENTION = "opengl"``), while the LeRobot
videos the checkpoint trained on are top-down. Measured on demo episode 0 replayed
into a live env: MAE(demo_frame0, live_raw) = 77.8, MAE(demo_frame0, live[::-1]) =
15.2 (the residual is video compression). Feeding the raw obs would be an
upside-down train/test gap -- the silent success-rate killer the card's handshake
banner warns about, and one no handshake key can catch.

**The action is permuted.** The LeRobot dataset stores actions in modality.json
order (base_motion 0:4, control_mode 4:5, eef_pos 5:8, eef_rot 8:11, gripper
11:12), which is NOT the order robosuite's PandaOmron controller consumes
(arm OSC 0:6, gripper 6, base vx/vy/wyaw/torso 7:11, base_mode 11 --
``plugins/embodiment_robocasa/drivers.py``). robocasa's own
``lerobot_utils.ACTION_KEY_ORDERING_HDF5`` is the source of truth for the
mapping; :func:`lerobot_to_env` is its inverse.

Watchdog: one CHILD PROCESS per episode, killed by the parent on overrun
(``subprocess.run(timeout=)`` -> SIGKILL). A SIGALRM would not fire inside
MuJoCo C code. The child re-writes its JSON every ``--flush-every`` steps, so a
killed episode still reports the stages it reached, marked ``truncated``.

Read-only w.r.t. the ledger: scratch seeds only (42xxxx), seals nothing, never
touches STATUS.md.

    # parent (all seeds, both arms are separate invocations)
    cd /home/yusenzlabpc/Desktop/physical-harness && MUJOCO_GL=egl PYTHONPATH=. \
      /home/yusenzlabpc/Desktop/sims/robocasa-venv/bin/python \
      scripts/probe_pi05_rollout.py --arm pi05 --split train --seeds 420101:420111 \
      --steps 1400 --timeout 1800 --out runs/gate2_eval

    # one episode, in-process (what the parent spawns)
    ... scripts/probe_pi05_rollout.py --arm pi05 --split train --seed 420101 \
      --out runs/gate2_eval

    # the discriminator: the SAME policy, from the DEMOS' own initial states
    ... scripts/probe_pi05_rollout.py --arm pi05 --split train --demos 0:10 \
      --steps 2400 --timeout 900 --out runs/gate2_diag

    # the serving path as a suspect: re-query every step instead of draining the
    # chunk, optionally ensembling the overlap (both OFF unless asked for)
    ... scripts/probe_pi05_rollout.py --arm pi05 --split train --seeds 420101:420111 \
      --steps 2400 --replan-every 1 --ensemble 0.25 --out runs/round98_k1_ens
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENV_NAME = "MicrowaveThawingFridge"
ROBOT = "PandaOmron"

#: RoboCasa ships a scene split and the demos were collected on ONE side of it
#: (extras/dataset_meta.json: layout_ids=-2, style_ids=-2,
#: obj_instance_split="pretrain"). LAYOUT_GROUPS_TO_IDS: -1 TEST = layouts 1-10,
#: -2 TRAIN = 11-60, -3 ALL = 1-60.
#:
#: Leaving these unset resolves to ALL, which silently MIXES the two sides. The
#: first run of this probe drew 4 test layouts and 6 train ones and reported one
#: number over both. That is not a wrong split, it is an UNDECLARED one -- and
#: the two sides answer different questions:
#:
#:   train  scenes the demos covered            -> capability
#:   test   scenes structurally never seen      -> generalisation
#:
#: A paired comparison is fair on either side (both arms get identical scenes),
#: but the CLAIM it supports depends on which, so the caller names it and the
#: name is recorded in every episode record.
SPLITS = {"train": -2, "test": -1, "all": -3}

#: Object instances have their own split, and the demos used the pretrain half.
#: Passing None here draws from all of them, so the policy can meet geometry it
#: never trained on while the layout split says "in distribution".
OBJ_SPLIT = "pretrain"

#: agentview_right is never fed to the model (RoboCasaInputs zeroes the third
#: slot) and rendering it costs a third of the per-step render budget.
CAMERAS = ["robot0_agentview_left", "robot0_eye_in_hand"]
CAM_PX = 256  # the dataset's native stream size; the server resizes to 224

#: obs key -> slice, in modality.json's state order (robocasa's own
#: lerobot_utils.LEROBOT_STATE_TO_HDF5_STATE, read left to right).
STATE_KEYS = ("robot0_base_pos", "robot0_base_quat", "robot0_base_to_eef_pos",
              "robot0_base_to_eef_quat", "robot0_gripper_qpos")

#: The five mission predicates, in chain order. Names are exactly the ones
#: plugins/embodiment_robocasa/predicates.py exports.
PREDICATES = ("fridge_is_open", "obj_grasped", "obj_in_microwave",
              "microwave_closed", "microwave_on")

#: The client-side training-observation contract (probe_vla_handshake.CONTRACT).
CONTRACT = {"image_size": [224, 224], "views": ["base_0_rgb", "left_wrist_0_rgb"],
            "chunk": 10, "unnorm_key": "robocasa/lerobot"}


def lerobot_to_env(a) -> np.ndarray:
    """modality.json action order -> the robosuite PandaOmron action vector.

    Inverse of robocasa ``lerobot_utils.ACTION_KEY_ORDERING_HDF5``. Getting this
    wrong does not raise: the base would receive eef deltas and the arm would
    receive wheel velocities, and the rollout would look merely bad.
    """
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    if a.shape[0] != 12:
        raise ValueError(f"expected a 12-dim action, got {a.shape}")
    out = np.empty(12)
    out[0:3] = a[5:8]     # eef_position   -> arm OSC position
    out[3:6] = a[8:11]    # eef_rotation   -> arm OSC rotation
    out[6] = a[11]        # gripper_close  -> gripper
    out[7:11] = a[0:4]    # base_motion    -> base vx/vy/wyaw/torso
    out[11] = a[4]        # control_mode   -> base_mode
    return out


def build_obs(obs, prompt: str) -> dict:
    """The observation the checkpoint was trained on, out of a live robocasa obs.
    Images are flipped back to the dataset's top-down convention (see module doc)."""
    return {
        "observation/image": np.ascontiguousarray(
            obs["robot0_agentview_left_image"][::-1]),
        "observation/wrist_image": np.ascontiguousarray(
            obs["robot0_eye_in_hand_image"][::-1]),
        "observation/state": np.concatenate(
            [np.asarray(obs[k], dtype=np.float32).reshape(-1) for k in STATE_KEYS]),
        "prompt": prompt,
    }


def make_env(seed: int, split: str):
    """One env on a NAMED scene split -- see SPLITS on why this is not optional."""
    os.environ.setdefault("MUJOCO_GL", "egl")
    from robocasa.utils.env_utils import create_env
    group = SPLITS[split]
    return create_env(env_name=ENV_NAME, robots=ROBOT, seed=seed,
                      camera_names=list(CAMERAS),
                      camera_widths=CAM_PX, camera_heights=CAM_PX,
                      layout_ids=group, style_ids=group,
                      obj_instance_split=OBJ_SPLIT)


def assert_in_split(env, split: str) -> int:
    """The layout the env actually DREW must lie on the declared side.

    ``layout_id`` is only assigned at reset, so this runs after it (and after a
    demo_replay reset, which overrides the scene from the demo's sealed
    ep_meta). robocasa's own table is the truth here, never a second copy of the
    ranges. The undeclared-split bug was invisible for exactly one reason --
    nothing checked -- so this fails the episode rather than annotating it.
    """
    from robocasa.models.scenes.scene_registry import LAYOUT_GROUPS_TO_IDS

    allowed = LAYOUT_GROUPS_TO_IDS[SPLITS[split]]
    layout = int(env.layout_id)
    if layout not in allowed:
        raise SystemExit(
            f"declared --split {split} (layouts {min(allowed)}-{max(allowed)}) "
            f"but the env drew layout {layout}: the declaration did not reach "
            f"create_env, and this episode would answer neither question")
    return layout


def load_predicates(env) -> dict:
    from harness.registry import load_provider
    # load_provider resolves the ref AND calls the zero-arg factory (the way
    # mission_kitchen_thaw.planner._wrap uses it), handing back pred(env)->bool.
    preds = {n: load_provider(f"plugins.embodiment_robocasa.predicates:{n}")
             for n in PREDICATES}
    # obj_grasped is the bare contact+fingers LATCH: it reads True with the hand
    # merely closed around meat that never left the shelf -- 7/7 of the synthetic
    # controls in scripts/probe_grasp_predicate.py. Every "grasp x/10" this probe
    # has printed was that latch, over rollouts whose grasps mostly FAILED, which
    # is exactly the regime the missing lift term hides. Bind the SECURE_DZ-shaped
    # predicate to the meat's resting z HERE, at episode open, before the arm has
    # moved it -- the same reference the mission's survey node seals.
    secure = load_provider("plugins.embodiment_robocasa.predicates:obj_grasped_secure")
    z0 = float(np.asarray(env.sim.data.body_xpos[env.obj_body_id["meat"]])[2])
    preds["obj_grasped"] = lambda e, _f=secure, _z=z0: _f(e, _z)
    return preds


# ── the two arms ─────────────────────────────────────────────────────────────

#: The action dimensions temporal ensembling must not average, in the SERVER's
#: (modality.json) order: control_mode 4 and gripper 11 are two-valued
#: decisions, and the mean of two chunks that disagree is a value neither of
#: them asked for. See RemoteChunkDriver's docstring.
DISCRETE_DIMS = (4, 11)


def pi05_stepper(args, env, obs, prompt):
    """A ``step(obs) -> env_action`` closure over the remote executor, warmed."""
    from plugins.policy_vla_remote import RemoteVlaPolicy

    contract = dict(CONTRACT)
    if args.sha:
        contract["checkpoint_sha"] = args.sha
    factory = RemoteVlaPolicy(host=args.host, port=args.port,
                              replan_every=args.replan_every,
                              ensemble=args.ensemble,
                              discrete_dims=DISCRETE_DIMS, **contract)
    driver = factory.make_driver(spec=None)

    t0 = time.perf_counter()
    driver.act(build_obs(obs, prompt))     # throwaway: the first call JITs (~10 s)
    warm_ms = (time.perf_counter() - t0) * 1000
    driver.reset()                         # discard it; the episode starts clean
    driver.calls = 0                       # ...and so does the inference count

    def step(o, _t):
        return lerobot_to_env(driver.act(build_obs(o, prompt)))

    step.driver = driver                   # the handover arm empties its chunk
    return step, {"handshake": driver.handshake, "warmup_ms": round(warm_ms, 1),
                  "replan_every": args.replan_every, "ensemble": args.ensemble}


#: The segment the scripted->pi05 handover happens at: everything before it is
#: driven by kitchen_driver, `place` onward by the policy.
HANDOVER_SEGMENT = "place"


def scripted_stepper(args, env, obs, prompt, stop_before=None):
    """The kitchen_driver, re-tasked segment by segment through SEGMENT_SPECS.

    ``stop_before`` raises StopIteration at that segment's entry INSTEAD of
    driving it -- how the handover arm gets nav+grasp from the scripted driver
    and nothing else. Either way the state at ``place`` entry is recorded, so
    the incumbent and the challenger are compared from the same distribution of
    hand-off states rather than from an assumption that they are the same.
    """
    from plugins.embodiment_robocasa.kitchen_driver import KitchenThawDriver
    from plugins.mission_kitchen_thaw.planner import _CHAIN, SEGMENT_SPECS
    from scripts.probe_place_demos import handover_state

    class _Spec:
        def __init__(self, task): self.task = task

    order = [seg_skill for _, seg_skill, _, _ in _CHAIN]
    drv = KitchenThawDriver()
    drv.observe_once(obs)
    state = {"i": -1, "log": [], "handover": {}}

    def _enter(i, t):
        skill = order[i]
        if skill == HANDOVER_SEGMENT:
            state["handover"].update(handover_state(env, t))  # in place: shared ref
            if stop_before == skill:
                raise StopIteration(f"handover before {skill!r} at t={t}")
        drv.enter_segment(env, _Spec(SEGMENT_SPECS[skill]["task"]))
        state["i"] = i
        state["log"].append({"segment": skill, "entered_t": t, "exited_t": None,
                             "success": None})

    def step(o, t):
        if state["i"] < 0:
            _enter(0, t)
        while drv.exhausted:
            rec = state["log"][-1]
            rec["exited_t"], rec["success"] = t, bool(drv.segment_success(env))
            if state["i"] + 1 >= len(order):
                raise StopIteration("scripted chain finished")
            _enter(state["i"] + 1, t)
        return np.asarray(drv.act(o), dtype=np.float64)

    return step, {"segments": state["log"], "handover": state["handover"]}


def handover_stepper(args, env, obs, prompt):
    """EXPERIMENT B: scripted nav+grasp, then pi0.5 drives `place` onward.

    The whole-episode rollout could never measure pi0.5's place capability --
    it was bottlenecked by pi0.5's own grasp. The scripted arm grasps far more
    often, so this gives the policy several times the attempts.

    (The 3/10 and 8/10 this paragraph used to quote were ``obj_grasped``
    readings, i.e. the bare contact+fingers LATCH -- since audited and found to
    fire on a hand merely closed around meat still on the shelf. They are not
    grasp rates and both numbers are void; ``load_predicates`` now binds the
    SECURE_DZ-shaped predicate, so a re-run produces a comparable one.)

    Three handover details that silently corrupt the number if missed, all
    handled here: the policy is connected, identity-gated and JIT-warmed BEFORE
    any actuation (``pi05_stepper`` runs first, at episode open); the buffered
    action chunk is dropped at the switch (a stale chunk is actions computed for
    a different situation); and the state at the switch is recorded so a failure
    from an out-of-distribution hand-off is not reported as a place result.
    """
    pi_step, pi_extra = pi05_stepper(args, env, obs, prompt)
    sc_step, sc_extra = scripted_stepper(args, env, obs, prompt,
                                         stop_before=HANDOVER_SEGMENT)
    switched = {"at": None}

    def step(o, t):
        if switched["at"] is None:
            try:
                return sc_step(o, t)
            except StopIteration:
                switched["at"] = t
                pi_step.driver.reset()
        return pi_step(o, t)

    return step, dict(sc_extra, **pi_extra, handover_segment=HANDOVER_SEGMENT)


def demo_stepper(args, env, obs, prompt):
    """POSITIVE CONTROL. Replays a human demo's own recorded actions through the
    SAME ``lerobot_to_env`` permutation and the same instrumentation as the two
    real arms, open loop, from the demo's own sealed initial state.

    This is the check that separates "the policy does nothing" from "the harness
    feeds it nonsense". Every demo succeeds by construction, so if this arm does
    NOT drive obj_in_microwave True, the action mapping is wrong and every number
    on the pi05 arm is void. It costs one episode and it is the only thing
    standing between an honest NO-GO and a fabricated one.
    """
    import pandas as pd

    df = pd.read_parquet(_DEMOS / "data" / "chunk-000"
                         / f"episode_{args.demo:06d}.parquet")
    actions = np.stack([np.asarray(v, dtype=np.float64) for v in df["action"]])

    def step(o, t):
        if t >= len(actions):
            raise StopIteration(f"demo {args.demo} actions exhausted at t={t}")
        return lerobot_to_env(actions[t])

    return step, {"demo_episode": args.demo, "demo_frames": int(len(actions))}


ARMS = {"pi05": pi05_stepper, "scripted": scripted_stepper,
        "demo_replay": demo_stepper, "handover": handover_stepper}

#: The LeRobot demo root; ``extras/`` carries the sealed model.xml + states the
#: demo_replay arm resets to (same layout probe_place_demos.py reads).
_DEMOS = Path(os.environ.get(
    "PH_LEROBOT_ROOT", Path.home() / "Desktop/datasets/robocasa/lerobot"))


def reset_to_demo(env, idx: int):
    """Put the live env in demo ``idx``'s sealed initial state (scene + qpos)."""
    import gzip

    from robocasa.scripts.dataset_scripts.playback_dataset import reset_to

    d = _DEMOS / "extras" / f"episode_{idx:06d}"
    with gzip.open(d / "model.xml.gz", "rt") as f:
        model = f.read()
    states = np.load(d / "states.npz")["states"]
    reset_to(env, {"model": model, "ep_meta": (d / "ep_meta.json").read_text(),
                   "states": states[0]})
    return env._get_observations()


# ── one episode ──────────────────────────────────────────────────────────────

def _overlay(frame, text_lines):
    import cv2
    for i, line in enumerate(text_lines):
        for colour, thick in ((( 0, 0, 0), 3), ((255, 255, 255), 1)):
            cv2.putText(frame, line, (6, 16 + 14 * i), cv2.FONT_HERSHEY_SIMPLEX,
                        0.40, colour, thick, cv2.LINE_AA)
    return frame


def run_episode(args) -> dict:
    import imageio.v2 as imageio

    seed = args.seed
    out_dir = Path(args.out)
    (out_dir / "videos").mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"ep_{args.arm}_{seed}.json"
    video_path = out_dir / "videos" / f"{args.arm}_{seed}.mp4"

    env = make_env(seed, args.split)
    obs = env.reset()
    if args.demo is not None:
        # EXPERIMENT A / the demo_replay control: drive a DEMO's world, not a
        # seeded one. Whatever the arm, the initial state is the demo's own
        # sealed one -- which is the point: it is the only way to ask whether a
        # policy that fails from create_env states also fails from the states
        # its training data actually started in.
        obs = reset_to_demo(env, args.demo)
    layout_id = assert_in_split(env, args.split)
    meta = env.get_ep_meta()
    prompt = str(meta.get("lang") or ENV_NAME)
    preds = load_predicates(env)

    rec: dict = {
        "arm": args.arm, "seed": seed, "prompt": prompt,
        "split": args.split, "layout_id": layout_id,
        "style_id": int(getattr(env, "style_id", -1)),
        "demo": args.demo,
        # the scene's own spawn pose, so a create_env episode and a demo episode
        # are comparable on the one number that decides where the robot starts
        "init_robot_base_pos": meta.get("init_robot_base_pos"),
        "init_robot_base_ori": meta.get("init_robot_base_ori"),
        "steps_requested": args.steps, "steps_run": 0,
        # THE mechanism metric. The demos command base mode on 20.09% of steps
        # (all 100 of them use it; min 7.5%), and this policy reaches, grasps,
        # then stalls without transporting. Counted over EVERY step, not the
        # action_trace subsample, so a share is not a sampling artifact.
        "base_mode_steps": 0, "base_mode_share": None,
        "inference_calls": None,
        "stage_first_t": {p: None for p in PREDICATES},
        "stage_reached": {p: False for p in PREDICATES},
        "stage_final": {p: False for p in PREDICATES},
        "terminal_success": False, "truncated": False, "ended_early": None,
        "video": str(video_path), "checkpoint_sha": args.sha,
        "video_fps": args.video_fps, "video_stride": args.video_stride,
    }

    step_fn, extra = ARMS[args.arm](args, env, obs, prompt)
    rec.update(extra)

    writer = imageio.get_writer(
        video_path, fps=args.video_fps, codec="libx264", quality=None,
        macro_block_size=1, ffmpeg_params=["-crf", "30", "-pix_fmt", "yuv420p"])

    def _flush():
        json_path.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str))

    t_start = time.perf_counter()
    t = 0
    try:
        for t in range(args.steps):
            live = {name: bool(p(env)) for name, p in preds.items()}
            for name, v in live.items():
                if v and rec["stage_first_t"][name] is None:
                    rec["stage_first_t"][name] = t
                    rec["stage_reached"][name] = True
            rec["stage_final"] = live

            if t % args.video_stride == 0:
                base = obs["robot0_agentview_left_image"][::-1]
                wrist = obs["robot0_eye_in_hand_image"][::-1]
                frame = np.concatenate([base, wrist], axis=1).copy()
                flags = " ".join(f"{lbl}:{int(live[p])}" for p, lbl in
                                 zip(PREDICATES, ("fridge", "grasp", "INSIDE",
                                                  "closed", "on")))
                writer.append_data(_overlay(frame, [
                    f"{args.arm}  seed {seed}  t={t}",
                    flags,
                    prompt[:62],
                ]))

            try:
                action = step_fn(obs, t)
            except StopIteration as e:
                rec["ended_early"] = str(e)
                break
            rec["base_mode_steps"] += int(action[11] > 0)  # env order: mode at 11
            if t % args.trace_every == 0:
                # diagnostic only: what the arm actually COMMANDS in the live
                # world, in env order (arm 0:6, gripper 6, base 7:11, mode 11).
                # Offline fidelity cannot answer this -- it never leaves the
                # demo distribution.
                rec.setdefault("action_trace", []).append(
                    {"t": t, "env_action": [round(float(x), 4) for x in action]})
            obs, _r, _d, _i = env.step(action)
            rec["steps_run"] = t + 1

            if t % args.flush_every == 0:
                _flush()
        else:
            rec["ended_early"] = "step budget spent"
    finally:
        # a final predicate read on the world as it now is, then seal
        try:
            rec["stage_final"] = {n: bool(p(env)) for n, p in preds.items()}
            for name, v in rec["stage_final"].items():
                if v and rec["stage_first_t"][name] is None:
                    rec["stage_first_t"][name] = rec["steps_run"]
                    rec["stage_reached"][name] = True
            rec["terminal_success"] = bool(env._check_success())
        except Exception as e:  # noqa: BLE001 -- an unreadable oracle is reported, not raised
            rec["oracle_error"] = repr(e)
        rec["seconds"] = round(time.perf_counter() - t_start, 2)
        rec["steps_per_second"] = round(rec["steps_run"] / max(rec["seconds"], 1e-9), 2)
        rec["base_mode_share"] = round(rec["base_mode_steps"]
                                       / max(rec["steps_run"], 1), 4)
        drv = getattr(step_fn, "driver", None)
        if drv is not None:
            rec["inference_calls"] = drv.calls
        writer.close()
        _flush()
        try:
            env.close()
        except Exception:  # noqa: BLE001
            pass
    return rec


# ── parent: one child per episode, SIGKILL on overrun ────────────────────────

def _seeds(spec: str) -> list[int]:
    if ":" in spec:
        lo, hi = (int(x) for x in spec.split(":", 1))
        return list(range(lo, hi))
    return [int(x) for x in spec.split(",")]


#: A campaign over DEMO initial states still needs a distinct seed per episode
#: (the env is built seeded, then overridden by reset_to) and a distinct output
#: file. Scratch range, so no ledger block is burned.
DEMO_SEED_BASE = 420200


def _jobs(args) -> list[tuple[int, int | None]]:
    """(seed, demo) per episode. --demos runs from demo initial states."""
    if args.demos is not None:
        return [(DEMO_SEED_BASE + d, d) for d in _seeds(args.demos)]
    return [(s, args.demo) for s in _seeds(args.seeds)]


def run_campaign(args) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed, demo in _jobs(args):
        json_path = out_dir / f"ep_{args.arm}_{seed}.json"
        if json_path.exists():
            json_path.unlink()
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--arm", args.arm, "--split", args.split,
               "--seed", str(seed), "--out", str(out_dir),
               "--steps", str(args.steps), "--host", args.host,
               "--port", str(args.port), "--video-stride", str(args.video_stride),
               "--video-fps", str(args.video_fps),
               "--flush-every", str(args.flush_every)]
        if args.replan_every is not None:
            cmd += ["--replan-every", str(args.replan_every)]
        if args.ensemble is not None:
            cmd += ["--ensemble", str(args.ensemble)]
        if demo is not None:
            cmd += ["--demo", str(demo)]
        if args.sha:
            cmd += ["--sha", args.sha]
        t0 = time.perf_counter()
        killed = False
        try:
            subprocess.run(cmd, timeout=args.timeout, check=False)
        except subprocess.TimeoutExpired:
            killed = True  # subprocess.run already SIGKILLed the child
        dt = time.perf_counter() - t0

        if json_path.exists():
            row = json.loads(json_path.read_text())
        else:
            row = {"arm": args.arm, "seed": seed, "split": args.split,
                   "demo": demo, "crashed": True,
                   "stage_reached": {p: False for p in PREDICATES},
                   "stage_first_t": {p: None for p in PREDICATES},
                   "steps_run": 0, "terminal_success": False}
        if killed:
            row["truncated"] = True
            row["truncation"] = f"parent watchdog SIGKILL at {args.timeout}s"
            json_path.write_text(json.dumps(row, indent=1, sort_keys=True, default=str))
        row["wall_seconds"] = round(dt, 2)
        rows.append(row)
        print(f"[{args.arm}] seed {seed}  steps={row.get('steps_run')} "
              f"{'TRUNCATED ' if row.get('truncated') else ''}"
              f"stages={''.join(k[0].upper() if row['stage_reached'].get(k) else '.' for k in PREDICATES)} "
              f"success={row.get('terminal_success')}  {dt:.0f}s", flush=True)

    (out_dir / f"rollouts_{args.arm}.json").write_text(
        json.dumps({"arm": args.arm, "split": args.split, "episodes": rows},
                   indent=1, sort_keys=True, default=str))
    n = len(rows)
    print(f"\n{args.arm} [{args.split}]: {n} episodes, "
          + ", ".join(f"{p} {sum(bool(r['stage_reached'].get(p)) for r in rows)}/{n}"
                      for p in PREDICATES))
    return 0


def summarize(out_dir: Path) -> int:
    """Fold the per-arm files into the two plotting artifacts. Self-describing on
    purpose: whoever plots this should not need to read this script."""
    # Grouped from the per-episode files, not the per-arm rollups: a campaign that
    # died before writing its rollup still reports every episode it finished, and
    # a one-off control run (demo_replay) lands here without a special case.
    arms: dict[str, list] = {}
    for path in sorted(out_dir.glob("ep_*.json")):
        doc = json.loads(path.read_text())
        arms.setdefault(doc["arm"], []).append(doc)
    for rows in arms.values():
        rows.sort(key=lambda r: r["seed"])
    if not arms:
        raise SystemExit(f"no ep_*.json under {out_dir}")

    def _ep(r):
        return {"seed": r["seed"], "demo": r.get("demo"),
                "episode_length": r.get("steps_run"),
                "steps_requested": r.get("steps_requested"),
                "terminal_success": bool(r.get("terminal_success")),
                "truncated": bool(r.get("truncated")),
                "crashed": bool(r.get("crashed")),
                "split": r.get("split"),
                "layout_id": r.get("layout_id"), "style_id": r.get("style_id"),
                "prompt": r.get("prompt"),
                "stages": {p: {"reached": bool(r["stage_reached"].get(p)),
                               "first_t": r["stage_first_t"].get(p),
                               "final": bool((r.get("stage_final") or {}).get(p))}
                           for p in PREDICATES},
                "video": r.get("video"),
                "wall_seconds": r.get("wall_seconds"),
                "base_mode_share": r.get("base_mode_share"),
                "inference_calls": r.get("inference_calls"),
                "replan_every": r.get("replan_every"),
                "ensemble": r.get("ensemble"),
                "segments": r.get("segments"),
                "handover": r.get("handover") or None,
                }

    def _splits(rows):
        """Every split these episodes declared. More than one entry -- or a
        null, from a record written before --split existed -- means the number
        below is an average over scenes that answer different questions."""
        return sorted({str(r.get("split")) for r in rows})

    roles = {
        "pi05": "TREATMENT -- the fine-tuned pi0.5 executor driving the whole task",
        "scripted": "BASELINE -- the scripted kitchen_driver, segment by segment, "
                    "no replans and no verify nodes (not the governed mission, and "
                    "not a gate-3 result)",
        "demo_replay": "POSITIVE CONTROL -- a human demo's own actions replayed "
                       "open loop through the same action permutation and the same "
                       "instrumentation. It must succeed; if it does not, every "
                       "number in this file is void.",
        "handover": "CHALLENGER -- scripted nav+grasp, then pi0.5 drives `place` "
                    "onward. Its obj_in_microwave is a place-capability number "
                    "ONLY for episodes whose `handover` state sits inside the "
                    "demo distribution at the same moment (probe_place_demos.py "
                    "--replay); outside it, the number measures the hand-off.",
    }
    rollouts = {
        "schema": "gate2 pi0.5 segment-executor smoke; one record per (arm, seed)",
        "task": ENV_NAME, "robot": ROBOT,
        "arm_roles": {a: roles.get(a, "unlabelled") for a in arms},
        "seed_class": "scratch (42xxxx) -- burns no ledger block",
        "scene_split": {
            "source": "robocasa LAYOUT_GROUPS_TO_IDS, named per run by --split",
            "train": "layouts 11-60 + obj_instance_split=pretrain, the side the "
                     "demos were collected on -> CAPABILITY",
            "test": "layouts 1-10, structurally never seen -> GENERALISATION",
            "all": "layouts 1-60, a MIXTURE of both -> answers neither",
            "note": "a paired comparison is fair on any single side (both arms "
                    "get identical scenes); which side decides what the number "
                    "may be claimed to mean.",
        },
        "predicates": {
            "source": "plugins/embodiment_robocasa/predicates.py",
            "order": list(PREDICATES),
            "headline": "obj_in_microwave",
            "note": "first_t is the first env timestep the predicate read True; "
                    "null means it never did. fridge_is_open is TRUE AT RESET in "
                    "this scene, so it is a free stage, not an achievement.",
        },
        "arms": {arm: {"n": len(rows), "splits": _splits(rows),
                       "episodes": [_ep(r) for r in rows]}
                 for arm, rows in arms.items()},
    }
    (out_dir / "rollouts.json").write_text(
        json.dumps(rollouts, indent=1, sort_keys=True, default=str))

    summary = {
        "task": ENV_NAME,
        "seeds": sorted({r["seed"] for arm, rows in arms.items()
                         for r in rows if arm != "demo_replay"}),
        "headline_predicate": "obj_in_microwave",
        "arm_roles": rollouts["arm_roles"],
        "arms": {},
    }
    for arm, rows in arms.items():
        n = len(rows)
        handed = [r for r in rows if (r.get("handover") or {}).get("t") is not None]
        summary["arms"][arm] = {
            "n_episodes": n,
            "splits": _splits(rows),
            # the denominator a place number is actually over: a hand-off that
            # arrived without the meat is not a place attempt at all
            "handovers": len(handed),
            "handovers_grasped": sum(bool(r["handover"].get("grasped")) for r in handed),
            "stages_reached": {p: sum(bool(r["stage_reached"].get(p)) for r in rows)
                               for p in PREDICATES},
            "obj_in_microwave": sum(bool(r["stage_reached"].get("obj_in_microwave"))
                                    for r in rows),
            "terminal_success": sum(bool(r.get("terminal_success")) for r in rows),
            "truncated": sum(bool(r.get("truncated")) for r in rows),
            "crashed": sum(bool(r.get("crashed")) for r in rows),
            "mean_episode_length": (round(sum(r.get("steps_run") or 0
                                              for r in rows) / n, 1) if n else None),
            # pooled over steps, not a mean of per-episode shares: episodes that
            # ran longer commanded more steps and should weigh more
            "base_mode_share": round(sum(r.get("base_mode_steps") or 0 for r in rows)
                                     / max(sum(r.get("steps_run") or 0
                                               for r in rows), 1), 4),
            "inference_calls": sum(r.get("inference_calls") or 0 for r in rows),
            # more than one entry = this folder mixes execution policies
            "execution": sorted({(r.get("replan_every"), r.get("ensemble"))
                                 for r in rows}, key=str),
        }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True, default=str))
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=sorted(ARMS))
    ap.add_argument("--split", choices=sorted(SPLITS), default=None,
                    help="scene split to draw layouts/styles/objects from; "
                         "MANDATORY for any run that builds an env (see SPLITS)")
    ap.add_argument("--seed", type=int, help="run ONE episode in this process")
    ap.add_argument("--seeds", help='campaign: "S:E" | "S1,S2,.."  (scratch 42xxxx)')
    ap.add_argument("--demos", help='campaign over DEMO initial states: "S:E" | '
                                    '"D1,D2,.." demo indices (see --demo)')
    ap.add_argument("--steps", type=int, default=1400)
    ap.add_argument("--timeout", type=float, default=2400,
                    help="parent-side per-episode wall cap; SIGKILLs the child")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--sha", default=None, help="gate the served checkpoint identity")
    # Serving-side execution knobs, both OFF unless given -- the default is the
    # drain-the-whole-chunk driver every earlier number here was produced by.
    ap.add_argument("--replan-every", type=int, default=None,
                    help="execute only the first K actions of each chunk, then "
                         "re-infer (K=1 is closed loop; default: drain all 10, "
                         "9 of them open loop)")
    ap.add_argument("--ensemble", type=float, default=None,
                    help="temporal ensembling decay M: average the overlapping "
                         "chunks' predictions for a timestep, weight "
                         "exp(-M*age). Needs --replan-every. control_mode and "
                         "gripper are never averaged (see DISCRETE_DIMS)")
    ap.add_argument("--demo", type=int, default=None,
                    help="reset to this LeRobot demo's sealed initial state "
                         "(demo_replay also replays its actions from there)")
    ap.add_argument("--out", default="runs/gate2_eval")
    ap.add_argument("--video-stride", type=int, default=2)
    ap.add_argument("--video-fps", type=int, default=20)
    ap.add_argument("--flush-every", type=int, default=100)
    ap.add_argument("--trace-every", type=int, default=20,
                    help="record the commanded env action every Nth step")
    ap.add_argument("--summarize", action="store_true",
                    help="fold every rollouts_<arm>.json in --out into "
                         "rollouts.json + summary.json and exit")
    a = ap.parse_args(argv)
    if a.summarize:
        return summarize(Path(a.out))
    if a.arm is None:
        ap.error("--arm is required unless --summarize")
    # No default on purpose. A silent default is the bug being fixed, and a
    # warning still lets the run finish and print a number nobody can interpret.
    # It is conditional rather than argparse-level required=True because
    # --summarize builds no env: demanding a split there would make the caller
    # type a value the artifact must not record.
    if a.split is None:
        ap.error("--split is required: name the scene split this run draws from "
                 f"({'/'.join(sorted(SPLITS))}). train = capability, "
                 "test = generalisation, all = a mixture that answers neither.")
    if sum(x is not None for x in (a.seed, a.seeds, a.demos)) != 1:
        ap.error("give exactly one of --seed (one episode), --seeds (campaign) "
                 "or --demos (campaign over demo initial states)")
    if a.arm == "demo_replay" and a.demo is None and a.demos is None:
        ap.error("--arm demo_replay replays a demo's actions: name it with "
                 "--demo (one episode) or --demos (campaign)")
    if a.seed is not None:
        rec = run_episode(a)
        print(json.dumps({k: rec[k] for k in
                          ("arm", "seed", "steps_run", "stage_first_t",
                           "terminal_success", "seconds", "steps_per_second")},
                         sort_keys=True))
        return 0
    return run_campaign(a)


if __name__ == "__main__":
    sys.exit(main())
