"""The demonstration pipeline is part of the artefact, not a scratch script."""
import numpy as np
import pytest

# demos.collect_one drives real robosuite rollouts through the governor.env shim;
# skip cleanly when the embodiment card is unplugged (R2 card-removal decoupling).
pytest.importorskip("plugins.embodiment_robosuite.env")

from plugins.policies.demos import collect_one


def test_a_demonstration_is_reproducible_from_its_seed():
    """Same (seed, sigma) must give byte-identical data.

    The clone in version control was produced by a pipeline that was not, so
    nobody could have re-derived it. Reproducibility starts here.
    """
    a = collect_one((20000, 0.15, "lift", 0.004))
    b = collect_one((20000, 0.15, "lift", 0.004))
    assert a is not None and b is not None
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_noise_changes_the_states_visited_but_not_the_labels_bounds():
    """DART perturbs what is EXECUTED; the label stays the expert's own action."""
    clean = collect_one((20000, 0.0, "lift", 0.004))
    noisy = collect_one((20000, 0.30, "lift", 0.004))
    assert clean is not None and noisy is not None
    assert not np.array_equal(clean[0][: min(len(clean[0]), len(noisy[0]))],
                              noisy[0][: min(len(clean[0]), len(noisy[0]))]), \
        "injected noise did not change the visited states"
    for _, y in (clean, noisy):
        assert np.all(np.abs(y) <= 1.0 + 1e-9), "labels left the action range"


def test_collect_one_threads_the_env_provider_ref():
    """Round 62: the last L0 limitation closes -- demos can run on a mounted
    embodiment. Legacy 4-tuple args stay valid (byte-identical default path)."""
    import numpy as np

    from plugins.policies.demos import collect_one

    legacy = collect_one((20000, 0.15, "lift", 0.004))
    with_ref = collect_one((20000, 0.15, "lift", 0.004,
                            "plugins.embodiment_robosuite:provider"))
    assert legacy is not None and with_ref is not None
    assert np.array_equal(legacy[0], with_ref[0]) and np.array_equal(legacy[1], with_ref[1]), \
        "the default provider must reproduce the legacy path bit for bit"
