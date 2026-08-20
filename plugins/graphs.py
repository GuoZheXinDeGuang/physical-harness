"""Reference implementations of the layer-2 graph contracts.

InMemorySkillGraph is content-addressed the same way campaign artifacts are, so
a published skill's identity is its measurements, not a serial number. The
scene graph is a documented stub for the graphs team to replace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from harness.config import sha_json


class InMemorySkillGraph:
    def __init__(self, root: str | None = None) -> None:
        self._skills: dict[str, dict] = {}
        self._root = Path(root) if root else None
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)

    def publish(self, record: Mapping) -> str:
        payload = dict(record)
        digest = sha_json(payload)
        self._skills[digest] = payload
        if self._root is not None:
            (self._root / f"{digest}.json").write_text(
                json.dumps(payload, sort_keys=True, indent=1, default=str))
        return digest

    def skills(self) -> tuple[Mapping, ...]:
        return tuple(self._skills[k] for k in sorted(self._skills))


class StaticSceneGraph:
    """Stub: returns an empty snapshot. The graphs team replaces this provider."""

    def snapshot(self, obs: Mapping) -> Mapping:
        return {}


def skill_graph_provider(**params: Any) -> InMemorySkillGraph:
    return InMemorySkillGraph(**params)


def scene_graph_provider() -> StaticSceneGraph:
    return StaticSceneGraph()
