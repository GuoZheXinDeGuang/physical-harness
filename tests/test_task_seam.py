"""Round 83: the task.planner seam — contract, validation, and the workload loop.

Rung 1: a planner is untrusted regardless of author (governor/proposer.py's
stance), so validate_plan's rejection surface is the load-bearing thing: one
test per refusal class, each asserting the message names the offender so it
can be folded straight back into the next brief. The AST boundary tests cover
the new plugins/task package automatically (harness+stdlib imports only).

Rung 2: the workload's replan loop, tested with a monkeypatched rollout (no
simulation anywhere in this file, the test_rsi_workload fake手法): one-pass,
invalid-plan-refolded-then-repaired, stage-failure-to-exhaustion, the
max_actuations floor, and the ledger note.
"""

from __future__ import annotations

import json

import pytest

from harness import Kernel
from harness.contracts import TaskPlanner
from harness.definitions import CAPABILITIES
from harness.events import SessionLog
from harness.registry import load_provider
from plugins.graphs import InMemorySkillGraph
from plugins.task import workload
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


# =====================================================================
# rung 2: plugins.task.workload.run — the replan loop, rollout faked
# =====================================================================

WBRIEF = {"task": "stack", "catalogue": CATALOGUE, "oracles": ORACLES}


class _FakeEnv:
    def make_env(self, spec):
        raise AssertionError("no env in these tests: the rollout is monkeypatched")

    def tasks(self):
        return ("stack",)

    def object_key(self, spec):
        return "cubeA_pos"

    def success(self, obs, spec, start_z):
        return False


class _FakePolicy:
    def make_driver(self, spec):
        raise AssertionError("no driver in these tests")


class _FakeExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


class _FakeScene:
    def snapshot(self, obs):
        return {"frame": "world", "t": 0.0, "nodes": [], "relations": []}


class _CountingPlanner:
    """The real StackPlanner's plan, with every brief it saw captured."""

    def __init__(self):
        self.briefs: list[dict] = []

    def plan(self, brief):
        self.briefs.append(dict(brief))
        return StackPlanner().plan(brief)


class _FlakyPlanner(_CountingPlanner):
    """Hallucinates a skill first; repairs itself once the fault comes back."""

    def plan(self, brief):
        self.briefs.append(dict(brief))
        good = StackPlanner().plan(brief)
        if brief.get("fault") is None:
            bad = json.loads(json.dumps(good))
            bad["nodes"][0]["skill"] = "teleport"
            return bad
        return good


def _rollout_result(ok: bool) -> dict:
    """governed_rollout's stages-on shape (governed.py result assembly)."""
    return {"success": ok, "steps": 177, "stages": [
        {"name": "grasp", "entered_step": 0, "exited_step": 66, "success": True,
         "reached": True, "privilege_used": 1},
        {"name": "place", "entered_step": 66, "exited_step": None, "success": ok,
         "reached": True, "privilege_used": 1},
    ]}


class _RolloutFake:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.specs = []

    def __call__(self, spec):
        self.specs.append(spec)
        return _rollout_result(self.outcomes.pop(0))


def _task_kernel(planner, log=None) -> Kernel:
    k = Kernel(CAPABILITIES, log=log)
    k.provide("task.planner", planner, ref="tests.fakes:planner")
    k.provide("graph.scene", _FakeScene(), ref="tests.fakes:scene")
    k.provide("graph.skill", InMemorySkillGraph(), ref="plugins.graphs:skill_graph_provider")
    k.provide("embodiment.env", _FakeEnv(), ref="tests.fakes:env")
    k.provide("policy.driver", _FakePolicy(), ref="tests.fakes:policy")
    k.provide("exec.rollouts", _FakeExecutor(), ref="tests.fakes:executor")
    return k


