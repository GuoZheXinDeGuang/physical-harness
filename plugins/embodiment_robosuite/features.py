"""Robosuite feature surface: what THIS embodiment exposes to critics.

The contract machinery (Privilege, Feature, register, the registry and its
query functions) lives in harness.features; this module is the embodiment's
DECLARATION, executed at import time. Import-time registration is what keeps
the registry spawn-safe: a worker that loads this embodiment's provider ref
imports this package, and the registration re-runs in that process with no
state to migrate. The kernel path reaches here through the provider import;
the legacy path reaches here through governor.features' transitional shell.
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
_OBJECT_KEYS = ("cube_pos", "cubeA_pos", "Can_pos")


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

