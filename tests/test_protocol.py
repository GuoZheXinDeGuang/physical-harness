"""One test per Legal(G) rule plus the protocol helpers (harness/protocol.py)."""

from __future__ import annotations

import pytest

from harness import protocol as P

GRASP = P.SkillRecordV0(id="r1", name="grasp", args={"object": "entity"},
                        requires=("reachable(object)", "gripper_free()"),
                        ensures=("holding(object)",), clobbers=("gripper_free()",))
PLACE = P.SkillRecordV0(id="r2", name="place", args={"object": "entity", "target": "str"},
                        requires=("holding(object)",),
                        ensures=("in(object,target)", "gripper_free()"),
                        clobbers=("holding(object)",))
LOOK = P.SkillRecordV0(id="r3", name="look", ensures=("visible(pear)",))
RECS = {r.name: r for r in (GRASP, PLACE, LOOK)}
FACTS = {"reachable(apple)", "gripper_free()"}
OBJS = {"apple", "microwave"}


def graph(nodes, goal=("in(apple,microwave)",)):
    return {"mission": "thaw", "seed": 1, "tasks": [{"id": "t", "goal": list(goal)}],
            "nodes": nodes}


GOOD = graph([
    {"id": "g", "task": "t", "skill": "grasp", "args": {"object": "apple"}, "after": []},
    {"id": "p", "task": "t", "skill": "place",
     "args": {"object": "apple", "target": "microwave"}, "after": ["g"]},
])


def test_legal_graph():
    assert P.validate_graph(GOOD, RECS, FACTS, OBJS) == (True, [])


def test_typed():
    bad = graph([{"id": "g", "task": "t", "skill": "grasp",
                  "args": {"object": 3, "extra": 1}, "after": []}])
    ok, problems = P.validate_graph(bad, RECS, FACTS, OBJS)
    assert not ok and any(p.startswith("typed:") for p in problems)


def test_bound():
    two = P.SkillRecordV0.from_dict({**P.to_plain(PLACE), "bindings": {
        "sim": {"policies": {"scripted": {"task": "place"}, "pi05": {"ref": "x:provider"}}}}})
    recs = {**RECS, "place": two}
    ok, problems = P.validate_graph(graph([
        {**GOOD["nodes"][0], "executor": "pi05"}, {**GOOD["nodes"][1], "executor": "pi05"}]),
        recs, FACTS, OBJS)
    assert not ok and len(problems) == 1 and problems[0].startswith("bound: node 'g'")
    assert P.validate_graph(graph([
        {**GOOD["nodes"][0], "executor": "scripted"}, {**GOOD["nodes"][1], "executor": "pi05"}]),
        recs, FACTS, OBJS) == (True, [])


def test_projection_executors():
    ev = {"sim": {"n": 4, "k": 4, "by_executor": {"pi05": {"n": 20, "k": 18}}}}
    two = P.SkillRecordV0.from_dict({**P.to_plain(PLACE), "evidence": ev, "bindings": {"sim": {
        "policies": {"scripted": {"task": "place"},
                     "pi05": {"ref": "x:provider", "checkpoint_sha": "ab" * 32}}}}})
    card = P.vlm_projection({"place": two, "grasp": GRASP}, (), (), (), None,
                            show_evidence=True)["skills"]
    assert card[0]["executors"] == {"scripted": {"evidence": None}}
    ex = card[1]["executors"]
    assert ex["scripted"] == {"evidence": None}            # whole-record row never lent
    assert ex["pi05"]["checkpoint_sha"] == "ab" * 32 and 0.68 < ex["pi05"]["evidence"][0] < 0.9
    hidden = P.vlm_projection({"place": two}, (), (), (), None)["skills"][0]["executors"]
    assert hidden["pi05"]["evidence"] is None
    assert "executor" in P.VLM_OUTPUT_SCHEMA["nodes"][0]


