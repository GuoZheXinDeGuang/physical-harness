"""Contracts the three research layers plug into.

These Protocols are the backbone's public surface: the reasoning team provides
a Reasoner, the graphs team provides SceneGraph/SkillGraph, the control team
provides EnvProvider/PolicyFactory. The kernel checks structural conformance at
mount time so a wrong-shaped provider fails where it is mounted, not
mid-episode.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EnvProvider(Protocol):
    """Layer 3: an embodiment that can build environments for episode specs.

    The semantic hooks exist so consumers (the rsi workload above all) reach
    "which observation key holds the target" and "did the shared sub-goal
    succeed" through the contract instead of importing this embodiment's
    plugin -- the coupling that would otherwise block governed's own move.
    """

    def make_env(self, spec: Any) -> Any: ...
    def tasks(self) -> tuple[str, ...]: ...
    def object_key(self, spec: Any) -> str: ...
    def success(self, obs: Mapping, spec: Any, start_z: float) -> bool: ...


@runtime_checkable
class GroundTruthState(Protocol):
    """Privileged: simulator-only oracle state. A real deployment mounts no provider."""

    def object_pose(self, obs: Mapping, spec: Any) -> Any: ...


@runtime_checkable
class PolicyFactory(Protocol):
    """Layer 3: builds the frozen policy driver an episode runs under."""

    def make_driver(self, spec: Any) -> Any: ...


@runtime_checkable
class PerceptModel(Protocol):
    """Onboard estimate of task-relevant state, degradable for ablation."""

    def object_estimate(self, obs: Mapping, spec: Any, sensor_sd: float, draw: int) -> Any: ...


@runtime_checkable
class RolloutExecutor(Protocol):
    """Execution fabric: local pool today, distributed later, same contract."""

    def map(self, fn: Any, items: Sequence, *, workers: int) -> list: ...


@runtime_checkable
class Reasoner(Protocol):
    """Layer 1 seam. Today: propose from an evidence brief. Grows with the VLM team's interface."""

    def propose(self, brief: Mapping) -> Mapping: ...


@runtime_checkable
class SkillGraph(Protocol):
    """Layer 2 seam: measured skills with preconditions, effects, failure modes, capability boundaries."""

    def publish(self, record: Mapping) -> str: ...
    def skills(self) -> tuple[Mapping, ...]: ...


@runtime_checkable
class SceneGraph(Protocol):
    """Layer 2 seam: object/relation snapshot of the world."""

    def snapshot(self, obs: Mapping) -> Mapping: ...
