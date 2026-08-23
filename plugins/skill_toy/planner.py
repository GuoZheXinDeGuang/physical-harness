"""A toy ``harness.contracts.TaskPlanner``: one node, one new task name.

Deterministic table lookup like ``planner_stack.StackPlanner``, but keyed on the
NEW ``toy`` task. It emits a single ``stack`` skill node -- the base skill whose
execution binding already lives in ``plugins.task.workload.SKILL_SPECS`` -- so the
whole plan validates and dispatches with zero base edits. CATALOGUE/ORACLES are
the skill-authored vocabulary the manifest points the runtime at by ref.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: The one skill this toy task decomposes to, and the predicate it verifies --
#: both borrowed from the base stack vocabulary, imported by the runtime by ref.
CATALOGUE: dict[str, dict[str, type]] = {"stack": {"object": str, "target": str}}
ORACLES: tuple[str, ...] = ("stack_success",)


class ToyPlanner:
    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "toy":
            raise ValueError(f"ToyPlanner only plans 'toy', got {task!r}")
        # Round-trip through sorted JSON, same as StackPlanner: the emitted
        # mapping is exactly its canonical byte form.
        return json.loads(json.dumps({
            "goal": "toy demo: stack cubeA on cubeB",
            "nodes": [{"id": "toy-0", "skill": "stack",
                       "args": {"object": "cubeA", "target": "cubeB"},
                       "after": []}],
            "verify": [{"after": "toy-0", "predicate": "stack_success"}],
        }, sort_keys=True))

    @property
    def identity(self) -> str:
        return "toy_planner@v1"


def provider(**params: Any) -> ToyPlanner:
    return ToyPlanner(**params)
