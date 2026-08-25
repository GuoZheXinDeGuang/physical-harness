"""Onboard object-pose estimate for the robocasa card: privileged obs + noise.

RoboCasa ships no noise model (install report §3.5), so the wrapper adds it --
same math and the same deterministic-in-(seed, draw) discipline as the robosuite
card's OnboardPercept, so the two simulators degrade perception identically and
plugin_doctor Tier B's determinism-required smoke passes here too. xy perturbed,
z exact; at or below the privileged floor it returns ground truth.
"""

from __future__ import annotations

import numpy as np

from harness.percept import PRIVILEGED_SENSOR_SD
from plugins.embodiment_robocasa.env import object_key


class OnboardPercept:
    """Deterministic-in-(seed, draw) noisy estimate; xy perturbed, z exact."""

    def object_estimate(self, obs, spec, sensor_sd: float, draw: int) -> np.ndarray:
        true = np.asarray(obs[object_key(spec)]).copy()
        if sensor_sd <= PRIVILEGED_SENSOR_SD:
            return true
        rng = np.random.RandomState((spec.seed * 104729 + 3 + draw * 7907) % (2**31 - 1))
        return true + np.array([rng.normal(0, sensor_sd), rng.normal(0, sensor_sd), 0.0])


def provider() -> OnboardPercept:
    return OnboardPercept()
