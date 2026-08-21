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

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import numpy as np

from harness.registry import load_provider
from harness.spec import NOMINAL_SCHEDULE, EpisodeSpec  # noqa: F401  re-export: currency moved to the kernel
from plugins.embodiment_robosuite.env import (  # noqa: F401  re-export: embodiment moved to its plugin (L2 rung H)
    CONTROL_FREQ, TASKS, _default_make_env, lifted, object_key, task_config)

PHASE_HEIGHT = {"above": 0.10, "descend": 0.005, "close": 0.005, "lift": 0.25}

def make_env(spec: EpisodeSpec):
    """Build one environment for `spec`.

    Dispatch point for the embodiment.env capability seam: when `spec.env_provider`
    names a provider ("module:factory"), it is loaded via
    `harness.registry.load_provider` and asked to build the env. With no ref, this
    falls back to `_default_make_env`, the original robosuite path -- so a spec
    with no ref behaves byte-identically to before this seam existed.
    """
    ref = spec.env_provider
    if ref is not None:
        provider = load_provider(ref)
        return provider.make_env(spec)
    return _default_make_env(spec)


@dataclass(slots=True)
class FrozenPolicy:
    """The black-box policy under governance. Never learns, never retries."""

    spec: EpisodeSpec
    target: np.ndarray = field(default=None, repr=False)

    def observe_once(self, obs: Mapping[str, np.ndarray]) -> np.ndarray:
        """Take the single noisy percept the policy will act on for the whole episode."""
        rng = np.random.RandomState(self.spec.seed * 7919 + 11)
        sd = self.spec.percept_noise
        self.target = np.asarray(obs[object_key(self.spec)]).copy() + np.array(
            [rng.normal(0, sd), rng.normal(0, sd), 0.0]
        )
        return self.target

    def act(self, obs: Mapping[str, np.ndarray], phase: str) -> np.ndarray:
        """One 7-dof OSC_POSE action toward the phase goal, from the stale percept."""
        height = PHASE_HEIGHT[phase]
        if phase in ("descend", "close"):
            height += self.spec.grasp_height_offset
        goal = np.array([self.target[0], self.target[1], self.target[2] + height])
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
    """Run one un-governed episode.

    Delegates to :func:`governor.governed.governed_rollout` with no bundle so
    there is exactly ONE rollout implementation. Two of them would drift, and a
    gate whose two arms ran different code would measure the drift instead of
    the governance.
    """
    from governor.governed import governed_rollout

    return governed_rollout(spec, None)
