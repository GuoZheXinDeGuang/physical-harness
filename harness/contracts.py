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

    OPTIONAL extension (deliberately NOT in the structural check, so an
    embodiment that only has the shared sub-goal -- a future Isaac provider,
    say -- is not forced to implement it):

        terminal_success(obs, spec, start_z, env=None) -> bool

    The full-task terminal boolean, the only stage-shaped thing a gate may
    ever consume (stage chains are attribution, never gating). Some terminal
    predicates need the live env handle (robosuite Stack's `_check_success`
    reads contact ground truth). Consumers reach it via getattr and fall back
    to `success` when absent.
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
class TaskPlanner(Protocol):
    """Layer 1 seam, sibling to Reasoner: decompose a brief into a skill-call graph.

    ``brief`` carries task/scene/catalogue (and optionally fault/budget); the
    return is one plain-Mapping graph -- ``{goal, nodes: [{id, skill, args,
    after}], verify: [{after, predicate}]}`` -- that names skills from the
    offered catalogue, never inventing them. Structurally minimal so a
    deterministic stand-in and a VLM satisfy the same shape.
    """

    def plan(self, brief: Mapping) -> Mapping: ...


@runtime_checkable
class ModelEndpoint(Protocol):
    """The one model seam: an OpenAI-compatible chat-completions endpoint.

    A local sglang serving and a hosted API (DeepSeek/OpenAI/...) are the same
    shape with a different base_url, so every model-driven seat (a VLM planner,
    a model proposer, ph-station's agent) consumes this ONE contract and the
    provider card owns the HTTP client -- nothing else imports one. ``messages``
    is the OpenAI chat dict shape (``[{"role", "content"}]``; multimodal content
    is the same shape with content-part lists); ``opts`` pass through to the
    request body (temperature, max_tokens, seed, response_format, ...); the
    return is the reply text. ``available()`` is a light HTTP probe so consumers
    and plugin_doctor degrade to a graceful skip when no endpoint is up -- the
    model_qwen precedent.
    """

    def chat(self, messages: Sequence[Mapping], **opts: Any) -> str: ...
    def available(self) -> bool: ...


@runtime_checkable
class SkillGraph(Protocol):
    """Layer 2 seam: measured skills with preconditions, effects, failure modes, capability boundaries."""

    def publish(self, record: Mapping) -> str: ...
    def skills(self) -> tuple[Mapping, ...]: ...


@runtime_checkable
class SceneGraph(Protocol):
    """Layer 2 seam: object/relation snapshot of the world."""

    def snapshot(self, obs: Mapping) -> Mapping: ...
