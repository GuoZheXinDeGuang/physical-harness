"""A from-scratch ``harness.contracts.TaskPlanner`` for ``clear_build`` (M5).

The first LONG-horizon graph: four nodes over THREE distinct SKILL_SPECS
bindings (grasp / pick / stack), emitted in the exact shape the fail-first
validator admits (``plugins.task.validate.validate_plan``) --
``{goal, nodes:[{id,skill,args,after}], verify:[{after,predicate}]}``.

Deterministic table lookup like ``planner_stack`` / ``skill_geometric_grasp``:
same brief -> byte-identical plan (体检 determinism policy + the runtime's
replay). This is the SYMBOLIC layer; each node's measured execution is one
``governed_rollout`` the generic ``plugins.task.workload`` loop dispatches. The
CATALOGUE/ORACLES are card-authored (types are objects, so a ref, not JSON):
mixed predicates (``lifted`` / ``pick_success`` / ``stack_success``) are already
admitted node-by-node -- the workload scores ``result["success"]`` per node
regardless of predicate name, so no workload change is needed.

Node order encodes the clear-then-build narrative (grasp, clear the can, clear
the milk, build the tower). Governance touches only the stack node; ordering it
LAST maximizes the narrative but dilutes measurable power by ``q_pre`` = P(the
three earlier nodes all succeed) -- the calibration block measures it and the
go/no-go gate is on it (docs/long-horizon-design.md §4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: skill name -> {arg name: required python type}: the card's own vocabulary,
#: imported by the runtime by ref. grasp/pick/stack are typed here for the
#: validator; their EXECUTION bindings live in plugins.task.workload.SKILL_SPECS.
CATALOGUE: dict[str, dict[str, type]] = {
    "grasp": {"object": str},
    "pick": {"object": str},
    "stack": {"object": str, "target": str},
}

#: Verify predicates a plan may name -- one per skill's terminal success boolean.
ORACLES: tuple[str, ...] = ("lifted", "pick_success", "stack_success")


class ClearBuildPlanner:
    """Layer 1 ``harness.contracts.TaskPlanner``: deterministic table lookup."""

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "clear_build":
            raise ValueError(
                f"ClearBuildPlanner only plans 'clear_build', got {task!r}")
        # Round-trip through sorted JSON, same as StackPlanner: the emitted
        # mapping is exactly its canonical byte form.
        return json.loads(json.dumps({
            "goal": "clear the can and the milk, then build the tower",
            "nodes": [
                {"id": "grasp-cube", "skill": "grasp",
                 "args": {"object": "cube"}, "after": []},
                {"id": "pick-can", "skill": "pick",
                 "args": {"object": "can"}, "after": []},
                {"id": "pick-milk", "skill": "pick",
                 "args": {"object": "milk"}, "after": ["pick-can"]},
                {"id": "build-stack", "skill": "stack",
                 "args": {"object": "cubeA", "target": "cubeB"},
                 "after": ["pick-milk"]},
            ],
            "verify": [
                {"after": "grasp-cube", "predicate": "lifted"},
                {"after": "pick-can", "predicate": "pick_success"},
                {"after": "pick-milk", "predicate": "pick_success"},
                {"after": "build-stack", "predicate": "stack_success"},
            ],
        }, sort_keys=True))

    @property
    def identity(self) -> str:
        return "clear_build_planner@v1"


def provider(**params: Any) -> ClearBuildPlanner:
    return ClearBuildPlanner(**params)


if __name__ == "__main__":
    # No cross-plugin import: this card may not import plugins.task.validate
    # (tests/test_boundaries.py). Structural shape is asserted here; the real
    # validate_plan coverage lives in tests/test_clear_build.py (a test may
    # import both).
    planner = ClearBuildPlanner()
    brief = {"task": "clear_build", "scene": {}, "catalogue": CATALOGUE}
    plan = planner.plan(brief)
    assert set(plan) == {"goal", "nodes", "verify"} and plan["goal"]
    # byte-identical replay
    assert json.dumps(plan, sort_keys=True) == \
        json.dumps(planner.plan(brief), sort_keys=True)
    # four nodes, three distinct skills, the stack node gated behind the milk pick
    assert [n["id"] for n in plan["nodes"]] == \
        ["grasp-cube", "pick-can", "pick-milk", "build-stack"]
    assert {n["skill"] for n in plan["nodes"]} == {"grasp", "pick", "stack"}
    assert plan["nodes"][3]["after"] == ["pick-milk"]
    assert [v["predicate"] for v in plan["verify"]] == \
        ["lifted", "pick_success", "pick_success", "stack_success"]
    try:
        planner.plan({"task": "stack"})
    except ValueError:
        pass
    else:
        raise AssertionError("wrong task must fail loudly")
    print("plugins/clear_build/planner.py self-check OK")
