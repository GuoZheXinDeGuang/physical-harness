"""Recovery execution: the intervention machinery a promoted rule triggers.

Lives in the RSI plugin, not the policies plugin, because recovery is the
workload's half of the control hand-off: drivers own nominal behaviour,
recovery owns the correction, and the plugin boundary between them is the same
line the hand-back contract draws at runtime.
"""

from __future__ import annotations

import numpy as np

from harness.spec import PHASE_HEIGHT, STACK_PHASE_HEIGHT

#: Grasp + place vocabulary, merged exactly as StackScriptedDriver._HEIGHT is
#: (plugins/policies/drivers.py). The two key sets are disjoint, so this adds
#: the place-phase heights (over_b/place/release/retreat) and leaves every
#: grasp-phase height byte-identical -- a regrasp program resolves as before.
_HEIGHT = {**PHASE_HEIGHT, **STACK_PHASE_HEIGHT}


class RecoveryActor:
    """Scripted repair that owns control for the length of its program.

    A program is a sequence of SEGMENTS. A fixed segment replays a phase for a
    set number of steps; a servo segment runs a closed-loop primitive from
    :mod:`plugins.rsi.servo` and decides its own length from proprioception. Mixing
    them is the point: the approach can stay open-loop while the part that
    actually has to make contact stops guessing at an estimated height.
    """

    def __init__(self, program, target: np.ndarray, height_offset: float = 0.0) -> None:
        from plugins.rsi.servo import make as make_servo

        self.target = target
        #: Round 61: recovery goals take the same per-embodiment vertical
        #: correction the policy does. Without it the Sawyer campaign's
        #: candidate judged perfectly (+20.7pp against its blind twin) and
        #: repaired nothing (0 fixed): the regrasp closed 1cm above the cube
        #: every single time. Detection transferred; repair was Panda-tuned.
        self.height_offset = height_offset
        self.segments: list = []
        for step in program:
            kind = step[0]
            if kind.startswith("servo_"):
                _k, budget = step[0], step[1]
                kw = {"max_steps": budget}
                if kind == "servo_descend":
                    kw["target_xy"] = np.asarray(target)[:2]
                if kind == "servo_probe":
                    kw["centre"] = np.asarray(target)
                self.segments.append(make_servo(kind, **kw))
            else:
                name, dur, dx, dy = step
                self.segments.append([(name, dx, dy)] * dur)
        self._i = 0

    def _current(self):
        while self._i < len(self.segments):
            seg = self.segments[self._i]
            if isinstance(seg, list):
                if seg:
                    return seg
            elif not seg.done:
                return seg
            self._i += 1
        return None

    def act(self, obs) -> np.ndarray:
        seg = self._current()
        if isinstance(seg, list):
            name, dx, dy = seg.pop(0)
            height = _HEIGHT[name]
            if name in ("descend", "close"):
                height += self.height_offset
            goal = np.array([self.target[0] + dx, self.target[1] + dy,
                             self.target[2] + height])
            delta = np.clip((goal - np.asarray(obs["robot0_eef_pos"])) * 8.0, -1, 1)
            # Mirrors StackScriptedDriver._GRIP_CLOSED: a place-phase program
            # keeps the cube held through over_b/place and only opens on
            # release/retreat. Old grasp programs contain none of the extra
            # names, so their grip resolves exactly as before.
            grip = 1.0 if name in ("close", "lift", "over_b", "place") else -1.0
            return np.array([*delta, 0.0, 0.0, 0.0, grip])
        return seg.act(obs)

    @property
    def done(self) -> bool:
        return self._current() is None
