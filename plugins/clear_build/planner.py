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

Node order puts the GOVERNED stack node FIRST (the design §1/§4.1 lever, taken on
the calibration number not on taste). The v1 clear-then-build order ran stack
LAST; its calibration (runs/scripted-calibration/clear-build-cal) tripped the §4.3 gate -- chains died
mostly at the ungoverned grasp-cube first node (52 ungoverned deaths vs 31 at the
governed stack node), so stack governance could not touch the headline. Ordering
stack first makes every chain reach it (no ``q_pre`` dilution on the governed
node) and makes it the node chains actually die at, which is the precondition
the §4.3 go/no-go gate checks before any dev burn (local-archive/docs/retired-from-public/long-horizon-design.md
§1, §4.1, §4.3). The clear-then-build narrative is the deliberate cost.
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
            "goal": "build the tower first, then grasp the cube and clear the can and milk",
            "nodes": [
                {"id": "build-stack", "skill": "stack",
                 "args": {"object": "cubeA", "target": "cubeB"}, "after": []},
                {"id": "grasp-cube", "skill": "grasp",
                 "args": {"object": "cube"}, "after": ["build-stack"]},
                {"id": "pick-can", "skill": "pick",
                 "args": {"object": "can"}, "after": ["grasp-cube"]},
                {"id": "pick-milk", "skill": "pick",
                 "args": {"object": "milk"}, "after": ["pick-can"]},
            ],
            "verify": [
                {"after": "build-stack", "predicate": "stack_success"},
                {"after": "grasp-cube", "predicate": "lifted"},
                {"after": "pick-can", "predicate": "pick_success"},
                {"after": "pick-milk", "predicate": "pick_success"},
            ],
        }, sort_keys=True))

    @property
    def identity(self) -> str:
        return "clear_build_planner@v2"


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
    # four nodes, three distinct skills, the governed stack node ordered FIRST
    assert [n["id"] for n in plan["nodes"]] == \
        ["build-stack", "grasp-cube", "pick-can", "pick-milk"]
    assert {n["skill"] for n in plan["nodes"]} == {"grasp", "pick", "stack"}
    assert plan["nodes"][0]["after"] == []
    assert plan["nodes"][3]["after"] == ["pick-can"]
    assert [v["predicate"] for v in plan["verify"]] == \
        ["stack_success", "lifted", "pick_success", "pick_success"]
    try:
        planner.plan({"task": "stack"})
    except ValueError:
        pass
    else:
        raise AssertionError("wrong task must fail loudly")
    print("plugins/clear_build/planner.py self-check OK")
