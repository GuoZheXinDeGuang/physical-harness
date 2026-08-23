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

from harness.registry import load_provider
from harness.spec import (  # noqa: F401  re-export: currency moved to the kernel
    NOMINAL_SCHEDULE,
    EpisodeSpec,
)

#: Names that live in the embodiment card and are re-exported here for the legacy
#: import paths (plugins.policies.demos, the percept plugin, a couple of tests).
#: Forwarded lazily via PEP 562 (round 96 R2) so `import governor.env` -- and the
#: base names above (rollout, make_env, EpisodeSpec, NOMINAL_SCHEDULE) -- resolve
#: with the embodiment card UNPLUGGED. Touching a card name pulls the card; a base
#: consumer that never touches one never does. Same pattern as governor/policy.py.
_CARD_NAMES = ("CONTROL_FREQ", "TASKS", "_default_make_env", "lifted",
               "object_key", "task_config")


def __getattr__(name: str):
    if name in _CARD_NAMES:
        import plugins.embodiment_robosuite.env as _env

        return getattr(_env, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def make_env(spec: EpisodeSpec):
    """Build one environment for `spec`.

    Dispatch point for the embodiment.env capability seam: when `spec.env_provider`
    names a provider ("module:factory"), it is loaded via
    `harness.registry.load_provider` and asked to build the env. With no ref, this
    falls back to `_default_make_env`, the original robosuite path -- so a spec
    with no ref behaves byte-identically to before this seam existed. That path
    imports the card lazily (round 96 R2): the shim itself no longer hard-imports
    it at module load.
    """
    ref = spec.env_provider
    if ref is not None:
        provider = load_provider(ref)
        return provider.make_env(spec)
    import plugins.embodiment_robosuite.env as _env

    return _env._default_make_env(spec)


def rollout(spec: EpisodeSpec) -> dict:
    """Run one un-governed episode.

    Delegates to :func:`plugins.rsi.governed.governed_rollout` with no bundle so
    there is exactly ONE rollout implementation. Two of them would drift, and a
    gate whose two arms ran different code would measure the drift instead of
    the governance.
    """
    from plugins.rsi.governed import governed_rollout

    return governed_rollout(spec, None)
