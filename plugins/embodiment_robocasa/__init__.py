"""RoboCasa embodiment: the EnvProvider for capability `embodiment.env`.

Thin adapter over `env.py`, mirroring the robosuite card's __init__ so the two
simulators satisfy one contract (harness.contracts.EnvProvider; Kernel.provide's
isinstance gate, which plugin_doctor Tier A reuses). `env` is imported at module
top, but `env.make_env` imports robocasa LAZILY, so mounting this provider on a
card-absent machine never drags the simulator in.
"""

from __future__ import annotations

from typing import Any

import plugins.embodiment_robocasa.env as _env


class RobocasaEmbodiment:
    """Layer 3 `harness.contracts.EnvProvider`, backed by env.py verbatim."""

    def make_env(self, spec: Any) -> Any:
        return _env.make_env(spec)

    def tasks(self) -> tuple[str, ...]:
        return tuple(sorted(_env.TASKS))

    def object_key(self, spec: Any) -> str:
        return _env.object_key(spec)

    def success(self, obs: Any, spec: Any, start_z: float) -> bool:
        return _env.lifted(obs, spec, start_z)

    def frame(self, obs: Any):
        """harness.media's frame source, one for every driver of this embodiment:
        the camera image already in the obs (env.frame), rendering not required."""
        return _env.frame(obs)

    def terminal_success(self, obs: Any, spec: Any, start_z: float, env: Any = None) -> bool:
        """OPTIONAL contract extension (see harness.contracts.EnvProvider): the
        full-task terminal boolean, the only thing a gate may consume. RoboCasa's
        own `env._check_success()` IS that boolean (kitchen_thaw: food inside the
        microwave AND gripper released AND microwave turned on) -- it reads fixture
        and contact ground truth the obs dict cannot carry, hence the env handle.
        No handle fails loudly rather than mislabelling."""
        if env is None:
            raise ValueError("robocasa terminal success needs the live env handle "
                             "(env._check_success reads fixture/contact ground truth)")
        return bool(env._check_success())


def provider() -> RobocasaEmbodiment:
    return RobocasaEmbodiment()
