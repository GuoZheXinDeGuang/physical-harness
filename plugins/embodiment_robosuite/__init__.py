"""Robosuite embodiment: the reference EnvProvider for capability `embodiment.env`.

Thin adapter, on purpose. All of the numeric/RNG-sensitive work stays exactly
where it was -- `governor.env._default_make_env` -- so mounting this provider
through the kernel and calling `governor.env.make_env` with no ref must build
byte-identical environments. See governor/env.py's `make_env` docstring for the
dispatch this plugin is mounted behind, and ARCHITECTURE.md's "L0 迁移方式".
"""

from __future__ import annotations

from typing import Any

import governor.env as _env


class RobosuiteEmbodiment:
    """Layer 3 `harness.contracts.EnvProvider`, backed by governor.env verbatim."""

    def make_env(self, spec: Any) -> Any:
        return _env._default_make_env(spec)

    def tasks(self) -> tuple[str, ...]:
        return tuple(sorted(_env.TASKS))


def provider(**params: Any) -> RobosuiteEmbodiment:
    return RobosuiteEmbodiment(**params)
