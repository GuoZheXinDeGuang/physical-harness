"""The base observation-feature catalog: what the harness declares by default.

Round 96 (R2) moves these declarations OFF the embodiment card and onto the
base. Before this, the catalog was populated as an import side-effect of
``plugins.embodiment_robosuite.features`` -- the round-69 "registry inversion":
the process-wide ``REGISTRY`` was empty until some card import happened to run,
so base logic (percept, invariant, proposer, search) and its tests could only
see features if a card had already been imported, and every base test that
needed the catalog had to ``import`` the card purely for the side-effect.

Owning the catalog here kills that class outright. ``harness.features`` imports
this module at its own bottom, so the catalog is populated the instant the
feature machinery is imported -- which every base consumer does -- and there is
no longer any way to read an empty ``REGISTRY``. It is also spawn-safe by the
same token: a worker re-imports ``harness.features`` and the registration
re-runs with no state to migrate, no card required.

The extractors read robosuite/Panda observation keys (``robot0_*``, ``cube*``)
because the harness is sim-only today; the names, order, and semantics are
byte-identical to the card declarations they replace, so the catalog every
sealed run hashed against is unchanged (parity). A future embodiment card that
exposes DIFFERENT features registers them itself on mount (harness.features
.register); nothing here forbids that, and nothing here depends on it.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from harness.features import Feature, Privilege, register

# --- observable: a real Panda reports all of these -------------------------------

def _finger_gap(o: Mapping[str, np.ndarray]) -> float:
    q = np.asarray(o["robot0_gripper_qpos"])
    return float(abs(q[0] - q[1]))


def _eef_speed(o: Mapping[str, np.ndarray]) -> float:
    return float(np.linalg.norm(np.asarray(o["robot0_joint_vel"])))


register(Feature("observable.finger_gap", Privilege.OBSERVABLE, _finger_gap,
                 "Distance between the two gripper fingers. Near zero means closed on nothing."))
register(Feature("observable.eef_z", Privilege.OBSERVABLE,
                 lambda o: float(np.asarray(o["robot0_eef_pos"])[2]),
                 "End-effector height in the world frame (forward kinematics; no external sensing)."))
register(Feature("observable.joint_speed", Privilege.OBSERVABLE, _eef_speed,
                 "L2 norm of joint velocities. Stalls and collisions show up here."))
register(Feature("observable.gripper_effort", Privilege.OBSERVABLE,
                 lambda o: float(np.linalg.norm(np.asarray(o["robot0_gripper_qvel"]))),
                 "Gripper joint speed magnitude; nonzero while the fingers are still closing."))

# --- privileged: only the simulator knows these ----------------------------------
#
# Note what this costs. The observable features above read proprioception and are
# object-agnostic, so they transfer to another task unchanged. The privileged
# ones must first FIND the object, and every task names it differently
# (cube_pos / cubeA_pos / Can_pos). Privilege is not just untransferable to a
# real robot; here it does not even transfer between two simulated tasks without
# a lookup table.

#: Object-pose keys in resolution order, one per supported task.
_OBJECT_KEYS = ("cube_pos", "cubeA_pos", "Can_pos", "Milk_pos")


def _object_pos(o: Mapping[str, np.ndarray]) -> np.ndarray:
    for key in _OBJECT_KEYS:
        if key in o:
            return np.asarray(o[key])
    raise KeyError(f"no object pose in observation; looked for {_OBJECT_KEYS}")


register(Feature("privileged.object_z", Privilege.PRIVILEGED,
                 lambda o: float(_object_pos(o)[2]),
                 "Ground-truth height of the target object. Needs external perception on a real robot."))
register(Feature("privileged.grasp_error", Privilege.PRIVILEGED,
                 lambda o: float(np.linalg.norm(
                     _object_pos(o)[:2] - np.asarray(o["robot0_eef_pos"])[:2])),
                 "Ground-truth planar distance from the end effector to the target object."))

# Stack residuals (R2): RELATIONAL, cubeA relative to cubeB, following the
# grasp_error precedent -- the relation lives in the FEATURE so a stage clause
# over it stays Trigger-shaped with an honest tolerance threshold. A static
# object_z threshold here would be the round-10 table-height fragility.

#: cubeA's centre height when properly seated on cubeB: cubeA half-width 0.02
#: + cubeB half-width 0.025. Cube geometry that travels with the cubes, not a
#: scene fact like table height.
_STACK_SEAT = 0.045


def _stack_xy_residual(o: Mapping[str, np.ndarray]) -> float:
    if "cubeB_pos" not in o:
        return float("nan")  # not a stacking scene; project() is eager, so degrade, never raise
    return float(np.linalg.norm(
        np.asarray(o["cubeA_pos"])[:2] - np.asarray(o["cubeB_pos"])[:2]))


def _stack_z_residual(o: Mapping[str, np.ndarray]) -> float:
    if "cubeB_pos" not in o:
        return float("nan")
    return float(np.asarray(o["cubeA_pos"])[2]
                 - (np.asarray(o["cubeB_pos"])[2] + _STACK_SEAT))


register(Feature("privileged.stack_xy_residual", Privilege.PRIVILEGED, _stack_xy_residual,
                 "Ground-truth planar distance from cubeA to cubeB. NaN off the stack scene."))
register(Feature("privileged.stack_z_residual", Privilege.PRIVILEGED, _stack_z_residual,
                 "Ground-truth height of cubeA's centre above its seated-on-cubeB pose. "
                 "NaN off the stack scene."))
