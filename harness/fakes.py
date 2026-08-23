"""Base-owned null providers: the harness's own boot double.

Every non-privileged capability contract in ``harness.contracts`` gets a null
provider here, so the kernel can boot and run one full governed episode with
ZERO plugins installed -- the "开机" proof (W1). They import only ``harness.*``
+ stdlib (the harness-imports-nothing boundary the AST test enforces), which is
exactly what also lets them double as ``plugin_doctor``'s fixtures (R6).

Null means honest-empty, not fake-success: the env yields canned zero obs and
never reports success, the policy emits a zero action, the reasoner proposes
nothing. A green episode here proves the seams connect, not that anything works.
``embodiment.ground_truth`` gets no fake -- it is privileged and simulator-only,
mounted explicitly only where oracle state is needed (same as ``base_profile``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from harness import contracts
from harness.config import sha_json

#: The one obs key the null env carries; the 3-vector so ``obs[key][2]`` (start_z)
#: works for a governed rollout without a simulator.
_OBJECT_KEY = "object"
_CANNED_OBJECT: tuple[float, float, float] = (0.0, 0.0, 0.0)
#: A 7-DoF-shaped zero action; dimension is arbitrary, the point is "no motion".
_ACTION_DIM = 7


def _canned_obs() -> dict[str, tuple[float, float, float]]:
    return {_OBJECT_KEY: _CANNED_OBJECT}


class _FakeEnvHandle:
    """One episode's env: canned zero obs, never done, close is a no-op."""

    def reset(self) -> dict:
        return _canned_obs()

    def step(self, action: Any) -> tuple[dict, float, bool, dict]:
        return _canned_obs(), 0.0, False, {}

    def close(self) -> None:
        pass


class FakeEnv(contracts.EnvProvider):
    def make_env(self, spec: Any) -> _FakeEnvHandle:
        return _FakeEnvHandle()

    def tasks(self) -> tuple[str, ...]:
        return ("fake",)

    def object_key(self, spec: Any) -> str:
        return _OBJECT_KEY

    def success(self, obs: Mapping, spec: Any, start_z: float) -> bool:
        return False


class _FakeDriver:
    identity = "fake:zero"

    def act(self, obs: Mapping) -> tuple[float, ...]:
        return (0.0,) * _ACTION_DIM


class FakePolicy(contracts.PolicyFactory):
    def make_driver(self, spec: Any) -> _FakeDriver:
        return _FakeDriver()


class FakePercept(contracts.PerceptModel):
    def object_estimate(self, obs: Mapping, spec: Any, sensor_sd: float,
                        draw: int) -> tuple[float, float, float]:
        return _CANNED_OBJECT


class FakePlanner(contracts.TaskPlanner):
    def plan(self, brief: Mapping) -> dict:
        return {"goal": brief.get("task", ""),
                "nodes": [{"id": "n0", "skill": "noop", "args": {}, "after": []}],
                "verify": []}


class FakeReasoner(contracts.Reasoner):
    def propose(self, brief: Mapping) -> dict:
        return {}


class FakeSkillGraph(contracts.SkillGraph):
    """In-memory, content-addressed: ``publish`` is idempotent like the real graph."""

    def __init__(self) -> None:
        self._skills: dict[str, dict] = {}

    def publish(self, record: Mapping) -> str:
        payload = dict(record)
        digest = sha_json(payload)
        self._skills[digest] = payload
        return digest

    def skills(self) -> tuple[Mapping, ...]:
        return tuple(self._skills[k] for k in sorted(self._skills))


class FakeSceneGraph(contracts.SceneGraph):
    def snapshot(self, obs: Mapping) -> dict:
        return {"objects": {}, "relations": ()}


def env_provider() -> FakeEnv:
    return FakeEnv()


def policy_provider() -> FakePolicy:
    return FakePolicy()


def percept_provider() -> FakePercept:
    return FakePercept()


def task_planner_provider() -> FakePlanner:
    return FakePlanner()


def reasoner_provider() -> FakeReasoner:
    return FakeReasoner()


def skill_graph_provider() -> FakeSkillGraph:
    return FakeSkillGraph()


def scene_graph_provider() -> FakeSceneGraph:
    return FakeSceneGraph()
