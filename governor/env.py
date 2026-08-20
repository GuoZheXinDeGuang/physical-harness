"""Deterministic robosuite episode provider and the frozen base policy.

Determinism contract
--------------------
Paired same-seed gating compares "policy alone" against "policy + critic" on
identical seeds, so the two runs must be bit-identical apart from the critic's
own effect. robosuite owns its RNG (``environments/base.py``:
``self.rng = np.random.default_rng(seed)``); seeding ``np.random`` globally does
NOT control it, and a harness that does so silently degrades its gate into a
coin flip. Every environment here is therefore built through
``suite.make(seed=...)``. ``tests/test_determinism.py`` is the regression that
keeps it that way.

The frozen policy
-----------------
A black box, never updated. It takes ONE noisy reading of the cube pose at t=0
and then runs a fixed-duration phase schedule open-loop. This reproduces the
characteristic failure of a real vision-language-action policy -- acting on a
wrong percept, with no contact awareness and no retry -- rather than a control
bug. The failure is recoverable in principle, which is what makes it worth
governing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterator, Mapping

import numpy as np

CONTROL_FREQ = 20
PHASE_HEIGHT = {"above": 0.10, "descend": 0.005, "close": 0.005, "lift": 0.25}
NOMINAL_SCHEDULE: tuple[tuple[str, int], ...] = (
    ("above", 25), ("descend", 25), ("close", 12), ("lift", 38),
)


@dataclass(frozen=True, slots=True)
class EpisodeSpec:
    """Everything that makes one episode reproducible."""

    seed: int
    task: str = "Lift"
    robot: str = "Panda"
    #: robosuite terminates at the horizon and refuses further steps. A governed
    #: episode can splice several recovery programs into its schedule, so this
    #: must cover base + max_generations * program length, not just the nominal 100.
    horizon: int = 900
    #: Std-dev of the frozen policy's one-shot cube-pose percept. The difficulty knob.
    percept_noise: float = 0.020
    #: Std-dev of joint-space reset noise; robosuite's own initialization randomness.
    arm_noise: float = 0.02
    kp: float = 8.0
    schedule: tuple[tuple[str, int], ...] = NOMINAL_SCHEDULE

    def child(self, **kw) -> "EpisodeSpec":
        return replace(self, **kw)


def make_env(spec: EpisodeSpec):
    """Build one deterministic robosuite environment for `spec`."""
    import robosuite as suite

    return suite.make(
        spec.task,
        robots=spec.robot,
        has_renderer=False,
        has_offscreen_renderer=False,
        use_camera_obs=False,
        control_freq=CONTROL_FREQ,
        horizon=spec.horizon,
        initialization_noise={"magnitude": spec.arm_noise, "type": "gaussian"},
        seed=spec.seed,  # the ONLY correct seeding channel; see module docstring
    )


@dataclass(slots=True)
class FrozenPolicy:
    """The black-box policy under governance. Never learns, never retries."""

    spec: EpisodeSpec
    target: np.ndarray = field(default=None, repr=False)

    def observe_once(self, obs: Mapping[str, np.ndarray]) -> np.ndarray:
        """Take the single noisy percept the policy will act on for the whole episode."""
        rng = np.random.RandomState(self.spec.seed * 7919 + 11)
        sd = self.spec.percept_noise
        self.target = np.asarray(obs["cube_pos"]).copy() + np.array(
            [rng.normal(0, sd), rng.normal(0, sd), 0.0]
        )
        return self.target

    def act(self, obs: Mapping[str, np.ndarray], phase: str) -> np.ndarray:
        """One 7-dof OSC_POSE action toward the phase goal, from the stale percept."""
        goal = np.array([self.target[0], self.target[1], self.target[2] + PHASE_HEIGHT[phase]])
        delta = np.clip((goal - np.asarray(obs["robot0_eef_pos"])) * self.spec.kp, -1, 1)
        grip = 1.0 if phase in ("close", "lift") else -1.0
        return np.array([*delta, 0.0, 0.0, 0.0, grip])


def phase_at(schedule: tuple[tuple[str, int], ...], t: int) -> str | None:
    """Phase owning control step `t`, or None once the schedule is exhausted."""
    acc = 0
    for name, dur in schedule:
        if t < acc + dur:
            return name
        acc += dur
    return None


def rollout(spec: EpisodeSpec) -> dict:
    """Run one un-governed episode; return the full per-step feature trace.

    The trace is what a critic would have seen, recorded for every declared
    feature regardless of privilege, so a later analysis can ask what an
    observable-only critic could have known at step t.
    """
    from governor.features import REGISTRY

    env = make_env(spec)
    obs = env.reset()
    policy = FrozenPolicy(spec)
    policy.observe_once(obs)
    names = sorted(REGISTRY)
    trace: dict[str, list[float]] = {n: [] for n in names}
    phases: list[str] = []
    total = sum(d for _, d in spec.schedule)
    for t in range(total):
        phase = phase_at(spec.schedule, t)
        obs, _r, _done, _info = env.step(policy.act(obs, phase))
        for n in names:
            trace[n].append(REGISTRY[n].extract(obs))
        phases.append(phase)
    success = bool(env._check_success())
    env.close()
    return {
        "seed": spec.seed,
        "success": success,
        "steps": total,
        "phases": phases,
        "trace": {n: np.asarray(v) for n, v in trace.items()},
    }
