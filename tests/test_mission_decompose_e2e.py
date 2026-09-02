"""Mission lane end to end, no simulator: a REAL runtime subprocess takes a
natural-language mission brief, decomposes it over the fake model endpoint
(reply 1 of a sequence), plans task 1 from a mounted PlanRecord (library) and
task 2 through planner_vlm (reply 2), composes ONE graph with real goals
(Covered bites) and runs it to a sealed episode. A decomposition naming an
unknown predicate is sealed as ``mission.refused`` and nothing dispatches; the
storecli trajectories export carries the composed plan decision with both
tasks' verifies."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from board import store as bs
from harness.protocol import graph_sha, plan_lower_bound
from plugins.graphs import InMemorySkillGraph
from test_mission_e2e import REPO, SESSION, _CANNED, _CARD, _Runtime, _StackAs

DECOMPOSER = "test_mission_e2e:vlm_planner_provider"
ENV = "test_mission_e2e:env_provider"
GOAL = "on(cubeA,cubeB)"
CARD = (_CARD.format(task="e2e_stack", planner="planner_provider")
        + _CARD.format(task="e2e_vlm", planner="vlm_planner_provider"))
#: The fake endpoint's reply SEQUENCE: decomposition, then task 2's graph, then
#: the second mission's (refused) decomposition. Task 1 replays the library.
REPLIES = [
    {"tasks": [{"id": "t1", "task": "e2e_stack", "goal": [GOAL]},
               {"id": "t2", "task": "e2e_vlm", "goal": ["on(cubeA, cubeB)"]}],
     "rationale": "stack, then stack again"},
    _CANNED,
    {"tasks": [{"id": "t1", "task": "e2e_stack", "goal": ["levitating(cubeA)"]}],
     "rationale": "no such predicate"},
]


def _plan_record() -> dict:
    graph = {k: v for k, v in _StackAs().plan({"task": "e2e_stack", "scene": {}}).items()
             if k not in ("planner", "rationale")}
    return {"kind": "plan", "id": graph_sha(graph), "task": "e2e_stack", "goal": [GOAL],
            "graph": graph, "embodiment": ENV, "arm": "scripted",
            "evidence": {"n": 12, "k": 12, "L_mean": 1.0, "seed_blocks": [], "sessions": []},
            "rule": {"theta": 0.8, "n_min": 10, "lower": plan_lower_bound(12, 12)},
            "published_from": []}


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    InMemorySkillGraph(root=str(runs / SESSION / "skills")).publish(_plan_record())
    rt = _Runtime(runs, card=CARD, canned=REPLIES, env={"PH_MISSION_DECOMPOSER": DECOMPOSER})
    yield rt
    rt.stop()


@pytest.fixture(scope="module")
def mission(runtime):
    return runtime.run({"kind": "mission", "mission": "stack cubeA on cubeB, twice",
                        "seed": 5, "arm": "scripted"})


def _kinds(rows, kind):
    return [r["data"] for r in rows if r["kind"] == kind]


def test_mission_decomposes_reuses_a_plan_record_and_seals_a_covered_episode(mission):
    _, rows = mission
    dec = _kinds(rows, "mission.decomposed")
    assert len(dec) == 1 and dec[0]["decomposer"] == DECOMPOSER and len(dec[0]["prompt_sha"]) == 64
    assert dec[0]["tasks"] == [{"id": "t1", "task": "e2e_stack", "goal": [GOAL]},
                               {"id": "t2", "task": "e2e_vlm", "goal": [GOAL]}]
    assert dec[0]["rationale"] == REPLIES[0]["rationale"] and not _kinds(rows, "mission.refused")
    plans = _kinds(rows, "task.plan")
    assert len(plans) == 1 and plans[0]["legal"] is True, plans
    plan = plans[0]
    # Covered is non-vacuous: the sealed graph carries the decomposed goals
    assert plan["graph"]["tasks"] == [{"id": "t1", "goal": [GOAL]}, {"id": "t2", "goal": [GOAL]}]
    assert [n["task"] for n in plan["graph"]["nodes"]] == ["t1", "t2"]
    assert plan["graph_sha"] == graph_sha(plan["graph"]) and "present(cubeA)" in plan["facts"]
    # two plan decisions inside the one composed row: library vs the VLM endpoint
    per_task = plan["planner"]["tasks"]
    assert plan["planner"]["provider"] == "mission" and plan["planner"]["prompt_sha"] == dec[0]["prompt_sha"]
    assert per_task["t1"] == {"provider": "library", "plan_id": _plan_record()["id"]}
    assert per_task["t2"]["provider"] == "plugins.model_endpoint:fake_provider"
    assert len(per_task["t2"]["prompt_sha"]) == 64
    assert _kinds(rows, "task.verify") == [
        {"node": "t1.stack-0", "results": {"stack_success": True}},
        {"node": "t2.stack-0", "results": {"stack_success": True}}]
    end = _kinds(rows, "task.plan_complete")
    assert len(end) == 1 and end[0]["success"] is True and end[0]["actuations"] == 2
    assert not _kinds(rows, "runtime.task_error")


def test_unknown_predicate_in_the_decomposition_is_refused_before_dispatch(runtime, mission):
    _, rows = runtime.run({"kind": "mission", "mission": "levitate cubeA", "seed": 6},
                          expect="failed")
    refused = _kinds(rows, "mission.refused")
    assert len(refused) == 1 and refused[0]["decomposer"] == DECOMPOSER
    assert "unknown predicate 'levitating'" in refused[0]["error"]
    assert refused[0]["mission"] == "levitate cubeA" and refused[0]["seed"] == 6
    for kind in ("mission.decomposed", "task.plan", "task.verify", "task.plan_complete"):
        assert _kinds(rows, kind) == []
    assert len(_kinds(rows, "runtime.task_error")) == 1


def test_trajectories_export_carries_the_composed_plan_decision(runtime, mission, tmp_path):
    out = tmp_path / "traj"
    res = subprocess.run(
        [sys.executable, "-m", "board.storecli", "trajectories", SESSION,
         "--runs", str(runtime.runs), "--out", str(out)],
        cwd=str(REPO), capture_output=True, text=True, check=True)
    assert json.loads(res.stdout)["dev"] >= 1
    dev = [json.loads(l) for l in (out / "dev.jsonl").read_text().splitlines()]
    plan = _kinds(mission[1], "task.plan")[0]
    sample = [s for s in dev if s["y"]["graph"] == plan["graph_id"]]
    assert len(sample) == 1 and sample[0]["o"]["legal"] is True and sample[0]["o"]["success"] is True
    assert sample[0]["o"]["verify"] == {"t1.stack-0": {"stack_success": True},
                                        "t2.stack-0": {"stack_success": True}}
    assert sample[0]["o"]["seed"] == 5 and sample[0]["x"]["visible"] == ["pick", "stack"]
    assert bs.plan_index(runtime.session)[0]["k"] == 1
