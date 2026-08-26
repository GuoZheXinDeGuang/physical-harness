"""LIBERO embodiment (SKELETON): the EnvProvider for capability `embodiment.env`.

Thin adapter over env.py, mirroring the robocasa card's __init__ so a third
simulator satisfies the same contract (harness.contracts.EnvProvider). env is
imported at module top, but env.make_env imports LIBERO lazily, so mounting
this provider on a card-absent machine never drags the simulator in.

No percept.model seam and no terminal_success yet -- those come with the full
embodiment card (LIBERO's bddl goal predicates are the terminal oracle to wrap,
and per this repo's discipline they get audited for discrimination first).
"""

from __future__ import annotations

from typing import Any

import plugins.embodiment_libero.env as _env


class LiberoEmbodiment:
    """Layer 3 `harness.contracts.EnvProvider`, backed by env.py verbatim."""

    def make_env(self, spec: Any) -> Any:
        return _env.make_env(spec)

    def tasks(self) -> tuple[str, ...]:
        return tuple(sorted(_env.TASKS))

    def object_key(self, spec: Any) -> str:
        return _env.object_key(spec)

    def success(self, obs: Any, spec: Any, start_z: float) -> bool:
        return _env.lifted(obs, spec, start_z)


def provider() -> LiberoEmbodiment:
    return LiberoEmbodiment()
