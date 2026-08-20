"""Contact-seeking repair primitives.

Round 19 found the ceiling: 39 of 42 unrepaired failures put the gripper within
3cm of the object and still did not seat a grasp, and no re-approach SHAPE and
no percept accuracy fixed most of them. The remaining gap is the grasp itself.

These close the loop on proprioception -- descend until contact is felt, close
until the fingers stop moving -- so the privilege budget stays at zero.

A CORRECTION, kept here because the mistake is instructive. ``ServoDescend`` was
written on the premise that the repair aims at a wrong estimated height. It does
not: :func:`governor.governed._percept_object` perturbs x and y and leaves z
exactly true (``np.array([N(0,sd), N(0,sd), 0.0])``), so the naive repair already
descends to the correct height and contact-seeking removes an error that is not
there. Measured percept z-error over 60 episodes: 0.00000.

The premise was never checked before the primitive was built. What noise there
IS lives in the table plane, so the primitive that would match this failure is a
contact-guided LATERAL search, not a vertical one. ``ServoClose`` -- closing until
the fingers settle instead of for a fixed count -- is unaffected by the error and
is the part of this module that still has a reason to exist.

Round 19's conclusion stands: a harness cannot exceed its repair primitives, and
raising that ceiling means writing a better one. This was one attempt, tested
with adequate power, that did not clear.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Per-step descent of the end effector below which the arm is treated as blocked.
CONTACT_DZ = 2.5e-4
#: Per-step change in finger opening below which the fingers are treated as settled.
SETTLE_DGAP = 1.5e-4
#: Finger opening under which the gripper is holding nothing.
EMPTY_GAP = 0.012


def finger_gap(obs) -> float:
    q = np.asarray(obs["robot0_gripper_qpos"])
    return float(abs(q[0] - q[1]))


@dataclass
class ServoDescend:
    """Descend until contact, rather than to an estimated height.

    Commands a steady downward velocity and watches the end effector's actual
    height. When it stops falling while still being pushed down, something is
    under it. The object's estimated z never enters the loop.
    """

    max_steps: int = 40
    gain: float = 8.0
    target_xy: np.ndarray | None = None
    _last_z: float | None = field(default=None, repr=False)
    _stalled: int = field(default=0, repr=False)
    _used: int = field(default=0, repr=False)

    def act(self, obs) -> np.ndarray:
        eef = np.asarray(obs["robot0_eef_pos"])
        z = float(eef[2])
        if self._last_z is not None and (self._last_z - z) < CONTACT_DZ:
            self._stalled += 1
        else:
            self._stalled = 0
        self._last_z = z
        dxy = np.zeros(2)
        if self.target_xy is not None:
            dxy = np.clip((np.asarray(self.target_xy) - eef[:2]) * self.gain, -1, 1)
        self._used += 1
        return np.array([dxy[0], dxy[1], -0.35, 0.0, 0.0, 0.0, -1.0])

    @property
    def done(self) -> bool:
        return self._stalled >= 3 or self._used >= self.max_steps


@dataclass
class ServoClose:
    """Close until the fingers stop moving, then report whether anything is held."""

    max_steps: int = 30
    _last_gap: float | None = field(default=None, repr=False)
    _settled: int = field(default=0, repr=False)
    _used: int = field(default=0, repr=False)
    _final_gap: float = field(default=0.0, repr=False)

    def act(self, obs) -> np.ndarray:
        gap = finger_gap(obs)
        if self._last_gap is not None and abs(self._last_gap - gap) < SETTLE_DGAP:
            self._settled += 1
        else:
            self._settled = 0
        self._last_gap = gap
        self._final_gap = gap
        self._used += 1
        return np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])

    @property
    def done(self) -> bool:
        return self._settled >= 3 or self._used >= self.max_steps

    @property
    def holding(self) -> bool:
        """Whether the settled opening is wide enough to be holding something."""
        return self._final_gap > EMPTY_GAP


def make(kind: str, **kw):
    if kind == "servo_descend":
        return ServoDescend(**kw)
    if kind == "servo_close":
        return ServoClose(**kw)
    raise KeyError(f"unknown servo primitive {kind!r}")
