"""Robosuite embodiment: the reference EnvProvider for capability `embodiment.env`.

Thin adapter, on purpose. All of the numeric/RNG-sensitive work stays exactly
where it was -- `governor.env._default_make_env` -- so mounting this provider
through the kernel and calling `governor.env.make_env` with no ref must build
byte-identical environments. See governor/env.py's `make_env` docstring for the
dispatch this plugin is mounted behind, and ARCHITECTURE.md's "L0 迁移方式".
"""

from __future__ import annotations

import dataclasses
from typing import Any

import plugins.embodiment_robosuite.env as _env

# The observation-feature catalog moved to base ownership in round 96 (R2):
# harness.feature_catalog declares it and harness.features populates REGISTRY on
# import, so this card no longer registers anything. A future embodiment that
# exposes DIFFERENT features would register them here (harness.features.register)
# on mount; robosuite's features ARE the base catalog, so it declares none.


class RobosuiteEmbodiment:
    """Layer 3 `harness.contracts.EnvProvider`, backed by governor.env verbatim.

    `robot=None` leaves the spec untouched (byte-identical to the legacy path,
    which is what parity holds onto). A named robot overrides the spec's field
    before construction -- that is how a second embodiment becomes a PROVIDER
    CHOICE: mount refs are zero-arg factories on purpose (Mount params cannot
    travel to spawn workers, and the workload refuses them), so each embodiment
    variant is its own factory function, not a parameterised one.
    """

    def __init__(self, robot: str | None = None) -> None:
        self.robot = robot

    def make_env(self, spec: Any) -> Any:
        if self.robot is not None and spec.robot != self.robot:
            spec = dataclasses.replace(spec, robot=self.robot)
        return _env._default_make_env(spec)

    def tasks(self) -> tuple[str, ...]:
        return tuple(sorted(_env.TASKS))

    def object_key(self, spec: Any) -> str:
        return _env.object_key(spec)

    def success(self, obs: Any, spec: Any, start_z: float) -> bool:
        return _env.lifted(obs, spec, start_z)

    def terminal_success(self, obs: Any, spec: Any, start_z: float, env: Any = None) -> bool:
        """OPTIONAL contract extension (see harness.contracts.EnvProvider):
        the full-task terminal boolean, the only thing a gate may consume.

        Stack's terminal predicate is the simulator's own `_check_success()`
        (lifted off the table AND touching cubeB AND released), which reads
        contact ground truth the obs dict cannot carry -- hence the env
        handle. lift/pickcan fall back to the shared sub-goal. No handle on
        stack fails loudly rather than mislabelling.
        """
        if spec.task == "stack":
            if env is None:
                raise ValueError("stack terminal success needs the live env handle "
                                 "(env._check_success reads contact ground truth)")
            return bool(env._check_success())
        return self.success(obs, spec, start_z)


def provider() -> RobosuiteEmbodiment:
    return RobosuiteEmbodiment()


def sawyer_provider() -> RobosuiteEmbodiment:
    """Second embodiment, one factory: Rethink Sawyer instead of Franka Panda."""
    return RobosuiteEmbodiment(robot="Sawyer")
