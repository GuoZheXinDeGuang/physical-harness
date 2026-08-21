"""Regression for the paired-gate precondition.

If someone reseeds robosuite through np.random instead of suite.make(seed=),
paired same-seed comparison silently becomes a measurement of simulator noise.
This test fails loudly in that case.
"""
import hashlib

import numpy as np
import pytest

from governor.env import rollout
from harness.spec import EpisodeSpec


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


def test_search_handles_variable_length_governed_traces():
    """A fired recovery makes episodes longer; generation 2 mixes lengths.

    This is a regression for a crash that only appeared at generation 2 of a
    real campaign: np.stack over ragged traces.
    """
    import numpy as np

    from plugins.rsi.stats.search import align, divergence_profile, earliest_divergence

    short = {"observable.finger_gap": np.full(100, 0.04)}
    long_ = {"observable.finger_gap": np.concatenate([np.full(100, 0.001), np.full(112, 0.001)])}
    traces = [short] * 10 + [long_] * 10
    labels = [True] * 10 + [False] * 10
    padded, valid = align([t["observable.finger_gap"] for t in traces])
    assert padded.shape == (20, 212)
    assert valid[0] == 20 and valid[-1] == 10, "validity count must track real samples"
    prof = divergence_profile(traces, labels, "observable.finger_gap")
    assert prof.shape == (212,)
    assert earliest_divergence(traces, labels, "observable.finger_gap") == 0


def test_governed_episode_never_steps_a_terminated_env():
    """Three chained recoveries exceed the nominal horizon; the runner must stop cleanly.

    Regression for a generation-3 campaign crash: robosuite raises
    'executing action in terminated episode' once the horizon is passed.
    """
    from plugins.rsi.governed import Bundle, RecoverySpec, Rule, governed_rollout
    from plugins.rsi.stats.search import Trigger

    always = lambda i: Rule(f"g{i}", Trigger("observable.eef_z", "gt", -1e9, 1, 1),
                            RecoverySpec(sensor_sd=0.02))
    bundle = Bundle(rules=tuple(always(i) for i in range(1, 5)),
                    critic_budget=0, action_budget=0)
    r = governed_rollout(EpisodeSpec(seed=42), bundle)
    assert isinstance(r["success"], bool)
    assert r["steps"] <= EpisodeSpec(seed=42).horizon
