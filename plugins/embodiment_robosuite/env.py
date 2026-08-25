"""Robosuite embodiment implementation: scene wiring, construction, semantics.

Everything here is what a DIFFERENT embodiment would replace wholesale: the
task table, the environment factory, which observation key holds the target,
and what "the grasp succeeded" means. The generic dispatch (`make_env` on the
spec ref) stays in the transitional governor module; the EnvProvider contract
methods below are how the rsi workload reaches these semantics without ever
importing this plugin.
"""

from __future__ import annotations

import numpy as np

from harness.spec import NOMINAL_SCHEDULE, STACK_SCHEDULE, Clause, EpisodeSpec, StageSpec

CONTROL_FREQ = 20
#: Per-task scene wiring: the robosuite env id, the observation key holding the
#: target object's pose, and any extra make() kwargs. The observable features a
#: zero-privilege critic reads are object-independent, so only the PRIVILEGED
#: side needs this table -- which is itself an argument for the privilege budget.
TASKS: dict[str, dict] = {
    "lift":     {"env": "Lift",      "object_key": "cube_pos",  "kwargs": {}},
    "stack":    {"env": "Stack",     "object_key": "cubeA_pos", "kwargs": {}},
    "pickcan":  {"env": "PickPlace", "object_key": "Can_pos",
                 "kwargs": {"single_object_mode": 2, "object_type": "can"}},
    "pickmilk": {"env": "PickPlace", "object_key": "Milk_pos",
                 "kwargs": {"single_object_mode": 2, "object_type": "milk"}},
    # M7 clear_workspace: ONE mode-0 PickPlace episode staging ALL FOUR objects.
    # The persistent runner builds the env ONCE from "clearall"; each sub-goal
    # retargets the driver via a per-object task below, whose ONLY job is to name
    # the object_key the segment reads/scores against (the env is never rebuilt
    # per segment -- object_key is the only field the persistent path consults).
    # All mode 0 so a stray make_env stays self-consistent with the episode env.
    "clearall":    {"env": "PickPlace", "object_key": "Milk_pos",
                    "kwargs": {"single_object_mode": 0}},
    "clearmilk":   {"env": "PickPlace", "object_key": "Milk_pos",
                    "kwargs": {"single_object_mode": 0}},
    "clearbread":  {"env": "PickPlace", "object_key": "Bread_pos",
                    "kwargs": {"single_object_mode": 0}},
    "clearcereal": {"env": "PickPlace", "object_key": "Cereal_pos",
                    "kwargs": {"single_object_mode": 0}},
    "clearcan":    {"env": "PickPlace", "object_key": "Can_pos",
                    "kwargs": {"single_object_mode": 0}},
}

#: Height above its starting pose the target must reach for the shared sub-goal.
LIFT_MARGIN = 0.04


def task_config(spec: EpisodeSpec) -> dict:
    """Scene wiring for this spec's task."""
    if spec.task not in TASKS:
        raise KeyError(f"unknown task {spec.task!r}; known: {sorted(TASKS)}")
    return TASKS[spec.task]


def object_key(spec: EpisodeSpec) -> str:
    """Observation key holding the target object's pose."""
    return task_config(spec)["object_key"]


def lifted(obs, spec: EpisodeSpec, start_z: float) -> bool:
    """The SHARED sub-goal: the target is off the table and held.

    Lift, Stack and PickPlace have different task successes -- lift it, place it
    on B, drop it in a bin -- and the frozen policy's four-phase schedule only
    ever attempts the first. Scoring each task's own success would measure the
    policy's missing skills, not the critic's. What all three share is the
    grasp, which is exactly the failure the evolved critics detect, so the
    transfer experiment scores the grasp: raised past a margin, with the fingers
    still held apart by something.
    """
    z = float(np.asarray(obs[object_key(spec)])[2])
    q = np.asarray(obs["robot0_gripper_qpos"])
    return bool(z > start_z + LIFT_MARGIN and abs(q[0] - q[1]) > 0.01)


#: Phases of STACK_SCHEDULE that belong to the grasp stage; the rest are place.
_STACK_GRASP_PHASES = ("above", "descend", "close", "lift")