def test_workload_one_pass(monkeypatch):
    kernel = _task_kernel(StackPlanner())
    fake = _RolloutFake([True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=123)

    assert out["success"] is True
    assert out["replans"] == 0 and out["actuations"] == 1
    assert [s["name"] for s in out["nodes"]["stack-0"]["stages"]] == ["grasp", "place"]
    # the dispatched spec carries the kernel-resolved refs and the REAL
    # stack_stages() chain, loaded by ref string (plugins never import siblings)
    from plugins.embodiment_robosuite.env import stack_stages

    spec = fake.specs[0]
    assert spec.task == "stack" and spec.seed == 123 and spec.terminal_label
    assert spec.env_provider == "tests.fakes:env"
    assert spec.policy_provider == "tests.fakes:policy"
    assert spec.stages == stack_stages()
    resolved = {r.capability: r.consumer for r in kernel.resolutions()}
    assert resolved == {"task.planner": "task", "graph.scene": "task",
                        "graph.skill": "task", "embodiment.env": "task",
                        "policy.driver": "task", "exec.rollouts": "task"}
    assert not any(r.privileged for r in kernel.resolutions())


def test_invalid_plan_is_folded_back_and_repaired(monkeypatch):
    planner = _FlakyPlanner()
    kernel = _task_kernel(planner)
    fake = _RolloutFake([True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=1)

    assert out["success"] is True and out["replans"] == 1
    assert len(planner.briefs) == 2
    fault = planner.briefs[1]["fault"]
    assert fault["kind"] == "invalid_plan"
    assert "teleport" in fault["msg"], "the validator's own words must reach the planner"
    assert out["faults"][0]["kind"] == "invalid_plan"
    assert len(fake.specs) == 1, "an invalid plan must never actuate"


def test_stage_failure_replans_until_max_replans_exhausted(monkeypatch):
    planner = _CountingPlanner()
    kernel = _task_kernel(planner)
    fake = _RolloutFake([False, False, False])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=1, max_replans=2, max_actuations=10)

    assert out["success"] is False
    assert len(planner.briefs) == 3, "initial plan + exactly max_replans replans"
    assert out["replans"] == 2 and out["actuations"] == 3
    # the Fault-shaped brief preserves failed/done/left, attributably
    fault = planner.briefs[1]["fault"]
    assert fault["kind"] == "node_failure" and fault["node"] == "stack-0"
    assert fault["done"] == ["grasp"] and fault["left"] == ["place"]
    assert "stack_success" in fault["failed"], "the failed verify predicate is named too"


def test_max_actuations_is_a_model_independent_floor(monkeypatch):
    planner = _CountingPlanner()
    kernel = _task_kernel(planner)
    fake = _RolloutFake([False, False, False, False])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=1, max_replans=5, max_actuations=1)

    assert out["success"] is False
    assert len(fake.specs) == 1, "the floor is enforced BEFORE dispatch"
    assert out["actuations"] == 1
    assert out["faults"][-1]["kind"] == "budget"
    assert len(planner.briefs) == 2, "budget exhaustion stops the loop, replans unspent"


def test_plan_complete_enters_the_session_chain(monkeypatch):
    log = SessionLog()
    kernel = _task_kernel(StackPlanner(), log=log)
    monkeypatch.setattr(workload, "_governed_rollout", _RolloutFake([True]))

    workload.run(dict(WBRIEF), kernel, seed=7)

    rows = [r for r in log.rows() if r["kind"] == "task.plan_complete"]
    assert len(rows) == 1
    data = rows[0]["data"]
    assert data["success"] is True and data["actuations"] == 1 and data["replans"] == 0
    assert data["nodes"]["stack-0"]["stages"] == [
        {"name": "grasp", "success": True}, {"name": "place", "success": True}]
    kinds = [r["kind"] for r in log.rows()]
    assert kinds.index("capability.resolve") < kinds.index("task.plan_complete"), \
        "the note must sit downstream of the resolutions in the same chain"
    assert log.verify(), "the combined ledger no longer verifies"


def test_mounted_params_on_actuating_capabilities_are_refused():
    kernel = Kernel(CAPABILITIES)
    kernel.provide("task.planner", StackPlanner(), ref="tests.fakes:planner")
    kernel.provide("graph.scene", _FakeScene(), ref="tests.fakes:scene")
    kernel.provide("graph.skill", InMemorySkillGraph(), ref="plugins.graphs:skill_graph_provider")
    kernel.provide("embodiment.env", _FakeEnv(), ref="tests.fakes:env",
                   params={"variant": "viewer"})
    kernel.provide("policy.driver", _FakePolicy(), ref="tests.fakes:policy")
    kernel.provide("exec.rollouts", _FakeExecutor(), ref="tests.fakes:executor")
    with pytest.raises(ValueError, match="parameter-free"):
        workload.run(dict(WBRIEF), kernel, seed=1)
