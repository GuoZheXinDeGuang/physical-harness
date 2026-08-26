"""RoboCasa recovery primitives: the 12-dim intervention a promoted rule triggers.

The RSI plugin's ``RecoveryActor`` (plugins/rsi/recovery.py) speaks the robosuite
tabletop's 7-dim above/descend/close/lift vocabulary -- meaningless on PandaOmron,
whose action is 12-dim (arm OSC 0:6, gripper 6, base vx/vy/wyaw 7:10, torso 10,
base_mode 11). This actor is the robocasa card's answer: a bounded, scripted repair
that REUSES ``drivers.py``'s verified motor primitives (``_arm_action`` /
``_base_action``, which already encode the empirically-measured base_mode discipline
and the world->base-frame rotation) rather than re-deriving a 12-dim sequence.

Two shapes, one per registered ``Strategy`` (plugins/rsi/repertoire.py, card
``embodiment_robocasa``):

* ``regrasp_kitchen`` -- for the grasp node: unclench -> raise clear -> re-descend
  onto a fresh live meat pose -> clench. The measured false-enclosure death
  (drivers.GraspDriver.done: SECURE_DZ is the real-enclosure judge) is a grip that
  read closed on nothing; this releases and re-seats. Arm mode throughout (MODE=-1).
* ``redock_retry`` -- for a nav/at stall: back the base straight out, then re-drive
  the fixture dock (a fresh ``NavigateDriver``), handing back so the stalled segment
  retries from a clean approach. Base mode throughout (MODE=+1).

Like the stage drivers, this reads LIVE privileged env state (the meat body, the
base pose) -- these are oracle scripted policies the harness governs, not learned
policies under test, so the recovery re-read is env truth, not the tabletop percept
seam (whose ``object_key`` would not even resolve a sub-goal task). The phase names
are deliberately DISJOINT from the tabletop PHASE_HEIGHT/STACK_PHASE_HEIGHT vocab so
``governed._is_place_recovery`` stays False and the firing path never routes this
through a place estimate.
"""

from __future__ import annotations

import numpy as np

from plugins.embodiment_robocasa import drivers as D

#: Arm-mode phases (MODE=-1) vs base-mode phases (MODE=+1). Names chosen to not
#: collide with the tabletop vocab (see module docstring).
_ARM_PHASES = frozenset({"unclench", "raise", "reseat", "clench"})
_BASE_PHASES = frozenset({"backout", "redock"})

#: How far the ``raise`` phase lifts the eef before re-seating, and the small
#: below-object aim the ``reseat`` phase descends to -- mirrors GraspDriver's
#: LIFT_DZ-scale clearance and its "aim slightly below the grasp point" (drivers.py).
_RAISE_DZ = 0.12
_RESEAT_BELOW = 0.005
_ARM_CAP = 0.3


