"""The task loop seals protocol-v0 events (task.plan / task.verify / task.fault /
task.replan_rejected) and refuses a non-monotone replan before dispatch."""

from __future__ import annotations

import json

from harness.events import SessionLog
from plugins.task import workload
from plugins.task.planner_stack import CATALOGUE, StackPlanner
from test_task_seam import CT_BRIEF, _CountingPlanner, _RolloutFake, _task_kernel


class _DroppingPlanner(_CountingPlanner):
    """On replan, renames the finished node: a monotonicity violation."""

    def plan(self, brief):
        self.briefs.append(dict(brief))
        plan = json.loads(json.dumps(StackPlanner().plan(brief)))
        if brief.get("fault") is not None:
            plan["nodes"][0]["id"] = "pick-can-again"
            plan["verify"][0]["after"] = "pick-can-again"
        return plan


def test_verify_fault_and_replan_events_enter_the_chain(monkeypatch):
    log = SessionLog()
    kernel = _task_kernel(_CountingPlanner(), log=log)
    monkeypatch.setattr(workload, "_governed_rollout", _RolloutFake([True, False, True]))

    out = workload.run(dict(CT_BRIEF), kernel, seed=1, max_actuations=4)

    assert out["success"] is True and out["replans"] == 1
    kinds = [r["kind"] for r in log.rows() if r["kind"].startswith("task.")]
    assert kinds == ["task.plan", "task.verify", "task.verify", "task.fault",
                     "task.plan", "task.verify", "task.plan_complete"]
    verify = [r["data"] for r in log.rows() if r["kind"] == "task.verify"]
    assert verify[1] == {"node": "pick-milk", "results": {"pick_success": False}}
    fault = next(r["data"] for r in log.rows() if r["kind"] == "task.fault")
    assert fault["node"] == "pick-milk" and fault["failed"] == ["place", "pick_success"]
    plans = [r["data"] for r in log.rows() if r["kind"] == "task.plan"]
    assert plans[1]["done"] == ["pick-can"] and plans[1]["legal"] is True
    assert log.verify()


def test_non_monotone_replan_is_sealed_rejected_and_never_dispatched(monkeypatch):
    log = SessionLog()
    planner = _DroppingPlanner()
    kernel = _task_kernel(planner, log=log)
    fake = _RolloutFake([True, False, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(CT_BRIEF), kernel, seed=1, max_replans=1, max_actuations=4)

    assert out["success"] is False and out["replans"] == 1
    assert len(fake.specs) == 2, "the rejected graph was never dispatched"
    rejected = [r["data"] for r in log.rows() if r["kind"] == "task.replan_rejected"]
    assert len(rejected) == 1 and rejected[0]["replan"] == 1
    assert "pick-can" in rejected[0]["problems"][0]
    plans = [r["data"] for r in log.rows() if r["kind"] == "task.plan"]
    assert [p["legal"] for p in plans] == [True, False]


def test_graph_problems_catch_rewritten_done_node():
    old = {"goal": "g", "nodes": [{"id": "a", "skill": "pick", "args": {"object": "can"},
                                   "after": []}]}
    new = {"goal": "g", "nodes": [{"id": "a", "skill": "pick", "args": {"object": "milk"},
                                   "after": []}]}
    problems = workload._graph_problems(new, old, {"a"}, {}, CATALOGUE, seed=0)
    assert any("rewrote done node 'a'" in p for p in problems)
    assert workload._graph_problems(old, old, {"a"}, {}, CATALOGUE, seed=0) == []
