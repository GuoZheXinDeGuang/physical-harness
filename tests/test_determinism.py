"""Regression for the paired-gate precondition.

If someone reseeds robosuite through np.random instead of suite.make(seed=),
paired same-seed comparison silently becomes a measurement of simulator noise.
This test fails loudly in that case.
"""
import hashlib

import numpy as np
import pytest

from governor.env import EpisodeSpec, rollout


def _digest(result) -> str:
    flat = np.concatenate([result["trace"][k] for k in sorted(result["trace"])])
    return hashlib.sha256(np.round(flat, 9).tobytes()).hexdigest()


@pytest.mark.parametrize("seed", [0, 3])
def test_same_seed_is_bit_identical(seed):
    a = rollout(EpisodeSpec(seed=seed))
    b = rollout(EpisodeSpec(seed=seed))
    assert _digest(a) == _digest(b), "same-seed reruns diverged; paired gating is invalid"
    assert a["success"] == b["success"]


def test_distinct_seeds_produce_distinct_episodes():
    digests = {_digest(rollout(EpisodeSpec(seed=s))) for s in (0, 1, 2)}
    assert len(digests) == 3, "seeds are not actually controlling the episode"


def test_global_numpy_seed_does_not_leak_into_the_environment():
    """Setting the global seed differently must NOT change a seeded episode."""
    np.random.seed(11)
    a = _digest(rollout(EpisodeSpec(seed=5)))
    np.random.seed(9999)
    b = _digest(rollout(EpisodeSpec(seed=5)))
    assert a == b, "episode depends on global numpy state; seeding channel is wrong"
