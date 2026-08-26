"""This card's recovery repair shapes, declared in its manifest's ``[recoveries.*]``.

Each strategy is a different SHAPE of repair, expressed as phases plus an
optional planar offset applied to the re-estimated object pose. All of them
consume the same percept the privilege budget admits, so switching strategy
never changes what a bundle is allowed to know.

They live in THIS card because ``plugins/rsi/servo.py``'s RecoveryActor turns a
phase name into an action via ``harness/spec_tabletop.py``'s
PHASE_HEIGHT/STACK_PHASE_HEIGHT -- a tabletop arm's above/descend/close/lift/
place vocabulary. That vocabulary means nothing on another embodiment, so a
repair shape is declared BY the card whose vocabulary it speaks, and a card
declaring no ``[recoveries.*]`` has no recovery primitives at all
(``plugins/rsi/repertoire.py`` reports that verbatim, never substituting these).
"""

from __future__ import annotations

from dataclasses import dataclass

#: (phase, duration, dx, dy) -- the offset displaces the goal in the table plane.
Step = tuple[str, int, float, float]


@dataclass(frozen=True, slots=True)
class Strategy:
    """One named repair shape (satisfies ``harness.contracts.RecoveryStrategy``)."""

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


REGRASP = Strategy(
    "regrasp",
    (("descend", 10, 0.0, 0.0), ("above", 22, 0.0, 0.0), ("descend", 25, 0.0, 0.0),
     ("close", 14, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
    "Full retreat and re-approach on a fresh percept. The incumbent; assumes the "
    "object is where the new estimate says and the grip simply missed.",
)

SETTLE = Strategy(
    "settle",
    (("descend", 6, 0.0, 0.0), ("close", 20, 0.0, 0.0), ("lift", 44, 0.0, 0.0)),
    "No retreat: sink slightly, close longer, lift. For a near-miss where the "
    "fingers are already around the object and the grip just did not seat.",
)

LATERAL = Strategy(
    "lateral",
    (("descend", 10, 0.0, 0.0), ("above", 18, 0.03, 0.0), ("descend", 26, 0.03, 0.0),
     ("close", 14, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
    "Re-approach from 3cm to the side. For a repeated miss in the same direction, "
    "where a second attempt at the same pose reproduces the first failure.",
)

HIGH_RESET = Strategy(
    "high_reset",
    (("lift", 14, 0.0, 0.0), ("above", 26, 0.0, 0.0), ("descend", 28, 0.0, 0.0),
     ("close", 16, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
    "Clear the workspace vertically first, then descend fresh. For a configuration "
    "where retreating in place keeps the arm in contact with the scene.",
)

SERVO_REGRASP = Strategy(
    "servo_regrasp",
    (("descend", 10, 0.0, 0.0), ("above", 20, 0.0, 0.0),
     ("servo_descend", 40, 0.0, 0.0), ("servo_close", 30, 0.0, 0.0),
     ("lift", 40, 0.0, 0.0)),
    "Approach open loop, then descend until contact is FELT and close until the "
    "fingers stop. Round 19 showed the gap is the grasp, not the approach: the "
    "naive repair aims at percept_z, which is the quantity that was wrong. Contact "
    "and finger motion are proprioceptive, so the privilege budget stays at zero.",
)

PROBE_REGRASP = Strategy(
    "probe_regrasp",
    (("descend", 10, 0.0, 0.0), ("above", 20, 0.0, 0.0), ("descend", 24, 0.0, 0.0),
     ("servo_probe", 160, 0.0, 0.0), ("lift", 40, 0.0, 0.0)),
    "Approach, then close-and-step-sideways until the fingers settle on something. "
    "Measured before building: planar percept error is 1.24cm mean / 2.25cm p90, "
    "the gripper-object offset tracks it at r=+0.67, and misses sit at 1.64cm "
    "against 1.05cm for holds. Two probe rings at 1.2 and 2.4cm cover the p90 miss.",
)

REPLACE = Strategy(
    "replace",
    (("lift", 10, 0.0, 0.0), ("over_b", 12, 0.0, 0.0), ("place", 20, 0.0, 0.0),
     ("release", 8, 0.0, 0.0), ("retreat", 10, 0.0, 0.0)),
    "The missing place-shaped repair: every other strategy is grasp-shaped and "
    "ends in lift. This one assumes the cube is already held and the SEAT failed "
    "-- lift clear, move over cubeB, place, release, retreat -- run against a FRESH "
    "independent cubeB estimate (the place goal governed threads in). Independence "
    "of the draw is the whole repair channel, exactly as it is for the regrasp: the "
    "original placement acted on one noisy cubeB read, this acts on another.",
)
