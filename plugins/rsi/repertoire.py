"""A repertoire of recovery strategies, not one program with tunable durations.

Round 16 measured where the remaining ground is: of 54 residual dev failures on
the cloned policy, 42 had the critic fire and the repair run and fail anyway.
Detection was not the bottleneck; one scripted regrasp not covering the failure
was. Round 6 had already searched that program's phase DURATIONS and found the
gain did not survive a clean gate -- because durations are not the axis that
matters when the repair is the wrong shape.

Each strategy here is a different SHAPE of repair, expressed as phases plus an
optional planar offset applied to the re-estimated object pose. All of them
consume the same percept the privilege budget admits, so switching strategy
never changes what a bundle is allowed to know.
"""

from __future__ import annotations

from dataclasses import dataclass

#: (phase, duration, dx, dy) -- the offset displaces the goal in the table plane.
Step = tuple[str, int, float, float]


@dataclass(frozen=True, slots=True)
class Strategy:
    """One named repair shape."""

    name: str
    steps: tuple[Step, ...]
    rationale: str

    @property
    def length(self) -> int:
        """Upper bound: servo segments may finish early."""
        return sum(d for _n, d, _x, _y in self.steps)

    @property
    def uses_feedback(self) -> bool:
        return any(n.startswith("servo_") for n, _d, _x, _y in self.steps)


REPERTOIRE: tuple[Strategy, ...] = (
    Strategy(
        "regrasp",
        (("descend", 10, 0.0, 0.0), ("above", 22, 0.0, 0.0), ("descend", 25, 0.0, 0.0),
         ("close", 14, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
        "Full retreat and re-approach on a fresh percept. The incumbent; assumes the "
        "object is where the new estimate says and the grip simply missed.",
    ),
    Strategy(
        "settle",
        (("descend", 6, 0.0, 0.0), ("close", 20, 0.0, 0.0), ("lift", 44, 0.0, 0.0)),
        "No retreat: sink slightly, close longer, lift. For a near-miss where the "
        "fingers are already around the object and the grip just did not seat.",
    ),
    Strategy(
        "lateral",
        (("descend", 10, 0.0, 0.0), ("above", 18, 0.03, 0.0), ("descend", 26, 0.03, 0.0),
         ("close", 14, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
        "Re-approach from 3cm to the side. For a repeated miss in the same direction, "
        "where a second attempt at the same pose reproduces the first failure.",
    ),
    Strategy(
        "high_reset",
        (("lift", 14, 0.0, 0.0), ("above", 26, 0.0, 0.0), ("descend", 28, 0.0, 0.0),
         ("close", 16, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
        "Clear the workspace vertically first, then descend fresh. For a configuration "
        "where retreating in place keeps the arm in contact with the scene.",
    ),
)

REPERTOIRE = REPERTOIRE + (
    Strategy(
        "servo_regrasp",
        (("descend", 10, 0.0, 0.0), ("above", 20, 0.0, 0.0),
         ("servo_descend", 40, 0.0, 0.0), ("servo_close", 30, 0.0, 0.0),
         ("lift", 40, 0.0, 0.0)),
        "Approach open loop, then descend until contact is FELT and close until the "
        "fingers stop. Round 19 showed the gap is the grasp, not the approach: the "
        "naive repair aims at percept_z, which is the quantity that was wrong. Contact "
        "and finger motion are proprioceptive, so the privilege budget stays at zero.",
    ),
)

REPERTOIRE = REPERTOIRE + (
    Strategy(
        "probe_regrasp",
        (("descend", 10, 0.0, 0.0), ("above", 20, 0.0, 0.0), ("descend", 24, 0.0, 0.0),
         ("servo_probe", 160, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
        "Approach, then close-and-step-sideways until the fingers settle on something. "
        "Measured before building: planar percept error is 1.24cm mean / 2.25cm p90, "
        "the gripper-object offset tracks it at r=+0.67, and misses sit at 1.64cm "
        "against 1.05cm for holds. Two probe rings at 1.2 and 2.4cm cover the p90 miss.",
    ),
)

BY_NAME = {s.name: s for s in REPERTOIRE}
DEFAULT = "regrasp"


def strategy(name: str) -> Strategy:
    if name not in BY_NAME:
        raise KeyError(f"unknown recovery strategy {name!r}; known: {sorted(BY_NAME)}")
    return BY_NAME[name]


def names() -> list[str]:
    return [s.name for s in REPERTOIRE]
