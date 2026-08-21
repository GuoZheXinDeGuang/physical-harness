"""L1 rung 1: the percept implementation lives in the plugin; the seam must be
behaviour-preserving in both directions (default ref and explicit ref)."""

import numpy as np

from harness.contracts import PerceptModel
from harness.registry import load_provider
from harness.spec import EpisodeSpec
from plugins.rsi.governed import DEFAULT_PERCEPT_REF, _percept_object

_OBS = {"cube_pos": np.array([0.02, -0.11, 0.83])}


def _spec(**kw):
    return EpisodeSpec(seed=1234, task="lift", percept_noise=0.004, **kw)


def test_provider_satisfies_the_contract():
    assert isinstance(load_provider(DEFAULT_PERCEPT_REF), PerceptModel)


def test_default_and_explicit_ref_are_bitwise_identical():
    for seed in (0, 7, 4242):
        for draw in (0, 1, 3):
            for sd in (0.01, 0.02):
                spec_default = EpisodeSpec(seed=seed, task="lift", percept_noise=0.004)
                spec_ref = EpisodeSpec(seed=seed, task="lift", percept_noise=0.004,
                                       percept_provider=DEFAULT_PERCEPT_REF)
                a = _percept_object(_OBS, spec_default, sd, draw)
                b = _percept_object(_OBS, spec_ref, sd, draw)
                assert np.array_equal(a, b)


def test_privileged_sd_returns_exact_truth():
    est = _percept_object(_OBS, _spec(), 0.0, 0)
    assert np.array_equal(est, _OBS["cube_pos"])


def test_noise_is_deterministic_in_seed_and_draw_and_xy_only():
    a = _percept_object(_OBS, _spec(), 0.02, 1)
    b = _percept_object(_OBS, _spec(), 0.02, 1)
    c = _percept_object(_OBS, _spec(), 0.02, 2)
    assert np.array_equal(a, b), "same (seed, draw) must reproduce"
    assert not np.array_equal(a, c), "a new draw must move"
    assert a[2] == _OBS["cube_pos"][2], "z is never perturbed (round 21's premise)"
