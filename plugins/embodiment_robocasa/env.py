"""RoboCasa embodiment: scene wiring, construction, semantics.

The robosuite card's sibling for a DIFFERENT simulator. Everything here is what
robocasa replaces wholesale: the task table (robocasa kitchen env ids, not
robosuite ones), the environment factory (`robocasa create_env`, not
`robosuite.make`), which observation key holds the target, and what the shared
sub-goal means. `robocasa` is imported LAZILY inside the factory so importing
this module stays base-clean (test_boundaries parses it, plugin_doctor mounts it
on a card-absent machine); only make_env drags the simulator in.
"""

from __future__ import annotations

import importlib
import os

import numpy as np

from harness.spec import EpisodeSpec

#: Per-task scene wiring: the robocasa kitchen env id and the observation key
#: holding the target object's pose. kitchen_thaw's target is the frozen food
#: MicrowaveThawingFridge names "meat" (its own _check_success reads that name),
#: so its pose lands in obs under "meat_pos". Only the kitchen_thaw first-mission
#: task is registered for phase 2 (docs/project-documentation.md §5.4).
TASKS: dict[str, dict] = {
    "kitchen_thaw": {"env": "MicrowaveThawingFridge", "object_key": "meat_pos"},
    # Long-horizon composite missions (install report §3.2). object_key names the
    # FIRST target's pose key -- the single-object seams (percept.object_estimate,
    # lifted) read it; the mission graphs verify every object via their own
    # predicate layers, not this key.
    "recycle_cans": {"env": "RecycleSodaCans", "object_key": "can1_pos"},
    "pack_lunch": {"env": "PackFoodByTemp", "object_key": "hot0_pos"},
    # Same benchmark world as pack_lunch, but the task graph is emitted from the
    # shared abstract skill library by a VLM rather than a fixed mission table.
    "pack_all_robocasa": {"env": "PackFoodByTemp", "object_key": "hot0_pos"},
    "basket_smoke_vlm": {
        "env": "BasketPackingSmoke",
        "object_key": "item0_pos",
        # Importing this embodiment-owned module registers its Kitchen subclass
        # with robosuite before create_env resolves the env name.
        "register": "plugins.embodiment_robocasa.basket_env",
    },
    "steam_prep": {"env": "MultistepSteaming", "object_key": "vegetable1_pos"},
}

#: robocasa's mobile-manipulator embodiment (Panda arm + Omron base). The kitchen
#: envs are authored for it; the tabletop Panda (EpisodeSpec's default) is a
#: different robot family, so this card FORCES PandaOmron regardless of the spec's
#: robot field -- the robot is a fact of the card, not a per-episode knob here.
ROBOT = "PandaOmron"

#: Height above its starting pose the target must reach for the shared sub-goal.
LIFT_MARGIN = 0.04


def task_config(spec: EpisodeSpec) -> dict:
    if spec.task not in TASKS:
        raise KeyError(f"unknown task {spec.task!r}; known: {sorted(TASKS)}")
    return TASKS[spec.task]


def object_key(spec: EpisodeSpec) -> str:
    """Observation key holding the target object's pose."""
    return task_config(spec)["object_key"]


def lifted(obs, spec: EpisodeSpec, start_z: float) -> bool:
    """The shared sub-goal: the target food is off its start pose (grasped+raised).

    Obs-only (no env handle), so it reads the target's z against start_z -- the
    relational form, never an absolute table/shelf height (round-10 fragility
    lesson from the robosuite card). The FULL-task terminal boolean is robocasa's
    own _check_success, exposed via terminal_success in the provider.
    """
    z = float(np.asarray(obs[object_key(spec)])[2])
    return bool(z > start_z + LIFT_MARGIN)


def make_env(spec: EpisodeSpec):
    """Build one deterministic robocasa kitchen env for `spec`.

    seed is passed through to robocasa's scene-generation rng (layout/style/object
    sampling all draw from it -- same seed, same scene, verified in the install
    report §3.6). MUJOCO_GL defaults to egl for headless render; the caller's env
    var wins if already set.
    """
    os.environ.setdefault("MUJOCO_GL", "egl")
    from robocasa.utils.env_utils import create_env

    cfg = task_config(spec)
    if cfg.get("register"):
        importlib.import_module(cfg["register"])
    return create_env(env_name=cfg["env"], robots=ROBOT, seed=spec.seed)


#: Offscreen camera preference for ``frame`` (first present wins; None = free cam).
FRAME_CAMERAS = ("robot0_agentview_left", "agentview", "frontview")


def frame(env, size: int = 128):
    """One ``size``x``size`` RGB uint8 frame of the live env for harness.media
    (scripts/frame_dump's render path, minus the file). Lazy offscreen context;
    read-only on mjData, so it consumes no rng. None when rendering fails."""
    try:
        sim = env.sim
        if sim._render_context_offscreen is None:
            from robosuite.utils.binding_utils import MjRenderContextOffscreen
            MjRenderContextOffscreen(sim, device_id=-1)
        names = tuple(getattr(sim.model, "camera_names", ()) or ())
        cam = next((c for c in FRAME_CAMERAS if c in names), None)
        return sim.render(width=size, height=size, camera_name=cam)[::-1]
    except Exception:  # noqa: BLE001 -- a lost frame never touches the task
        return None
