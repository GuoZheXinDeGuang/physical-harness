"""The observation feature contract.

This is Governor's central mechanism and the place it diverges from both source
projects. Zetta's critics read simulator-internal quantities such as
``privileged.dishwasher.rack.residual_to_success``; its README forbids letting
privileged state "become hidden task control" but ships no mechanism enforcing
it. Here the separation is structural: every feature declares its class, a
critic declares the features it reads, and the gate can require a privilege
budget of zero.

Classes
-------
OBSERVABLE  A real robot could measure this from proprioception, joint sensors,
            or gripper state. Safe for a skill intended to transfer.
PRIVILEGED  Only a simulator knows it (ground-truth object pose, success
            residuals, contact ground truth). Usable for diagnosis, never
            silently usable for control.
DERIVED     A pure function of other declared features; inherits the highest
            privilege class of its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

import numpy as np


class Privilege(Enum):
    """How obtainable a feature is outside a simulator."""

    OBSERVABLE = "observable"
    PRIVILEGED = "privileged"

    @property
    def cost(self) -> int:
        """Budget units consumed by reading one feature of this class."""
        return 0 if self is Privilege.OBSERVABLE else 1


@dataclass(frozen=True, slots=True)
class Feature:
    """One named scalar a critic may read at control frequency."""

    name: str
    privilege: Privilege
    extract: Callable[[Mapping[str, np.ndarray]], float]
    doc: str

    def __post_init__(self) -> None:
        if not self.name or " " in self.name:
            raise ValueError(f"feature name must be a non-empty bare token, got {self.name!r}")
        expected = "observable." if self.privilege is Privilege.OBSERVABLE else "privileged."
        if not self.name.startswith(expected):
            raise ValueError(
                f"feature {self.name!r} is declared {self.privilege.value} "
                f"but its name does not start with {expected!r}; the namespace IS the declaration"
            )


def _f(x) -> float:
    return float(np.asarray(x).reshape(-1)[0]) if np.asarray(x).size == 1 else float(np.asarray(x))


# --- observable: a real Panda reports all of these -------------------------------

def _finger_gap(o: Mapping[str, np.ndarray]) -> float:
    q = np.asarray(o["robot0_gripper_qpos"])
    return float(abs(q[0] - q[1]))


def _eef_speed(o: Mapping[str, np.ndarray]) -> float:
    return float(np.linalg.norm(np.asarray(o["robot0_joint_vel"])))


REGISTRY: dict[str, Feature] = {}


def register(feature: Feature) -> Feature:
    """Add one feature to the process-wide catalog published to proposers."""
    if feature.name in REGISTRY:
        raise ValueError(f"duplicate feature {feature.name!r}")
    REGISTRY[feature.name] = feature
    return feature


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


def observable_names() -> list[str]:
    """Catalog entries a zero-privilege critic may read."""
    return sorted(n for n, f in REGISTRY.items() if f.privilege is Privilege.OBSERVABLE)


def privilege_cost(names) -> int:
    """Budget consumed by a critic reading `names`.

    Raises for an unknown feature: a proposer may not invent features, which is
    how a candidate is prevented from silently reaching into raw observations.
    """
    total = 0
    for n in names:
        if n not in REGISTRY:
            raise KeyError(f"unknown feature {n!r}; declared catalog is {sorted(REGISTRY)}")
        total += REGISTRY[n].privilege.cost
    return total


def extract(obs: Mapping[str, np.ndarray], names) -> dict[str, float]:
    """Project a raw environment observation onto the declared feature vector."""
    return {n: REGISTRY[n].extract(obs) for n in names}
