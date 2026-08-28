"""LIBERO embodiment (SKELETON): scene wiring + env factory.

Mirrors the robocasa card's env.py shape: a TASKS table, the shared lifted()
sub-goal, and a make_env that imports the simulator LAZILY so this module stays
base-clean (test_boundaries parses it; the base lane never drags LIBERO in).

Runs ONLY under sims/libero-venv (py3.10; robosuite 1.4.0 + mujoco 2.3.2 +
numpy 1.22.4 -- a 2022-era stack, ABI-incompatible with both other interpreters).

Known traps (verified during the 2026-08-27 install, docs/project-documentation.md §5.6):

* **`pip install -e` of LIBERO maps NOTHING.** Upstream setup.py uses
  find_packages() but the repo's top-level `libero/` dir has no __init__.py, so
  the PEP-660 editable finder's MAPPING is empty and `import libero` fails. The
  venv instead carries a `libero_repo.pth` pointing at the checkout; `libero`
  resolves as an implicit namespace package. Consequence: any cwd containing a
  `libero/` dir would shadow it -- same family as the robocasa namespace trap.
* **`~/.libero/config.yaml` is a machine-global singleton.** LIBERO writes
  absolute paths there on first import and every later import silently reuses
  them -- on this machine it pointed at the OLD Learning_based_model/LIBERO
  checkout (the openpi client), so the smoke ran off the wrong assets until
  caught. And with no config present, first import PROMPTS interactively
  (input()), hanging any headless run. Fix for both: a pre-written config at
  `<venv>/.libero/config.yaml` selected via LIBERO_CONFIG_PATH, defaulted here
  from sys.prefix so the card never touches the global file.
* `from libero.libero import benchmark` imports torch (init-state loading), so
  the venv carries cpu-only torch.
"""

from __future__ import annotations

import os
import sys

import numpy as np

from harness.spec import EpisodeSpec

#: Per-task scene wiring: benchmark suite, task index within the suite, and the
#: observation key holding the target object's pose. Suite task 0's bddl goal is
#: (On akita_black_bowl_1 plate_1) -- bowl 1, not 2, is the target (read from
#: the bddl file, not guessed). Skeleton registers the one smoke task.
TASKS: dict[str, dict] = {
    "libero_pick_bowl": {
        "suite": "libero_spatial",
        "task_id": 0,
        "object_key": "akita_black_bowl_1_pos",
    },
}

#: Height above its starting pose the target must reach for the shared sub-goal.
LIFT_MARGIN = 0.04


def task_config(spec: EpisodeSpec) -> dict:
    if spec.task not in TASKS:
        raise KeyError(f"unknown task {spec.task!r}; known: {sorted(TASKS)}")
    return TASKS[spec.task]


def object_key(spec: EpisodeSpec) -> str:
    return task_config(spec)["object_key"]


def lifted(obs, spec: EpisodeSpec, start_z: float) -> bool:
    """Shared sub-goal: target off its start pose (relational z, never absolute)."""
    z = float(np.asarray(obs[object_key(spec)])[2])
    return bool(z > start_z + LIFT_MARGIN)


def make_env(spec: EpisodeSpec):
    """Build one LIBERO OffScreenRenderEnv for `spec`, seeded.

    MUJOCO_GL=egl for headless render; LIBERO_CONFIG_PATH defaults to the
    venv-local config (see module docstring) -- caller's env vars win if set.
    Action space is 7-dof (OSC delta pose 6 + gripper 1); obs is a dict of
    per-object `{name}_pos/_quat`, robot proprio, and 128x128 agentview /
    eye-in-hand images.
    """
    os.environ.setdefault("MUJOCO_GL", "egl")
    os.environ.setdefault("LIBERO_CONFIG_PATH", os.path.join(sys.prefix, ".libero"))
    from libero.libero import benchmark, get_libero_path
    from libero.libero.envs import OffScreenRenderEnv

    cfg = task_config(spec)
    suite = benchmark.get_benchmark_dict()[cfg["suite"]]()
    task = suite.get_task(cfg["task_id"])
    bddl = os.path.join(get_libero_path("bddl_files"), task.problem_folder, task.bddl_file)
    env = OffScreenRenderEnv(bddl_file_name=bddl, camera_heights=128, camera_widths=128)
    env.seed(spec.seed)
    return env