def stack_stages() -> tuple[StageSpec, ...]:
    """The two-stage Stack measurement chain: grasp cubeA, seat it on cubeB.

    Budgets are derived from STACK_SCHEDULE's cumulative phase durations, so a
    stage boundary is exactly where the scripted policy's own schedule hands
    the grasp half over to the place half. Clause thresholds are authored
    tolerances (finger travel, lift margin, cube half-widths) -- never scene
    facts like table height, per the round-10 lesson. The chain is measurement
    only: terminal task success stays the single boolean the gate consumes.
    """
    grasp_budget = sum(d for name, d in STACK_SCHEDULE if name in _STACK_GRASP_PHASES)
    place_budget = sum(d for _, d in STACK_SCHEDULE) - grasp_budget
    return (
        StageSpec("grasp", (
            # The shared sub-goal, clause-shaped: fingers held apart by
            # something (lifted()'s own 0.01 literal) and cubeA raised at
            # least a lift margin above the seat plane -- relational via the
            # residual, so no table-height constant appears anywhere.
            Clause("observable.finger_gap", "gt", 0.01),
            Clause("privileged.stack_z_residual", "gt", 0.04),
        ), grasp_budget),
        StageSpec("place", (
            # Seated within cubeB's half-width in the plane, within cubeA's
            # half-width of the seat height, and released (fingers wider than
            # the 4cm cube they would otherwise still be pinching).
            Clause("privileged.stack_xy_residual", "lt", 0.025),
            Clause("privileged.stack_z_residual", "lt", 0.02),
            Clause("observable.finger_gap", "gt", 0.05),
        ), place_budget),
    )


def pick_stages() -> tuple[StageSpec, ...]:
    """The one-stage pick measurement chain: grasp whatever the task targets.

    A single grasp stage whose only clause is the object-independent half of
    lifted(): fingers held apart by something. The raised-past-a-margin half
    needs start_z, which a static Clause cannot carry, and an absolute
    object_z threshold would be the round-10 table-height fragility -- so the
    lift half stays with the terminal label (lifted()), which the governed
    rollout scores anyway. Budget is the full NOMINAL_SCHEDULE span: the
    scripted four-phase policy IS the grasp, there is no place half.
    """
    return (
        StageSpec("grasp", (
            Clause("observable.finger_gap", "gt", 0.01),
        ), sum(d for _, d in NOMINAL_SCHEDULE)),
    )


def _default_make_env(spec: EpisodeSpec):
    """Build one deterministic robosuite environment for `spec`."""
    import robosuite as suite

    cfg = task_config(spec)
    return suite.make(
        cfg["env"],
        robots=spec.robot,
        has_renderer=False,
        has_offscreen_renderer=False,
        use_camera_obs=False,
        control_freq=CONTROL_FREQ,
        horizon=spec.horizon,
        initialization_noise={"magnitude": spec.arm_noise, "type": "gaussian"},
        seed=spec.seed,  # the ONLY correct seeding channel; see module docstring
        **cfg["kwargs"],
    )


def camera_make_env(spec: EpisodeSpec):
    """Camera variant of `_default_make_env` for the grasp geometry chain.

    Adds an offscreen agentview RGB-D sensor: the obs dict gains
    `agentview_image` and `agentview_depth` (normalized [0, 1]; see grasp.py),
    every other key unchanged. The privileged/RNG channels match
    `_default_make_env` verbatim -- it stays untouched, parity is pinned on it,
    and camera obs only ADD keys. This is a separate factory, not a provider:
    the EnvProvider/PolicyDriver contracts are deliberately not touched.
    """
    import robosuite as suite

    cfg = task_config(spec)
    return suite.make(
        cfg["env"],
        robots=spec.robot,
        has_renderer=False,
        has_offscreen_renderer=True,
        use_camera_obs=True,
        camera_depths=True,
        camera_names=["agentview"],
        control_freq=CONTROL_FREQ,
        horizon=spec.horizon,
        initialization_noise={"magnitude": spec.arm_noise, "type": "gaussian"},
        seed=spec.seed,
        **cfg["kwargs"],
    )
