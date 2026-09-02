"""Supported/Covered are real: the task loop hands validate_graph the SkillRecordV0
map, sigma0 facts and objects; a graph whose requires nothing provides is refused
before dispatch; every deterministic card's plan is legal against its own records."""

from __future__ import annotations

import json

import pytest

from harness import protocol
from harness.events import SessionLog
from harness.manifest import discover
from plugins.task import workload
from plugins.task.planner_stack import StackPlanner
from scripts.harness_runtime import task_brief
from test_task_seam import _CountingPlanner, _RolloutFake, _task_kernel


def _brief(task: str) -> dict:
    return task_brief(task, discover().task_bindings[task])


class _Spy:
    _real = staticmethod(protocol.validate_graph)

    def __init__(self):
        self.calls = []

    def __call__(self, graph, records, facts, objects):
        self.calls.append((records, list(facts), list(objects)))
        return self._real(graph, records, facts, objects)


def test_fakes_stack_gate_receives_records_facts_and_objects(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(workload.protocol, "validate_graph", spy)
    monkeypatch.setattr(workload, "_governed_rollout", _RolloutFake([True]))
    log = SessionLog()
    out = workload.run(_brief("stack"), _task_kernel(_CountingPlanner(), log=log), seed=1)

    assert out["success"] is True
    records, facts, objects = spy.calls[0]
    assert records["stack"].requires == ("present(object)", "present(target)")
    assert "present(cubeA)" in facts and {"cubeA", "cubeB"} <= set(objects)
    plan = next(r["data"] for r in log.rows() if r["kind"] == "task.plan")
    assert plan["legal"] is True and plan["facts"] == facts and plan["objects"] == objects


class _UnsupportedPlanner(_CountingPlanner):
    """pick(bottle): nothing in sigma0 presents a bottle."""

    def plan(self, brief):
        self.briefs.append(dict(brief))
        plan = json.loads(json.dumps(StackPlanner().plan({**brief, "task": "clear_table"})))
        plan["nodes"][0]["args"]["object"] = "bottle"
        return plan


def test_graph_missing_a_supporter_is_refused_before_dispatch(monkeypatch):
    fake = _RolloutFake([True, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)
    log = SessionLog()
    out = workload.run(_brief("clear_table"), _task_kernel(_UnsupportedPlanner(), log=log),
                       seed=1, max_replans=1)

    assert out["success"] is False and fake.specs == []
    plans = [r["data"] for r in log.rows() if r["kind"] == "task.plan"]
    assert plans[0]["legal"] is False
    assert "supported: node 'pick-can' requires present(bottle)" in plans[0]["problems"][0]
    assert [r["kind"] for r in log.rows() if r["kind"] == "task.replan_rejected"]


@pytest.mark.parametrize("task", ["kitchen_thaw", "pack_lunch", "recycle_cans", "steam_prep"])
def test_deterministic_card_plans_are_legal_against_their_records(task):
    brief = _brief(task)
    planner = discover().task_bindings[task]["planner"]
    from harness.registry import load_provider
    plan = load_provider(planner, {}).plan({**brief, "scene": {}, "seed": 0})
    facts, objects = workload._sigma0(brief, {}, {}, workload._records(brief, brief["catalogue"]))
    assert facts and objects
    assert workload._graph_problems(plan, None, (), {**brief, "facts": facts, "objects": objects},
                                    brief["catalogue"], seed=0) == []
    # drop the declared facts: the first segment loses its supporter
    problems = workload._graph_problems(plan, None, (), {**brief, "facts": [], "objects": objects},
                                        brief["catalogue"], seed=0)
    assert any(p.startswith("supported:") for p in problems)
