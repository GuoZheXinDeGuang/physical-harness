"""A from-scratch ``harness.contracts.TaskPlanner`` for ``lift_geometric``.

The honest dogfood of the README "从零写 planner 的契约" section: a single-node
plan, card-authored CATALOGUE + ORACLES, emitted in the exact shape the
fail-first validator admits (``plugins.task.validate.validate_plan``) --
``{goal, nodes:[{id,skill,args,after}], verify:[{after,predicate}]}``. One
``grasp`` node whose success is checked by the shared ``lifted`` sub-goal.

Deterministic table lookup like ``skill_toy.planner`` / ``planner_stack``: same
brief -> byte-identical plan (体检 Tier B's determinism policy, and the runtime's
replay). This is the SYMBOLIC layer; the geometric grasp's measured execution
path is the acceptance campaign (evolution-mode ``governed_rollout`` on the lift
task under ``lift_geometric_provider``), not the generic task loop -- so the
node's ``grasp`` skill is card vocabulary, not a ``plugins.task.workload``
SKILL_SPECS execution binding (that binding is a follow-up when the plan-act loop
needs to drive geometric grasps directly).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: The one skill this task decomposes to, and the predicate that verifies it --
#: the card's own vocabulary, imported by the runtime by ref (types are objects,
#: so a ref not JSON). ``lifted`` is the shared lift sub-goal the campaign scores.
CATALOGUE: dict[str, dict[str, type]] = {"grasp": {"object": str}}
ORACLES: tuple[str, ...] = ("lifted",)


class LiftGeometricPlanner:
    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "lift_geometric":
            raise ValueError(
                f"LiftGeometricPlanner only plans 'lift_geometric', got {task!r}")
        # Round-trip through sorted JSON, same as StackPlanner: the emitted
        # mapping is exactly its canonical byte form.
        return json.loads(json.dumps({
            "goal": "lift the cube with a geometric (zero-privilege) grasp",
            "nodes": [{"id": "grasp-0", "skill": "grasp",
                       "args": {"object": "cube"}, "after": []}],
            "verify": [{"after": "grasp-0", "predicate": "lifted"}],
        }, sort_keys=True))

    @property
    def identity(self) -> str:
        return "lift_geometric_planner@v1"


def provider(**params: Any) -> LiftGeometricPlanner:
    return LiftGeometricPlanner(**params)
