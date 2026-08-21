"""Round 83 rung 1: the task.planner seam — contract, validation, determinism.

A planner is untrusted regardless of author (governor/proposer.py's stance),
so validate_plan's rejection surface is the load-bearing thing: one test per
refusal class, each asserting the message names the offender so it can be
folded straight back into the next brief. The AST boundary tests cover the
new plugins/task package automatically (harness+stdlib imports only).
"""

from __future__ import annotations

import json

import pytest

from harness import Kernel
from harness.contracts import TaskPlanner
from harness.definitions import CAPABILITIES
from harness.registry import load_provider
from plugins.task.planner_stack import CATALOGUE, ORACLES, StackPlanner
from plugins.task.validate import validate_plan

PLANNER_REF = "plugins.task.planner_stack:provider"
BRIEF = {"task": "stack", "scene": {}, "catalogue": CATALOGUE}


def _plan() -> dict:
    return StackPlanner().plan(BRIEF)


def test_stack_plan_passes_validation():
    ok, msg = validate_plan(_plan(), CATALOGUE, ORACLES)
    assert ok and msg == ""


def test_unknown_skill_is_refused_by_name():
    plan = _plan()
    plan["nodes"][0]["skill"] = "teleport"
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert not ok and "teleport" in msg and "catalogue" in msg


def test_bad_args_are_refused_by_name():
    unknown = _plan()
    unknown["nodes"][0]["args"]["speed"] = 2.0
    ok, msg = validate_plan(unknown, CATALOGUE, ORACLES)
    assert not ok and "speed" in msg

    mistyped = _plan()
    mistyped["nodes"][0]["args"]["object"] = 7
    ok, msg = validate_plan(mistyped, CATALOGUE, ORACLES)
    assert not ok and "'object'" in msg and "str" in msg


def test_hallucinated_predicate_is_refused():
    plan = _plan()
    plan["verify"][0]["predicate"] = "hope_it_worked"
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert not ok and "hope_it_worked" in msg and "oracles" in msg


def test_empty_graph_is_refused():
    for hollow in ({**_plan(), "nodes": []}, {**_plan(), "verify": []}):
        ok, _msg = validate_plan(hollow, CATALOGUE, ORACLES)
        assert not ok, hollow


def test_same_brief_yields_byte_identical_json():
    a = json.dumps(StackPlanner().plan(BRIEF), sort_keys=True)
    b = json.dumps(StackPlanner().plan(BRIEF), sort_keys=True)
    assert a == b


def test_planner_satisfies_the_contract_through_the_kernel():
    p = load_provider(PLANNER_REF)
    assert isinstance(p, TaskPlanner)
    assert p.identity == "stack_planner@v1"
    # Definition.__post_init__ already vetted task.planner's Protocol at import;
    # provide() runs the structural check, resolve() records the accounting.
    k = Kernel(CAPABILITIES)
    k.provide("task.planner", p, ref=PLANNER_REF)
    assert k.resolve("task.planner", consumer="task") is p
    assert [r.consumer for r in k.resolutions()] == ["task"]


def test_non_stack_task_fails_loudly():
    with pytest.raises(ValueError):
        StackPlanner().plan({"task": "lift"})
