"""M5: the ``clear_build`` long-horizon mission card -- planner graph, per-node
dispatch across three skill bindings, replan routing across nodes, the faked
4-node closed loop, and 体检 GREEN on the card.

No simulation in this file (the test_task_seam / test_rsi_workload fake手法): the
rollout is monkeypatched, so every assertion is about the SYMBOLIC chain (which
skills, what tasks, what routing) and the card's mount-time shape. The one real
headless episode is the manual smoke (scripts/chain_battery.py), reported
separately -- a live rollout is not a unit test.
"""

from __future__ import annotations

import json

import pytest

from harness import Kernel
from harness.definitions import CAPABILITIES
from harness.registry import load_provider
from plugins.clear_build.planner import CATALOGUE, ORACLES, ClearBuildPlanner
from plugins.graphs import InMemorySkillGraph
from plugins.task import workload
from plugins.task.validate import validate_plan

BRIEF = {"task": "clear_build", "scene": {}, "catalogue": CATALOGUE}


def _plan() -> dict:
    return ClearBuildPlanner().plan(BRIEF)


# ── the planner graph: shape + determinism + refusal ────────────────────────

def test_plan_validates_and_is_the_four_node_graph():
    plan = _plan()
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert ok and msg == ""
    assert plan["goal"]
    assert [n["id"] for n in plan["nodes"]] == \
        ["build-stack", "grasp-cube", "pick-can", "pick-milk"]
    # three DISTINCT skill bindings over the four nodes
    assert [n["skill"] for n in plan["nodes"]] == ["stack", "grasp", "pick", "pick"]
    assert {n["skill"] for n in plan["nodes"]} == {"grasp", "pick", "stack"}
    # the governed stack node runs first (design §4.3 lever); the rest chain after it
    assert plan["nodes"][0]["after"] == []
    assert plan["nodes"][2]["after"] == ["grasp-cube"]
    assert plan["nodes"][3]["after"] == ["pick-can"]
    # one verify edge per node, mixed predicates admitted by the card oracles
    assert [v["predicate"] for v in plan["verify"]] == \
        ["stack_success", "lifted", "pick_success", "pick_success"]


def test_same_brief_yields_byte_identical_json():
    assert json.dumps(ClearBuildPlanner().plan(BRIEF), sort_keys=True) == \
        json.dumps(ClearBuildPlanner().plan(BRIEF), sort_keys=True)


def test_wrong_task_fails_loudly():
    with pytest.raises(ValueError, match="clear_build"):
        ClearBuildPlanner().plan({"task": "stack"})


def test_planner_satisfies_the_contract_through_the_kernel():
    from harness.contracts import TaskPlanner

    p = load_provider("plugins.clear_build.planner:provider")
    assert isinstance(p, TaskPlanner) and p.identity == "clear_build_planner@v2"


# ── the composite policy: one mount, driver chosen by spec.task ──────────────

def test_composite_policy_routes_the_stack_node_only():
    from types import SimpleNamespace

    from plugins.policies import clear_build_provider
    from plugins.policies.drivers import ScriptedDriver, StackScriptedDriver

    fac = clear_build_provider()
    from harness.contracts import PolicyFactory
    assert isinstance(fac, PolicyFactory)
    from harness.spec import EpisodeSpec

    stack_spec = EpisodeSpec(seed=1, task="stack")
    lift_spec = EpisodeSpec(seed=1, task="lift")
    assert isinstance(fac.make_driver(stack_spec), StackScriptedDriver)
    assert isinstance(fac.make_driver(lift_spec), ScriptedDriver)


# ── per-node dispatch: each of the three bindings resolves its own spec ──────

def _rollout_result(ok: bool) -> dict:
    return {"success": ok, "steps": 100, "stages": [
        {"name": "grasp", "entered_step": 0, "exited_step": 50, "success": True,
         "reached": True, "privilege_used": 1},
        {"name": "place", "entered_step": 50, "exited_step": None, "success": ok,
         "reached": True, "privilege_used": 1}]}


class _RolloutFake:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.specs = []
        self.bundles = []

    def __call__(self, spec, bundle=None):
        self.specs.append(spec)
        self.bundles.append(bundle)
        return _rollout_result(self.outcomes.pop(0))


