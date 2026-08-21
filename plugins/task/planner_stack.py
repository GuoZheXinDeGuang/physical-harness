"""Deterministic stand-in planner for capability ``task.planner``.

SearchProposer's precedent (round 25): zero external API is the reference
path, not a fallback. ``StackPlanner`` is a hand-written decomposition table
keyed on ``brief["task"]``; a VLM planner is a later provider behind the same
``plan(brief) -> Mapping`` seam. The first closed loop emits a ONE-node graph
whose single node runs the existing Stack policy, so the whole seam (plan ->
validate -> governed rollout -> per-stage scoring -> replan -> ledger) is
proven with zero new numeric surface; multi-node decomposition waits for a
second skill provider to exist.

The tiny Stack vocabulary is hand-declared here (CATALOGUE / ORACLES) because
``graph.skill.skills()`` publishes measurement records, not callable
interfaces with arg schemas; the enrichment join that adds measured success
rates to skill SELECTION waits until a multi-skill choice needs it (YAGNI).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: skill name -> {arg name: required python type}. Authored on the skill side,
#: never by the planner: the planner only selects and parameterizes.
CATALOGUE: dict[str, dict[str, type]] = {
    "stack": {"object": str, "target": str},
}

#: Verify predicates a plan may name. "stack_success" is the embodiment's
#: terminal success boolean, reached at dispatch time through the
#: embodiment.env contract (plugins never import each other).
ORACLES: tuple[str, ...] = ("stack_success",)


class StackPlanner:
    """Layer 1 ``harness.contracts.TaskPlanner``: deterministic table lookup."""

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        if task != "stack":
            raise ValueError(f"StackPlanner only plans 'stack', got {task!r}")
        # Round-trip through sorted JSON: the emitted mapping is exactly its
        # canonical byte form, so the same brief dumps byte-identically and
        # only pure JSON types ever leave this seam.
        return json.loads(json.dumps({
            "goal": "stack cubeA on cubeB",
            "nodes": [{"id": "stack-0", "skill": "stack",
                       "args": {"object": "cubeA", "target": "cubeB"},
                       "after": []}],
            "verify": [{"after": "stack-0", "predicate": "stack_success"}],
        }, sort_keys=True))

    @property
    def identity(self) -> str:
        return "stack_planner@v1"


def provider(**params: Any) -> StackPlanner:
    return StackPlanner(**params)


if __name__ == "__main__":
    from plugins.task.validate import validate_plan

    planner = StackPlanner()
    brief = {"task": "stack", "scene": {}, "catalogue": CATALOGUE}
    plan = planner.plan(brief)
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert ok, msg
    assert json.dumps(plan, sort_keys=True) == json.dumps(planner.plan(brief), sort_keys=True)

    def refused(bad: dict, offender: str) -> None:
        ok, msg = validate_plan(bad, CATALOGUE, ORACLES)
        assert not ok and offender in msg, (offender, msg)

    node = dict(plan["nodes"][0])
    refused({**plan, "nodes": [{**node, "skill": "teleport"}]}, "teleport")
    refused({**plan, "nodes": [{**node, "args": {"object": "cubeA", "speed": 2}}]}, "speed")
    refused({**plan, "nodes": [{**node, "args": {"object": 7, "target": "cubeB"}}]}, "object")
    refused({**plan, "verify": [{"after": "stack-0", "predicate": "hope"}]}, "hope")
    refused({**plan, "verify": []}, "verify")
    refused({**plan, "nodes": []}, "nodes")
    print("plugins/task/planner_stack.py self-check OK")