def test_grounded():
    bad = graph([{"id": "g", "task": "t", "skill": "grasp", "args": {"object": "pear"},
                  "after": []}], goal=("holding(pear)",))
    ok, problems = P.validate_graph(bad, RECS, FACTS | {"reachable(pear)"}, OBJS)
    assert not ok and problems == [
        "grounded: node 'g' arg object='pear' is not in sigma0.objects nor produced by a predecessor"]
    # produced by a predecessor -> grounded
    good = graph([{"id": "l", "task": "t", "skill": "look", "args": {}, "after": []},
                  {"id": "g", "task": "t", "skill": "grasp", "args": {"object": "pear"},
                   "after": ["l"]}], goal=("holding(pear)",))
    assert P.validate_graph(good, RECS, FACTS | {"reachable(pear)"}, OBJS)[0]


def test_supported():
    ok, problems = P.validate_graph(GOOD, RECS, {"gripper_free()"}, OBJS)
    assert not ok and problems == [
        "supported: node 'g' requires reachable(apple) which nothing provides"]


def test_threat_incomparable_node():
    # a second grasp unordered w.r.t. place clobbers gripper_free() -> threat to g
    bad = graph([
        {"id": "g", "task": "t", "skill": "grasp", "args": {"object": "apple"}, "after": []},
        {"id": "g2", "task": "t", "skill": "grasp", "args": {"object": "apple"}, "after": []},
    ], goal=("holding(apple)",))
    ok, problems = P.validate_graph(bad, RECS, FACTS, OBJS)
    assert not ok and any(p.startswith("supported:") and "threatened" in p for p in problems)
    # same clobber ordered strictly after the consumer is no threat
    ordered = graph([
        {"id": "g", "task": "t", "skill": "grasp", "args": {"object": "apple"}, "after": []},
        {"id": "p", "task": "t", "skill": "place",
         "args": {"object": "apple", "target": "microwave"}, "after": ["g"]},
        {"id": "g2", "task": "t", "skill": "grasp", "args": {"object": "apple"}, "after": ["p"]},
    ], goal=("holding(apple)",))
    assert P.validate_graph(ordered, RECS, FACTS, OBJS)[0]


def test_covered():
    ok, problems = P.validate_graph(graph(GOOD["nodes"], goal=("visible(pear)",)),
                                    RECS, FACTS, OBJS)
    assert problems == ["covered: task 't' goal visible(pear) is ensured by none of its nodes"]
    # goal holding(apple) is clobbered by place before task end
    ok, problems = P.validate_graph(graph(GOOD["nodes"], goal=("holding(apple)",)),
                                    RECS, FACTS, OBJS)
    assert problems == ["covered: task 't' goal holding(apple) is clobbered before task end"]


def test_replan_monotone():
    new = graph([dict(GOOD["nodes"][0], args={"object": "microwave"})])
    ok, problems = P.replan_monotone(GOOD, new, ["g", "p"])
    assert not ok and len(problems) == 2
    assert P.replan_monotone(GOOD, GOOD, ["g"]) == (True, [])


def test_content_id_stable():
    g = P.ExecutionGraph.from_dict(GOOD)
    assert P.content_id(g) == P.content_id(P.ExecutionGraph.from_dict(GOOD))
    assert P.content_id(g) == P.content_id(P.to_plain(g))
    assert P.content_id(g) != P.content_id(P.ExecutionGraph.from_dict(dict(GOOD, seed=2)))
    t = P.Trajectory(x={"mission": "thaw"}, y={"graph": "abc"}, o={"legal": True})
    assert t.id == P.content_id({"x": t.x, "y": t.y})


def test_three_valued_eval():
    pred = P.PredicateRecord(id="p", name="holding", args=("apple",), reads=("grasped",))
    assert P.eval_predicate(pred, {}, lambda s: s["grasped"]) is None
    assert P.eval_predicate(pred, {"grasped": 1}, lambda s: s["grasped"]) is True
    assert P.all3([True, None]) is None and P.all3([None, False]) is False and P.all3([]) is True
    assert P.fault_from_verify(P.VerifyEvent("g", {"a()": True})) is None
    assert P.fault_from_verify(P.VerifyEvent("g", {"a()": True, "b()": None})).failed == ("b()",)


