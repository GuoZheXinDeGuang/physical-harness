"""Onboard object-pose estimate: the ablation ladder's carrier, now a provider.

Moved verbatim from governor.governed._percept_object at L1 rung 1. This is the
first thing a real robot swaps: mount a different percept.model provider and
the recovery stack runs on real perception while every other seam stays put.
The math must stay byte-identical to the phase 1 implementation -- parity
reruns are the check.
"""

from __future__ import annotations

import numpy as np

from governor.env import object_key
from harness.percept import PRIVILEGED_SENSOR_SD


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
