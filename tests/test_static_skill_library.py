"""Shared static skill contracts -> VLM graph -> embodiment segment binding."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness.skill_library import LIBRARY
from harness.spec import EpisodeSpec
from plugins import mission_basket_smoke as B
from plugins import mission_pack_all as M
from plugins import mission_stack_blocks as S
from plugins.planner_vlm import VlmPlanner
from plugins.task.validate import validate_plan
from plugins.task.workload import NodeCtx, _segment_spec
from scripts.harness_runtime import task_brief


def _graph() -> dict:
    nodes = []
    verify = []
    previous = None
    for obj in M.ITEMS:
        target = M.TARGET_BY_OBJECT[obj]
        calls = (
            ("navigate_to_object", {"object": obj}),
            ("pick", {"object": obj}),
            ("transport", {"object": obj, "target": target}),
            ("place_in", {"object": obj, "target": target}),
        )
        for index, (skill, args) in enumerate(calls):
            nid = f"{skill}-{obj}"
            nodes.append({"id": nid, "skill": skill, "kind": "segment",
                          "args": args, "after": [previous] if previous else []})
            verify.append({"after": nid, "predicate": "segment_success"})
            previous = nid
    return {"goal": M.DEFAULT_INSTRUCTION, "nodes": nodes, "verify": verify}


class _Endpoint:
    identity = "fake"

    def __init__(self, graph):
        self.graph = graph
        self.messages = None

    def chat(self, messages, **opts):
        self.messages = messages
        return json.dumps(self.graph)


def test_contracts_are_shared_but_only_admitted_bindings_are_planner_visible():
    robo = LIBRARY.bindings("robocasa")
    libero = LIBRARY.bindings("libero", implemented_only=False)
    assert {"pick", "place_in"} <= set(robo) & set(libero)
    assert not libero["pick"].implemented and not libero["place_in"].implemented
    assert LIBRARY.bindings("libero") == {}
    assert set(LIBRARY.bindings("robosuite")) == {"pick", "place_on"}


def test_vlm_receives_instruction_contracts_and_grounded_scene():
    endpoint = _Endpoint(_graph())
    planner = VlmPlanner()
    planner._ep = endpoint
    brief = {
        "task": "pack_all_robocasa",
        "instruction": "put every food item into the right box",
        "catalogue": M.CATALOGUE,
        "oracles": M.ORACLES,
        "skill_docs": M.SKILL_DOCS,
        "planning_context": M.PLANNING_CONTEXT,
        "seed": 923,
        "budget": 24,
    }
    plan = planner.plan(brief)
    ok, msg = validate_plan(
        plan, M.CATALOGUE, M.ORACLES, requirements=M.PLANNING_CONTEXT)
    assert ok, msg
    content = endpoint.messages[1]["content"]
    payload = json.loads(content[content.find("{"):content.rfind("}") + 1])
    assert payload["goal"] == brief["instruction"]
    assert payload["skill_docs"]["place_in"]["kind"] == "segment"
    assert payload["planning_context"]["target_by_object"] == M.TARGET_BY_OBJECT


def _ctx():
    spec = EpisodeSpec(seed=0, task="pack_all_robocasa")
    ep = SimpleNamespace(spec=spec)
    ctx = NodeCtx(seed=0, env_ref="e", policy_ref="p", skills=(),
                  nodes_out={}, predicates={}, episode=None,
                  segment_specs=M.SEGMENT_SPECS)
    return ep, ctx


def test_abstract_skill_resolves_to_robocasa_private_stage_name():
    ep, ctx = _ctx()
    node = {"id": "place-hot0", "skill": "place_in", "kind": "segment",
            "args": {"object": "hot0", "target": "tupperware0"}, "after": []}
    assert _segment_spec(node, ep, ctx).task == "pack_hot0"


def test_wrong_vlm_grounding_is_refused_before_actuation():
    ep, ctx = _ctx()
    node = {"id": "place-hot0", "skill": "place_in", "kind": "segment",
            "args": {"object": "hot0", "target": "tupperware1"}, "after": []}
    with pytest.raises(ValueError, match="expected 'tupperware0'"):
        _segment_spec(node, ep, ctx)


def test_manifest_binding_threads_library_context_into_workload_brief():
    from harness.manifest import discover

    binding = discover().task_bindings["pack_all_robocasa"]
    brief = task_brief("pack_all_robocasa", binding)
    assert brief["catalogue"] == M.CATALOGUE
    assert brief["skill_docs"] == M.SKILL_DOCS
    assert brief["planning_context"] == M.PLANNING_CONTEXT
    assert brief["default_instruction"] == M.DEFAULT_INSTRUCTION


def test_basket_smoke_exposes_only_pick_and_place_without_transport():
    assert set(B.CATALOGUE) == {"pick", "place_in"}
    assert B.PLANNING_CONTEXT["objects"] == ["item0", "item1", "item2"]
    assert B.PLANNING_CONTEXT["receptacles"] == ["basket"]
    assert "transport" not in B.CATALOGUE


def _basket_graph() -> dict:
    nodes = []
    verify = []
    previous = None
    for item in B.ITEMS:
        pick = f"pick-{item}"
        place = f"place-{item}"
        nodes.extend([
            {"id": pick, "skill": "pick", "kind": "segment",
             "args": {"object": item}, "after": [previous] if previous else []},
            {"id": place, "skill": "place_in", "kind": "segment",
             "args": {"object": item, "target": B.TARGET}, "after": [pick]},
        ])
        verify.extend([
            {"after": pick, "predicate": "segment_success"},
            {"after": place, "predicate": "segment_success"},
        ])
        previous = place
    return {"goal": B.DEFAULT_INSTRUCTION, "nodes": nodes, "verify": verify}


def test_basket_requirement_admits_exactly_three_ordered_pick_place_pairs():
    graph = _basket_graph()
    assert validate_plan(
        graph, B.CATALOGUE, B.ORACLES, requirements=B.PLANNING_CONTEXT) == (True, "")

    graph["nodes"] = graph["nodes"][:-2]
    graph["verify"] = graph["verify"][:-2]
    ok, msg = validate_plan(
        graph, B.CATALOGUE, B.ORACLES, requirements=B.PLANNING_CONTEXT)
    assert not ok and "item2" in msg and "exactly one" in msg


def test_basket_requirement_rejects_wrong_target_and_unordered_place():
    graph = _basket_graph()
    graph["nodes"][1]["args"]["target"] = "counter"
    ok, msg = validate_plan(
        graph, B.CATALOGUE, B.ORACLES, requirements=B.PLANNING_CONTEXT)
    assert not ok and "expected 'basket'" in msg

    graph = _basket_graph()
    graph["nodes"][1]["after"] = []
    ok, msg = validate_plan(
        graph, B.CATALOGUE, B.ORACLES, requirements=B.PLANNING_CONTEXT)
    assert not ok and "requires 'place_in' after 'pick'" in msg


def test_basket_abstract_calls_resolve_to_same_counter_driver_stages():
    spec = EpisodeSpec(seed=0, task="basket_smoke_vlm")
    ep = SimpleNamespace(spec=spec)
    ctx = NodeCtx(seed=0, env_ref="e", policy_ref="p", skills=(),
                  nodes_out={}, predicates={}, episode=None,
                  segment_specs=B.SEGMENT_SPECS)
    pick = {"id": "pick-item0", "skill": "pick", "kind": "segment",
            "args": {"object": "item0"}, "after": []}
    place = {"id": "place-item0", "skill": "place_in", "kind": "segment",
             "args": {"object": "item0", "target": "basket"},
             "after": ["pick-item0"]}
    assert _segment_spec(pick, ep, ctx).task == "grasp_item0"
    assert _segment_spec(place, ep, ctx).task == "pack_item0"


def _stack_graph() -> dict:
    return {
        "goal": S.DEFAULT_INSTRUCTION,
        "nodes": [
            {"id": "pick-cubeA", "skill": "pick", "kind": "segment",
             "args": {"object": "cubeA"}, "after": []},
            {"id": "place-cubeA-on-cubeB", "skill": "place_on",
             "kind": "segment", "args": {"object": "cubeA", "target": "cubeB"},
             "after": ["pick-cubeA"]},
        ],
        "verify": [
            {"after": "pick-cubeA", "predicate": "segment_success"},
            {"after": "place-cubeA-on-cubeB", "predicate": "segment_success"},
        ],
    }


def test_stack_blocks_is_exact_two_skill_vlm_graph():
    assert set(S.CATALOGUE) == {"pick", "place_on"}
    assert validate_plan(
        _stack_graph(), S.CATALOGUE, S.ORACLES,
        requirements=S.PLANNING_CONTEXT,
    ) == (True, "")


def test_stack_blocks_rejects_wrong_support_and_resolves_backend_tasks():
    graph = _stack_graph()
    graph["nodes"][1]["args"]["target"] = "table"
    ok, msg = validate_plan(
        graph, S.CATALOGUE, S.ORACLES, requirements=S.PLANNING_CONTEXT)
    assert not ok and "expected 'cubeB'" in msg

    spec = EpisodeSpec(seed=0, task="stack")
    ep = SimpleNamespace(spec=spec)
    ctx = NodeCtx(seed=0, env_ref="e", policy_ref="p", skills=(),
                  nodes_out={}, predicates={}, episode=None,
                  segment_specs=S.SEGMENT_SPECS)
    nodes = _stack_graph()["nodes"]
    assert _segment_spec(nodes[0], ep, ctx).task == "grasp_cubeA"
    assert _segment_spec(nodes[1], ep, ctx).task == "place_cubeA_on_cubeB"


def test_stack_blocks_manifest_threads_static_library_context():
    from harness.manifest import discover

    binding = discover().task_bindings["stack_blocks_vlm"]
    brief = task_brief("stack_blocks_vlm", binding)
    assert brief["catalogue"] == S.CATALOGUE
    assert brief["planning_context"] == S.PLANNING_CONTEXT
    assert binding["policy"] == "plugins.policies:stack_skill_provider"
