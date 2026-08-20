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

from governor.env import PHASE_HEIGHT, EpisodeSpec, FrozenPolicy, object_key, phase_at


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
        """Restart the clock: a clone resumed past its training horizon is off
        distribution, which would add an uncontrolled second failure source."""
        self.k = 0

    @property
    def exhausted(self) -> bool:
        return self.k >= self.HORIZON

    @property
    def identity(self) -> str:
        return f"cloned@{self.net.sha()[:12]}"


class RecoveryActor:
    """Scripted repair that owns control for the length of its program."""

    def __init__(self, program, target: np.ndarray) -> None:
        self.queue = [(name, i) for name, dur in program for i in range(dur)]
        self.target = target

    def act(self, obs) -> np.ndarray:
        name, _ = self.queue.pop(0)
        goal = np.array([self.target[0], self.target[1], self.target[2] + PHASE_HEIGHT[name]])
        delta = np.clip((goal - np.asarray(obs["robot0_eef_pos"])) * 8.0, -1, 1)
        grip = 1.0 if name in ("close", "lift") else -1.0
        return np.array([*delta, 0.0, 0.0, 0.0, grip])

    @property
    def done(self) -> bool:
        return not self.queue


def make_driver(spec: EpisodeSpec) -> PolicyDriver:
    """Resolve the spec's frozen policy."""
    if spec.policy in (None, "", "scripted"):
        return ScriptedDriver(spec)
    return ClonedDriver(spec, Path(spec.policy))