class RobocasaRecoveryActor:
    """Scripted 12-dim repair that owns control for the length of its program.

    Built by ``KitchenThawDriver.make_recovery`` (which holds the live env and the
    active stage's target names) and driven by ``governed_segment``'s recovery loop
    exactly like the tabletop ``RecoveryActor``: ``.act(obs) -> (12,)`` each step,
    ``.done`` when the program is spent. ``obs`` is accepted for protocol parity but
    the primitives read the env directly (oracle-side, as the drivers do)."""

    def __init__(self, env, name: str, steps, *, obj_name=None, fixture_name=None) -> None:
        self.env = env
        self.name = name
        self.obj_name = obj_name
        self.fixture_name = fixture_name
        #: flatten (phase, dur, dx, dy) into a per-step queue, same as RecoveryActor
        self.queue: list[tuple[str, float, float]] = []
        for step in steps:
            phase, dur, dx, dy = step
            if phase not in _ARM_PHASES and phase not in _BASE_PHASES:
                raise ValueError(
                    f"unknown robocasa recovery phase {phase!r}; known: "
                    f"{sorted(_ARM_PHASES | _BASE_PHASES)}")
            self.queue += [(phase, float(dx), float(dy))] * int(dur)
        self._i = 0
        self._raise_z: float | None = None
        self._nav = None

    @property
    def done(self) -> bool:
        return self._i >= len(self.queue)

    def act(self, obs) -> np.ndarray:
        phase, dx, dy = self.queue[self._i]
        self._i += 1
        if phase in _BASE_PHASES:
            return self._base_phase(phase, obs)
        return self._arm_phase(phase, dx, dy)

    # -- arm-mode phases (regrasp_kitchen) -------------------------------------
    def _arm_phase(self, phase: str, dx: float, dy: float) -> np.ndarray:
        env = self.env
        eef = D._eef(env)
        if phase == "unclench":
            return D._arm_action(env, eef, D.GRIP_OPEN, kp=0.0)
        if phase == "raise":
            if self._raise_z is None:
                self._raise_z = float(eef[2]) + _RAISE_DZ
            a = D._arm_action(env, np.array([eef[0], eef[1], self._raise_z]), D.GRIP_OPEN)
            a[0:3] = np.clip(a[0:3], -_ARM_CAP, _ARM_CAP)  # gentle raise (GraspDriver LIFT_CAP)
            return a
        if phase == "reseat":
            m = self._obj_xyz()
            aim = np.array([m[0] + dx, m[1] + dy, m[2] - _RESEAT_BELOW])
            a = D._arm_action(env, aim, D.GRIP_OPEN)
            a[2] = float(np.clip(a[2], -0.5, 0.5))
            return a
        # clench: close in place (kp=0), the enclosure settle
        return D._arm_action(env, eef, D.GRIP_CLOSE, kp=0.0)

    def _obj_xyz(self) -> np.ndarray:
        """The live target-object world pose (privileged, oracle-side)."""
        if self.obj_name is None:
            raise ValueError("regrasp recovery needs the active stage's obj_name")
        return D._obj_pos(self.env, self.obj_name)

    # -- base-mode phases (redock_retry) ---------------------------------------
    def _base_phase(self, phase: str, obs) -> np.ndarray:
        env = self.env
        if phase == "backout":
            a = D._zero()
            a[7] = -0.25  # straight reverse (drivers.py back-out recipe)
            a[D.GRIP] = D.GRIP_OPEN
            a[D.MODE] = D.GRIP_CLOSE  # +1 == base mode
            return a
        # redock: re-drive the fixture dock with a fresh NavigateDriver
        if self._nav is None:
            if self.fixture_name is None:
                raise ValueError("redock recovery needs the active stage's fixture_name")
            self._nav = D.NavigateDriver(self.fixture_name)
        return self._nav.act(env, obs)


if __name__ == "__main__":
    # Base-importable self-check: monkeypatch the drivers' live-state readers so the
    # phase->action dispatch runs without a real sim, and assert the base_mode
    # discipline (arm phases MODE=-1, base phases MODE=+1) and the grip transitions.
    import plugins.embodiment_robocasa.drivers as _D

    _D._eef = lambda env: np.array([1.0, 2.0, 3.0])
    _D._base_pose = lambda env: (np.array([0.0, 0.0]), 0.0)
    _D._obj_pos = lambda env, n: np.array([1.1, 2.1, 0.9])

    steps = (("unclench", 2, 0.0, 0.0), ("raise", 2, 0.0, 0.0),
             ("reseat", 2, 0.0, 0.0), ("clench", 2, 0.0, 0.0))
    act = RobocasaRecoveryActor(object(), "regrasp_kitchen", steps, obj_name="meat")
    assert len(act.queue) == 8, act.queue
    grips = []
    modes = []
    while not act.done:
        a = act.act({})
        assert a.shape == (_D.ADIM,)
        modes.append(a[_D.MODE])
        grips.append(a[_D.GRIP])
    assert all(m == _D.GRIP_OPEN for m in modes), ("regrasp is arm mode throughout", modes)
    assert grips[0] == _D.GRIP_OPEN and grips[-1] == _D.GRIP_CLOSE, ("unclench->clench", grips)
    assert act.done and RobocasaRecoveryActor(object(), "x", (), ).done

    # base-mode recovery: backout is a real reverse, redock delegates to NavigateDriver
    class _FakeNav:
        def __init__(self, fx):
            self.fx = fx

        def act(self, env, obs):
            a = _D._zero()
            a[_D.MODE] = _D.GRIP_CLOSE  # +1 base mode
            a[7] = 0.5
            return a

    _D.NavigateDriver = _FakeNav
    bsteps = (("backout", 1, 0.0, 0.0), ("redock", 1, 0.0, 0.0))
    bact = RobocasaRecoveryActor(object(), "redock_retry", bsteps, fixture_name="fridge")
    a0 = bact.act({})
    assert a0[_D.MODE] == _D.GRIP_CLOSE and a0[7] < 0, ("backout reverses in base mode", a0)
    a1 = bact.act({})
    assert a1[_D.MODE] == _D.GRIP_CLOSE and a1[7] == 0.5, ("redock delegates to nav", a1)

    # an unknown phase fails loudly (a proposer cannot invent a motor phase)
    try:
        RobocasaRecoveryActor(object(), "x", (("teleport", 1, 0.0, 0.0),))
    except ValueError:
        pass
    else:
        raise AssertionError("unknown recovery phase must fail loudly")
    print("plugins/embodiment_robocasa/recovery.py self-check OK")
