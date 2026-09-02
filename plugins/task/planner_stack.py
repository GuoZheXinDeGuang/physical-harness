"""Deterministic stand-in planner for capability ``task.planner``.

SearchProposer's precedent (round 25): zero external API is the reference
path, not a fallback. ``StackPlanner`` is a hand-written decomposition table
keyed on ``brief["task"]``; a VLM planner is a later provider behind the same
``plan(brief) -> Mapping`` seam. "stack" is the round-83 ONE-node seam proof;
"clear_table" (round 86) is the first true multi-node graph -- two sequenced
pick nodes over the second skill provider, exercising plan -> validate ->
per-node dispatch -> verify -> replan-with-done-work-preserved.

The tiny Stack vocabulary is hand-declared here (CATALOGUE / ORACLES) because
``graph.skill.skills()`` publishes measurement records, not callable
interfaces with arg schemas; the enrichment join that adds measured success
rates to skill SELECTION waits until a multi-skill choice needs it (YAGNI).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from harness.skill_library import RECORDS, catalogue_of, select

#: skill name -> {arg name: required python type}. Authored on the skill side
#: (skill-library/records), never by the planner, which only selects and parameterizes.
CATALOGUE: dict[str, dict[str, type]] = catalogue_of(select(RECORDS, "robosuite", ("stack", "pick")))

#: Verify predicates a plan may name. Each is the embodiment's terminal
#: success boolean for that skill, reached at dispatch time through the
#: embodiment.env contract (plugins never import each other).
ORACLES: tuple[str, ...] = ("stack_success", "pick_success")


class StackPlanner:
    """Layer 1 ``harness.contracts.TaskPlanner``: deterministic table lookup."""

    def plan(self, brief: Mapping) -> Mapping:
        task = brief.get("task")
        # Round-trip through sorted JSON: the emitted mapping is exactly its
        # canonical byte form, so the same brief dumps byte-identically and
        # only pure JSON types ever leave this seam.
        if task == "stack":
            return json.loads(json.dumps({
                "goal": "stack cubeA on cubeB",
                "nodes": [{"id": "stack-0", "skill": "stack",
                           "args": {"object": "cubeA", "target": "cubeB"},
                           "after": []}],
                "verify": [{"after": "stack-0", "predicate": "stack_success"}],
            }, sort_keys=True))
        if task == "clear_table":
            # The first true multi-node graph: two pick skills in sequence,
            # each with its own verify edge, so a mid-graph failure replans
            # with finished work preserved (the workload skips done nodes).
            return json.loads(json.dumps({
                "goal": "clear the table: pick the can, then the milk",
                "nodes": [
                    {"id": "pick-can", "skill": "pick",
                     "args": {"object": "can"}, "after": []},
                    {"id": "pick-milk", "skill": "pick",
                     "args": {"object": "milk"}, "after": ["pick-can"]},
                ],
                "verify": [
                    {"after": "pick-can", "predicate": "pick_success"},
                    {"after": "pick-milk", "predicate": "pick_success"},
                ],
            }, sort_keys=True))
        raise ValueError(
            f"StackPlanner only plans 'stack' or 'clear_table', got {task!r}")

    @property
    def identity(self) -> str:
        return "stack_planner@v1"


def provider(**params: Any) -> StackPlanner:
    return StackPlanner(**params)


if __name__ == "__main__":
    from plugins.task.validate import validate_plan

    planner = StackPlanner()
    for task in ("stack", "clear_table"):
        b = {"task": task, "scene": {}, "catalogue": CATALOGUE}
        p = planner.plan(b)
        ok, msg = validate_plan(p, CATALOGUE, ORACLES)
        assert ok, (task, msg)
        assert json.dumps(p, sort_keys=True) == json.dumps(planner.plan(b), sort_keys=True)
    brief = {"task": "stack", "scene": {}, "catalogue": CATALOGUE}
    plan = planner.plan(brief)

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