def test_pred_ref_str():
    assert P.pred_ref_str(" holding( apple , x )") == "holding(apple,x)"
    assert P.pred_ref_str({"name": "free"}) == P.pred_ref_str(("free",)) == "free()"
    assert P.instantiate("in(object,target)", {"object": "apple"}) == "in(apple,target)"
    with pytest.raises(ValueError):
        P.parse_pred_ref("1bad(")


def test_replan_progress_allows_one_as_is_retry_then_no_progress():
    g = {"goal": "x", "nodes": [{"id": "a", "skill": "grasp", "args": {"object": "apple"},
                                 "after": [], "executor": "scripted"}], "verify": []}
    row = {"graph_sha": P.graph_sha(g), "node": "a", "signature": "node_failure"}
    fault = {"kind": "node_failure", "node": "a"}
    assert P.replan_progress([], g, fault) == (True, "fresh")
    assert P.replan_progress([row], g, fault) == (True, "retry")
    assert P.replan_progress([row, row], g, fault) == (False, "no_progress")
    # a hinted no_progress fault carries the original signature forward
    assert P.replan_progress([row, row], g, {**fault, "kind": "no_progress",
                                             "signature": "node_failure"})[1] == "no_progress"
    # another executor / args = a different graph; another node or signature = fresh
    alt = {**g, "nodes": [{**g["nodes"][0], "executor": "pi05"}]}
    assert P.replan_progress([row, row], alt, fault) == (True, "fresh")
    assert P.replan_progress([row, row], g, {"kind": "node_failure", "node": "b"}) == (True, "fresh")
    assert P.replan_progress([row, row], g, {"kind": "invalid_plan"}) == (True, "fresh")


def test_insert_recovery_is_the_progress_a_no_progress_fault_asks_for():
    from plugins.task.validate import plan_to_graph, validate_plan
    g = {"goal": "x", "nodes": [
        {"id": "a", "skill": "grasp", "args": {"object": "apple"}, "after": []},
        {"id": "b", "skill": "place", "args": {"object": "apple"}, "after": ["a"]}],
        "verify": [{"after": "a", "predicate": "held"}, {"after": "b", "predicate": "placed"}]}
    cat = {"grasp": {"object": str}, "place": {"object": str}}
    oracles = {"held": {}, "placed": {}}
    fault = {"kind": "no_progress", "node": "b", "signature": "node_failure"}
    rows = [{"graph_sha": P.graph_sha(g), "node": "b", "signature": "node_failure"}] * 2
    assert P.replan_progress(rows, g, fault) == (False, "no_progress")
    g2 = P.insert_recovery(g, "b", "reapproach")
    assert [n["id"] for n in g2["nodes"]] == ["a", "recover-b", "b"]
    assert g2["nodes"][1] == {"id": "recover-b", "skill": "reapproach", "args": {},
                              "kind": "recovery", "after": ["a"]}
    assert g2["nodes"][2]["after"] == ["a", "recover-b"]
    assert P.replan_progress(rows, g2, fault) == (True, "fresh")
    assert P.replan_monotone(plan_to_graph(g), plan_to_graph(g2), ["a"]) == (True, [])
    # the strategy name is not a catalogue skill, and a recovery node takes no args
    assert validate_plan(g2, cat, oracles) == (True, "")
    assert not validate_plan({**g2, "nodes": [{**g2["nodes"][1], "args": {"x": 1}}, *g2["nodes"][::2]]},
                             cat, oracles)[0]
    assert P.insert_recovery(g2, "b", "reapproach") == g2     # idempotent
    with pytest.raises(KeyError):
        P.insert_recovery(g, "zz", "reapproach")
