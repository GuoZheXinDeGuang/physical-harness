"""M6: the ``inventory_build`` heterogeneous mission card -- the 11-node graph
(four node KINDS), the machine predicates, the chain through the generic loop
with a survey perceive node on a FAKE env, and verify-/manipulate-node replan
routing. Plus 体检 GREEN on the card.

No simulation in this file (the test_clear_build fake手法): the manipulate rollout
is monkeypatched and the survey perceive node reads a fake embodiment env, so
every assertion is about the SYMBOLIC chain (which kinds, what routing, what
sealed facts) -- never a live sim. The one real headless episode is the manual
smoke (scripts/inventory_smoke.py), reported separately.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from harness import Kernel
from harness.definitions import CAPABILITIES
from harness.registry import load_provider
from plugins.graphs import InMemorySkillGraph
from plugins.inventory_build.planner import (
    CATALOGUE, ORACLES, PREDICATES, InventoryBuildPlanner,
    _classify, _plan_order, _report, _verify_cleared, _verify_grasp,
    _verify_integrity)
from plugins.task import workload
from plugins.task.validate import NODE_KINDS, validate_plan

BRIEF = {"task": "inventory_build", "scene": {}, "catalogue": CATALOGUE}


def _plan() -> dict:
    return InventoryBuildPlanner().plan(BRIEF)


# ── the planner graph: shape + kinds + determinism + refusal ─────────────────

def test_plan_validates_and_is_the_eleven_node_heterogeneous_graph():
    plan = _plan()
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert ok and msg == ""
    ids = [n["id"] for n in plan["nodes"]]
    assert ids == ["survey", "classify", "plan-order", "grasp-cube", "verify-grasp",
                   "build-stack", "verify-integrity", "pick-can", "pick-milk",
                   "verify-cleared", "report"]
    kinds = [n.get("kind", "manipulate") for n in plan["nodes"]]
    # 3 kinds BEYOND manipulate: perceive x2, decide x2, verify x3, manipulate x4
    assert kinds.count("perceive") == 2 and kinds.count("decide") == 2
    assert kinds.count("verify") == 3 and kinds.count("manipulate") == 4
    assert set(kinds) - {"manipulate"} <= NODE_KINDS
    # the manipulate nodes reuse the grasp/stack/pick SKILL_SPECS bindings
    manip = [n["skill"] for n in plan["nodes"] if n.get("kind", "manipulate") == "manipulate"]
    assert manip == ["grasp", "stack", "pick", "pick"]
    # verify edges name only manipulate terminals (kindful nodes self-gate)
    assert [v["predicate"] for v in plan["verify"]] == \
        ["lifted", "stack_success", "pick_success", "pick_success"]


def test_every_kindful_node_names_a_declared_predicate():
    for n in _plan()["nodes"]:
        assert n["skill"] in CATALOGUE
        if n.get("kind", "manipulate") != "manipulate":
            assert n["skill"] in PREDICATES


def test_same_brief_yields_byte_identical_json():
    assert json.dumps(InventoryBuildPlanner().plan(BRIEF), sort_keys=True) == \
        json.dumps(InventoryBuildPlanner().plan(BRIEF), sort_keys=True)


def test_wrong_task_fails_loudly():
    with pytest.raises(ValueError, match="inventory_build"):
        InventoryBuildPlanner().plan({"task": "stack"})


def test_planner_satisfies_the_contract_through_the_kernel():
    from harness.contracts import TaskPlanner

    p = load_provider("plugins.inventory_build.planner:provider")
    assert isinstance(p, TaskPlanner) and p.identity == "inventory_build_planner@v1"


def test_every_predicate_ref_resolves_to_a_callable():
    for ref in PREDICATES.values():
        assert callable(load_provider(ref))


# ── the machine predicates: pure decide/verify/classify on sealed facts ──────

class _Ctx:
    def __init__(self, nodes_out, seed=0, env_ref="x"):
        self.nodes_out = nodes_out
        self.seed = seed
        self.env_ref = env_ref


_SURVEY_FACTS = {"success": True, "facts": {"poses": {
    "lift": [0.0, 0.0, 0.83], "stack": [0.1, 0.0, 0.83],
    "pickcan": [0.2, 0.1, 0.85], "pickmilk": [-0.1, 0.2, 0.85]}}}


def _full_nodes_out():
    return {
        "survey": _SURVEY_FACTS,
        "classify": {"success": True},
        "plan-order": {"success": True, "decision": ["lift", "stack", "pickcan", "pickmilk"]},
        "grasp-cube": {"success": True, "stages": [{"name": "grasp", "success": True}]},
        "build-stack": {"success": True, "stages": [
            {"name": "grasp", "success": True}, {"name": "place", "success": True}]},
        "pick-can": {"success": True},
        "pick-milk": {"success": True},
    }


def test_predicates_pass_over_correct_sealed_facts():
    ctx = _Ctx(_full_nodes_out())
    assert _classify({}, ctx)["success"]
    dec = _plan_order({}, ctx)
    assert dec["success"] and set(dec["decision"]) == {"lift", "stack", "pickcan", "pickmilk"}
    assert _verify_grasp({}, ctx)["success"]
    assert _verify_integrity({}, ctx)["success"]
    assert _verify_cleared({}, ctx)["success"]
    rep = _report({}, ctx)
    assert rep["success"] and rep["decision"]["order"] is not None


def test_predicates_fail_closed_on_missing_facts():
    empty = _Ctx({})
    # a missing prior fact fails the gate (-> the loop's replan), never crashes
    assert _classify({}, empty)["success"] is False
    assert _plan_order({}, empty)["success"] is False
    assert _verify_grasp({}, empty)["success"] is False
    assert _verify_integrity({}, empty)["success"] is False
    assert _verify_cleared({}, empty)["success"] is False
    assert _report({}, empty)["success"] is False


def test_verify_integrity_fails_when_place_stage_did_not_pass():
    out = _full_nodes_out()
    out["build-stack"]["stages"][1]["success"] = False
    assert _verify_integrity({}, _Ctx(out))["success"] is False


# ── survey perceive node on a FAKE embodiment env (no sim) ────────────────────

class _FakeSurveyEnv:
    """One reset returns a tabletop obs with every task's target pose key; the
    real OnboardPercept reads obs[object_key] with no simulator."""

    def reset(self):
        return {"cube_pos": np.array([0.0, 0.0, 0.83]),
                "cubeA_pos": np.array([0.1, 0.0, 0.83]),
                "Can_pos": np.array([0.2, 0.1, 0.85]),
                "Milk_pos": np.array([-0.1, 0.2, 0.85])}

    def close(self):
        pass


class _FakeEmbodiment:
    def make_env(self, spec):
        return _FakeSurveyEnv()

    def tasks(self):
        return ("lift", "pickcan", "pickmilk", "stack")

    def object_key(self, spec):
        return "cubeA_pos"

    def success(self, obs, spec, start_z):
        return False


def fake_embodiment():  # a load_provider-resolvable ref for embodiment.env
    return _FakeEmbodiment()


def test_survey_reads_the_fake_scene_and_seals_privilege():
    ctx = workload.NodeCtx(seed=1, env_ref="tests.test_inventory_build:fake_embodiment",
                           policy_ref="x", skills=(), nodes_out={}, predicates=PREDICATES)
    out = workload._perceive({"id": "survey", "skill": "survey"}, ctx)
    assert out["success"] is True
    assert set(out["facts"]["poses"]) == {"lift", "stack", "pickcan", "pickmilk"}
    # the perceive node seals the privileged pose channel it declared reading
    assert out["governance"]["privilege_cost"] == 1
    assert out["governance"]["privilege_features"] == ("privileged.object_z",)


# ── the chain through the generic loop, rollout faked + survey on fake env ────

def _rollout_result(ok: bool) -> dict:
    return {"success": ok, "steps": 100, "stages": [
        {"name": "grasp", "entered_step": 0, "exited_step": 50, "success": ok,
         "reached": True, "privilege_used": 1},
        {"name": "place", "entered_step": 50, "exited_step": None, "success": ok,
         "reached": True, "privilege_used": 1}]}


class _RolloutFake:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.specs = []

    def __call__(self, spec, bundle=None):
        self.specs.append(spec)
        return _rollout_result(self.outcomes.pop(0))


class _FakeScene:
    def snapshot(self, obs):
        return {"frame": "world", "t": 0.0, "nodes": [], "relations": []}


class _FakePolicy:
    def make_driver(self, spec):
        raise AssertionError("no driver: the rollout is monkeypatched")


class _FakeExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


def _task_kernel(planner) -> Kernel:
    k = Kernel(CAPABILITIES)
    k.provide("task.planner", planner, ref="tests.fakes:planner")
    k.provide("graph.scene", _FakeScene(), ref="tests.fakes:scene")
    k.provide("graph.skill", InMemorySkillGraph(),
              ref="plugins.graphs:skill_graph_provider")
    # the survey node loads THIS ref and calls make_env(spec).reset()
    k.provide("embodiment.env", _FakeEmbodiment(),
              ref="tests.test_inventory_build:fake_embodiment")
    k.provide("policy.driver", _FakePolicy(), ref="tests.fakes:policy")
    k.provide("exec.rollouts", _FakeExecutor(), ref="tests.fakes:executor")
    return k


WBRIEF = {"task": "inventory_build", "catalogue": CATALOGUE, "oracles": ORACLES,
          "predicates": PREDICATES}


def test_eleven_node_heterogeneous_chain_closes(monkeypatch):
    kernel = _task_kernel(InventoryBuildPlanner())
    # four manipulate nodes, all succeed; perceive/decide/verify run for real
    fake = _RolloutFake([True, True, True, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=42, max_actuations=15)

    assert out["success"] is True and out["replans"] == 0 and out["actuations"] == 11
    assert len(out["nodes"]) == 11 and all(n["success"] for n in out["nodes"].values())
    # only the four manipulate nodes hit the rollout, in graph order
    assert [s.task for s in fake.specs] == ["lift", "stack", "pickcan", "pickmilk"]
    # the perceive/decide nodes sealed their payloads for the downstream readers
    assert set(out["nodes"]["survey"]["facts"]["poses"]) == \
        {"lift", "stack", "pickcan", "pickmilk"}
    assert out["nodes"]["plan-order"]["decision"] is not None
    assert out["nodes"]["report"]["decision"]["cleared"] is True


# a flaky verify predicate: fails on its first dispatch, passes after the replan.
# Module-level state because the loop load_provider-resolves the ref fresh each
# dispatch -- this is how the test drives a VERIFY-node failure -> replan.
_FLAKY_CALLS: list[int] = []


def flaky_verify():
    def run(node, ctx):
        _FLAKY_CALLS.append(1)
        return {"success": len(_FLAKY_CALLS) >= 2}
    return run


def test_verify_node_failure_replans_and_skips_done_nodes(monkeypatch):
    _FLAKY_CALLS.clear()
    kernel = _task_kernel(InventoryBuildPlanner())
    fake = _RolloutFake([True, True, True, True])  # every manipulate node passes
    monkeypatch.setattr(workload, "_governed_rollout", fake)
    # override ONE verify predicate with the flaky one; the loop reroutes on its
    # first False exactly as it would on any node_failure -- no new routing code.
    brief = {**WBRIEF, "predicates": {**PREDICATES,
             "verify_integrity": "tests.test_inventory_build:flaky_verify"}}

    out = workload.run(brief, kernel, seed=7, max_replans=2, max_actuations=20)

    assert out["success"] is True and out["replans"] == 1
    # the verify node was the fault the loop rerouted on
    fault = out["faults"][0]
    assert fault["kind"] == "node_failure" and fault["node"] == "verify-integrity"
    # the nodes finished before the failing verify are never re-dispatched: only
    # grasp+stack ran on the rollout before verify-integrity; the two picks run
    # AFTER the replan, so the rollout sees exactly four specs, never more.
    assert [s.task for s in fake.specs] == ["lift", "stack", "pickcan", "pickmilk"]
    assert "verify-integrity" in fault["nodes_left"]
    assert {"survey", "classify", "plan-order", "grasp-cube", "verify-grasp",
            "build-stack"} <= set(fault["nodes_done"])


def test_manipulate_node_failure_replans(monkeypatch):
    kernel = _task_kernel(InventoryBuildPlanner())
    # grasp, stack, can pass; milk fails once then succeeds on replan
    fake = _RolloutFake([True, True, True, False, True])
    monkeypatch.setattr(workload, "_governed_rollout", fake)

    out = workload.run(dict(WBRIEF), kernel, seed=1, max_replans=2, max_actuations=20)

    assert out["success"] is True and out["replans"] == 1
    fault = out["faults"][0]
    assert fault["kind"] == "node_failure" and fault["node"] == "pick-milk"
    # only the milk pick re-dispatches; every finished node is skipped
    assert [s.task for s in fake.specs] == \
        ["lift", "stack", "pickcan", "pickmilk", "pickmilk"]


# ── 体检: plugin_doctor GREEN on the card ────────────────────────────────────

def test_plugin_doctor_green_on_the_card():
    import scripts.plugin_doctor as doctor

    rep = doctor.check("plugins/inventory_build")
    assert rep.green, [(r.name, r.status, r.detail) for r in rep.results]
    assert all(r.status != "FAIL" for r in rep.results)