@pytest.mark.parametrize("node,expect_task,stages_ref", [
    ({"id": "grasp-cube", "skill": "grasp", "args": {"object": "cube"}, "after": []},
     "lift", "plugins.embodiment_robosuite.env:pick_stages"),
    ({"id": "pick-can", "skill": "pick", "args": {"object": "can"}, "after": []},
     "pickcan", "plugins.embodiment_robosuite.env:pick_stages"),
    ({"id": "pick-milk", "skill": "pick", "args": {"object": "milk"}, "after": []},
     "pickmilk", "plugins.embodiment_robosuite.env:pick_stages"),
    ({"id": "build-stack", "skill": "stack",
      "args": {"object": "cubeA", "target": "cubeB"}, "after": []},
     "stack", "plugins.embodiment_robosuite.env:stack_stages"),
])
def test_each_node_kind_dispatches_its_binding(monkeypatch, node, expect_task, stages_ref):
    fake = _RolloutFake([True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)
    result = workload._dispatch(node, seed=7, env_ref="tests.fakes:env",
                                policy_ref="tests.fakes:policy", skills=())
    assert result["success"] is True
    (spec,) = fake.specs
    assert spec.task == expect_task and spec.seed == 7 and spec.terminal_label
    assert spec.percept_noise == 0.012
    assert spec.stages == load_provider(stages_ref)
    assert spec.env_provider == "tests.fakes:env"
    assert spec.policy_provider == "tests.fakes:policy"


def test_pick_object_with_no_scene_binding_fails_loudly():
    node = {"id": "x", "skill": "pick", "args": {"object": "bottle"}, "after": []}
    with pytest.raises(ValueError, match="bottle"):
        workload._dispatch(node, seed=1, env_ref="bogus", policy_ref="bogus", skills=())


# ── the chain through the workload loop, rollout faked ───────────────────────

class _FakeScene:
    def snapshot(self, obs):
        return {"frame": "world", "t": 0.0, "nodes": [], "relations": []}


class _FakeEnv:
    def make_env(self, spec):
        raise AssertionError("no env: the rollout is monkeypatched")

    def tasks(self):
        return ("lift", "pickcan", "pickmilk", "stack")

    def object_key(self, spec):
        return "cubeA_pos"

    def success(self, obs, spec, start_z):
        return False


class _FakePolicy:
    def make_driver(self, spec):
        raise AssertionError("no driver: the rollout is monkeypatched")


class _FakeExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


class _CountingPlanner:
    def __init__(self):
        self.briefs = []

    def plan(self, brief):
        self.briefs.append(dict(brief))
        return ClearBuildPlanner().plan(brief)


def _task_kernel(planner) -> Kernel:
    k = Kernel(CAPABILITIES)
    k.provide("task.planner", planner, ref="tests.fakes:planner")
    k.provide("graph.scene", _FakeScene(), ref="tests.fakes:scene")
    k.provide("graph.skill", InMemorySkillGraph(),
              ref="plugins.graphs:skill_graph_provider")
    k.provide("embodiment.env", _FakeEnv(), ref="tests.fakes:env")
    k.provide("policy.driver", _FakePolicy(), ref="tests.fakes:policy")
    k.provide("exec.rollouts", _FakeExecutor(), ref="tests.fakes:executor")
    return k


WBRIEF = {"task": "clear_build", "catalogue": CATALOGUE, "oracles": ORACLES}


def test_four_node_chain_closes(monkeypatch):
    kernel = _task_kernel(ClearBuildPlanner())
    fake = _RolloutFake([True, True, True, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=42, max_actuations=6)

    assert out["success"] is True and out["replans"] == 0 and out["actuations"] == 4
    assert [s.task for s in fake.specs] == ["stack", "lift", "pickcan", "pickmilk"]
    assert list(out["nodes"]) == ["build-stack", "grasp-cube", "pick-can", "pick-milk"]
    assert all(n["success"] for n in out["nodes"].values())


def test_node_failure_replans_and_skips_done_nodes(monkeypatch):
    planner = _CountingPlanner()
    kernel = _task_kernel(planner)
    # stack, grasp, can succeed; the milk pick fails once, then succeeds on replan
    fake = _RolloutFake([True, True, True, False, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=1, max_replans=2, max_actuations=6)

    assert out["success"] is True and out["replans"] == 1 and out["actuations"] == 5
    # the three finished nodes are never re-dispatched; only the milk pick re-runs
    assert [s.task for s in fake.specs] == \
        ["stack", "lift", "pickcan", "pickmilk", "pickmilk"]
    fault = planner.briefs[1]["fault"]
    assert fault["kind"] == "node_failure" and fault["node"] == "pick-milk"
    assert fault["nodes_done"] == ["build-stack", "grasp-cube", "pick-can"]
    assert fault["nodes_left"] == ["pick-milk"]


def test_ungoverned_stack_node_carries_empty_governance(monkeypatch):
    """No skills mounted -> assemble_bundle returns None: the baseline arm's
    stack node runs ungoverned, byte-identical to a bare governed_rollout."""
    kernel = _task_kernel(ClearBuildPlanner())
    fake = _RolloutFake([True, True, True, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=3, max_actuations=6)

    gov = out["nodes"]["build-stack"]["governance"]
    assert gov == {"skills": [], "bundle_sha": None,
                   "critic_budget": 0, "action_budget": 0}
    assert fake.bundles == [None, None, None, None]


# ── 体检: plugin_doctor GREEN on the card ────────────────────────────────────

def test_plugin_doctor_green_on_the_card():
    import scripts.plugin_doctor as doctor

    rep = doctor.check("plugins/clear_build")
    assert rep.green, [(r.name, r.status, r.detail) for r in rep.results]
    # base machine (robosuite absent) SKIPs the needs_sim card; a sim machine
    # PASSes Tier A on the binding. Either way: no FAIL.
    assert all(r.status != "FAIL" for r in rep.results)
