"""Same-counter pick/place driver for ``basket_smoke_vlm``."""

from __future__ import annotations

from typing import Any

import numpy as np

from plugins.embodiment_robocasa import drivers as D
from plugins.embodiment_robocasa import stage_extras as X

ITEMS = ("item0", "item1", "item2")


class SameCounterGraspDriver(D.GraspDriver):
    """Re-home an empty arm before each independent tabletop grasp.

    ``ReceptaclePlaceDriver`` may finish as soon as physics proves that the
    previous object is inside and released.  At that point the end effector can
    still be extended over the basket.  Starting the next base-align motion
    from that pose made the inherited fridge-oriented grasp retry all six
    approaches without ever reaching its hover gate.  Pulling the empty arm to
    a body-relative carry pose makes every pick segment self-contained; the
    underlying grasp geometry and its honest secure-lift judge stay unchanged.
    """

    RESET_STEPS = 80
    RESET_TOL = 0.06
    RESET_Z = 1.00

    def __init__(self, obj_name: str):
        super().__init__(obj_name)
        self._reset_left = self.RESET_STEPS

    def act(self, env, obs):
        if self._reset_left > 0:
            xy, psi = D._base_pose(env)
            c, s = np.cos(psi), np.sin(psi)
            fwd, lat = D.NavigateDriver.CARRY_FWD, D.NavigateDriver.CARRY_LAT
            txy = xy + np.array([c * fwd - s * lat, s * fwd + c * lat])
            eef = D._eef(env)
            retracted = float(np.linalg.norm(eef[:2] - txy)) < 0.12
            tz = self.RESET_Z if retracted else float(eef[2])
            goal = np.array([txy[0], txy[1], tz])
            if (float(np.linalg.norm(eef[:2] - txy)) < self.RESET_TOL
                    and abs(float(eef[2]) - self.RESET_Z) < self.RESET_TOL):
                self._reset_left = 0
            else:
                self._reset_left -= 1
                action = D._arm_action(env, goal, D.GRIP_OPEN, kp=6.0)
                action[0:3] = np.clip(
                    action[0:3], -D.NavigateDriver.ARM_CAP,
                    D.NavigateDriver.ARM_CAP)
                action[D.TORSO] = D._torso_cmd(env, 0.0) if retracted else 0.0
                return action
        return super().act(env, obs)


_STAGES: dict[str, tuple[Any, int]] = {}
for _item in ITEMS:
    _STAGES[f"grasp_{_item}"] = (
        lambda item=_item: SameCounterGraspDriver(item), 600)
    _STAGES[f"pack_{_item}"] = (
        lambda item=_item: X.ReceptaclePlaceDriver(item, "basket"), 450)


def provider(**params: Any) -> X.CompositePolicies:
    return X.CompositePolicies(_STAGES, "robocasa_basket_smoke@v2", **params)
