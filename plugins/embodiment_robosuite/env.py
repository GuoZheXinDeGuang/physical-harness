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

from harness.spec import EpisodeSpec

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
