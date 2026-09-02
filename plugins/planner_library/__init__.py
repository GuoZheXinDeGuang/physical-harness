"""planner_library card: PlanRecords first, the inner planner otherwise.

``plan(brief)`` looks through ``brief["plans"]`` (the mounted PlanRecords the
runtime threads from the skills root) for records matching the brief's
(task, embodiment, arm). A hit returns the record's graph with
``planner = {"provider": "library", "plan_id": <record id>}`` -- the highest
``rule.lower`` (the Jeffreys lower bound the record was published under) wins.
No hit delegates to the inner planner unchanged, so every existing binding
keeps its behaviour until a PlanRecord for it exists.

The inner planner is reached by registry ref (harness.registry is the door;
plugins never import each other).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from harness.registry import load_provider


class LibraryPlanner:
    def __init__(self, *, inner: str = "plugins.planner_vlm:provider",
                 inner_params: Mapping[str, Any] | None = None) -> None:
        self._inner_ref = inner
        self._inner_params = dict(inner_params or {})
        self._inner = None

    def _delegate(self):
        if self._inner is None:
            self._inner = load_provider(self._inner_ref, self._inner_params)
        return self._inner

    @property
    def deterministic(self) -> bool:        # plugin_doctor's exemption marker
        return getattr(self._delegate(), "deterministic", True)

    def available(self) -> bool:
        probe = getattr(self._delegate(), "available", None)
        return probe() if probe is not None else True

    @property
    def identity(self) -> str:
        return f"planner_library({self._inner_ref})"

    @staticmethod
    def hits(brief: Mapping) -> list[Mapping]:
        key = (brief.get("task"), brief.get("embodiment"), brief.get("arm", "scripted"))
        return [p for p in brief.get("plans") or ()
                if p.get("kind") == "plan"
                and (p.get("task"), p.get("embodiment"), p.get("arm")) == key]

    def plan(self, brief: Mapping) -> Mapping:
        hits = self.hits(brief)
        if not hits:
            return self._delegate().plan(brief)
        best = max(hits, key=lambda p: float(p["rule"]["lower"]))
        graph = dict(best["graph"])
        graph.setdefault("goal", graph.get("mission", ""))
        return {**graph, "planner": {"provider": "library", "plan_id": best["id"]}}


def provider(**params: Any) -> LibraryPlanner:
    return LibraryPlanner(**params)
