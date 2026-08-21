"""The frozen-policy seam, and the recovery hand-back contract.

Two providers
-------------
``ScriptedDriver`` is the four-phase open-loop policy the project has used since
round 1: one noisy percept at t=0, fixed phase durations, no retry.

``ClonedDriver`` is a behaviour-cloned MLP (governor/bc.py) running closed loop.
Its errors come from the fit rather than from a schedule anyone wrote, which is
what makes it the honest test of whether this harness only finds failures its
author designed.

Hand-back
---------
Both are black boxes, so a recovery cannot re-target them from the inside. A
fired critic hands control to a scripted recovery actor for the length of its
program and then hands it back -- Zetta's VLA re-entry contract
(Zetta-Embodiment/zetta/evolution/models.py, ``RecoveryRule``). Recovery steps do
not consume the policy's own clock: the scripted driver resumes at the phase it
was interrupted in, and the cloned driver resumes from a reset clock, because a
clock beyond its training horizon is out of distribution and would be a second,
uncontrolled failure source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from governor.env import PHASE_HEIGHT, EpisodeSpec, FrozenPolicy, phase_at
from harness.registry import load_provider


class PolicyDriver(Protocol):
    """A frozen policy. `act` is called only on steps the policy owns."""

    def observe_once(self, obs) -> np.ndarray: ...
    def act(self, obs) -> np.ndarray: ...
    def retarget(self, target: np.ndarray) -> None: ...
    def on_handback(self) -> None: ...
    @property
    def exhausted(self) -> bool: ...
    @property
    def identity(self) -> str: ...


class ScriptedDriver:
    """The four-phase open-loop policy, driven by its own phase counter."""

    def __init__(self, spec: EpisodeSpec) -> None:
        self.spec = spec
        self.inner = FrozenPolicy(spec)
        self.k = 0                      # policy-owned steps; recovery does not advance it

    def observe_once(self, obs) -> np.ndarray:
        return self.inner.observe_once(obs)

    def act(self, obs) -> np.ndarray:
        phase = phase_at(self.spec.schedule, self.k) or self.spec.schedule[-1][0]
        action = self.inner.act(obs, phase)
        self.k += 1
        return action

    def retarget(self, target: np.ndarray) -> None:
        self.inner.target = target

    def on_handback(self) -> None:
        """Skip the remainder of the interrupted phase.

        A recovery is a complete regrasp ending in close-and-lift, so the phase
        it interrupted has been superseded -- resuming the tail of a `close`
        that the repair already performed re-closes on an object now held.
        Measured on 200 held-out seeds: superseding scores +27.5pp, resuming
        scores +14.0pp with four regressions. The earlier splice implementation
        had this behaviour implicitly, by dropping the rest of the phase it
        broke out of; making it explicit is what let it be compared at all.
        """
        acc = 0
        for _name, dur in self.spec.schedule:
            acc += dur
            if self.k < acc:
                self.k = acc
                return

    @property
    def exhausted(self) -> bool:
        return self.k >= sum(d for _, d in self.spec.schedule)

    @property
    def identity(self) -> str:
        return "scripted@v1"


class ClonedDriver:
    """A behaviour-cloned MLP running closed loop."""

    #: Clock horizon the clone was trained over.
    HORIZON = 100

    def __init__(self, spec: EpisodeSpec, weights: Path) -> None:
        from governor.bc import MLPPolicy

        self.spec = spec
        self.net = MLPPolicy.load(Path(weights))
        self.percept = FrozenPolicy(spec)
        self.k = 0

    def observe_once(self, obs) -> np.ndarray:
        return self.percept.observe_once(obs)

    def act(self, obs) -> np.ndarray:
        from governor.bc import encode

        action = self.net.act(encode(obs, self.percept.target, self.k / self.HORIZON))
        self.k += 1
        return action

    def retarget(self, target: np.ndarray) -> None:
        self.percept.target = target

    def on_handback(self) -> None:
        """Resume in the regime the recovery advanced the task to, not at zero.

        The recovery ends holding the object aloft. Restarting the clock puts the
        clone back in its approach regime with the gripper opening, which undoes
        the repair -- measured at -2.0pp with 3 fixed and 7 broken. This is the
        same error the scripted driver's hand-back already avoids by superseding
        the interrupted phase rather than resuming it.

        The clock is set to where the demonstrator's own schedule enters `lift`,
        which is the phase the recovery program terminates in. That mapping comes
        from the demonstrations the clone was trained on, not from tuning.
        """
        acc = 0
        for name, dur in self.spec.schedule:
            if name == "lift":
                self.k = acc
                return
            acc += dur
        self.k = acc

    @property
    def exhausted(self) -> bool:
        return self.k >= self.HORIZON

    @property
    def identity(self) -> str:
        return f"cloned@{self.net.sha()[:12]}"


class RecoveryActor:
    """Scripted repair that owns control for the length of its program.

    A program is a sequence of SEGMENTS. A fixed segment replays a phase for a
    set number of steps; a servo segment runs a closed-loop primitive from
    :mod:`governor.servo` and decides its own length from proprioception. Mixing
    them is the point: the approach can stay open-loop while the part that
    actually has to make contact stops guessing at an estimated height.
    """

    def __init__(self, program, target: np.ndarray, height_offset: float = 0.0) -> None:
        from governor.servo import make as make_servo

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
            height = PHASE_HEIGHT[name]
            if name in ("descend", "close"):
                height += self.height_offset
            goal = np.array([self.target[0] + dx, self.target[1] + dy,
                             self.target[2] + height])
            delta = np.clip((goal - np.asarray(obs["robot0_eef_pos"])) * 8.0, -1, 1)
            grip = 1.0 if name in ("close", "lift") else -1.0
            return np.array([*delta, 0.0, 0.0, 0.0, grip])
        return seg.act(obs)

    @property
    def done(self) -> bool:
        return self._current() is None


def make_driver(spec: EpisodeSpec) -> PolicyDriver:
    """Resolve the spec's frozen policy driver.

    Dispatch point for the policy.driver capability seam: when `spec.policy_provider`
    names a provider ("module:factory"), it is loaded via
    `harness.registry.load_provider` and asked to build the driver. With no ref,
    this falls back to `_default_make_driver`, the original scripted/cloned
    dispatch -- so a spec with no ref behaves byte-identically to before this
    seam existed. Same string-not-hook rationale as `governor.env.make_env`.
    """
    ref = spec.policy_provider
    if ref is not None:
        provider = load_provider(ref)
        return provider.make_driver(spec)
    return _default_make_driver(spec)


def _default_make_driver(spec: EpisodeSpec) -> PolicyDriver:
    """Resolve the spec's frozen policy."""
    if spec.policy in (None, "", "scripted"):
        return ScriptedDriver(spec)
    return ClonedDriver(spec, Path(spec.policy))
