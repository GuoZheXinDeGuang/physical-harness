"""The replan progress rule end to end (protocol.replan_progress, wired in
plugins/task/workload.run): a tmp task card whose ``grab`` segment fails under
the episode's scripted driver and succeeds under a second executor ``alt``,
planner_vlm over the fake endpoint, the REAL scripts/harness_runtime.py
subprocess. Sequence: G, G (the one as-is retry), G again -> sealed
``task.replan_rejected{reason:"no_progress"}`` and a ``no_progress`` fault carrying
the hint; G' (executor alt) accepted and the task succeeds. A planner that keeps
answering G ends the task honestly with that fault. No simulator, nothing under
runs/. Also: a mission card's declared ``max_actuations`` is the task-brief default.
"""

from __future__ import annotations

import pytest

from harness import protocol
from harness.skill_library import segment_specs
from plugins.task import workload
from test_mission_e2e import _Runtime, _kinds

EMB = "test_replan_progress_e2e:env_provider"
ALT = "test_replan_progress_e2e:alt_provider"

RECORDS = {
    "reach": {"id": "reach", "name": "reach", "kind": "segment", "args": {},
              "bindings": {EMB: {"task": "reach"}}},
    "grab": {"id": "grab", "name": "grab", "kind": "segment", "args": {},
             "bindings": {EMB: {"policies": {"scripted": {"task": "grab"},
                                             "alt": {"ref": ALT}}}}},
}
CATALOGUE = {"reach": {}, "grab": {}}
ORACLES = ("seg_ok",)
EPISODE = {"task": "reach", "horizon": 20}
SEGMENT_SPECS = segment_specs(
    {k: protocol.SkillRecordV0.from_dict(v) for k, v in RECORDS.items()}, EMB)


class _Handle:
    def reset(self):
        return {"t": 0.0}

    def step(self, action):
        return {"t": 1.0}, 0.0, False, {}

    def close(self):
        pass


class _Env:
    def make_env(self, spec):
        return _Handle()

    def tasks(self):
        return ("reach", "grab")

    def object_key(self, spec):
        raise AssertionError("segment path never reads object_key")

    def success(self, obs, spec, start_z):
        return True

    def terminal_success(self, obs, spec, start_z, env=None):
        return True


class _Driver:
    """Scripted episode driver: reach succeeds; grab only under another executor."""
    exhausted = True

    def observe_once(self, obs):
        pass

    def on_handback(self):
        pass

    def act(self, obs):
        return (0.0,)

    def enter_segment(self, env, spec, executor=None):
        self.ok = spec.task == "reach" or executor is not None

    def segment_success(self, env):
        return self.ok


class _Policy:
    def make_driver(self, spec):
        return _Driver()


class _Alt:
    def make_driver(self, spec):
        return object()


def env_provider():
    return _Env()


def policy_provider():
    return _Policy()


def alt_provider(**params):
    return _Alt()


def vlm_planner_provider():
    from plugins.planner_vlm import provider
    return provider(endpoint="plugins.model_endpoint:fake_provider", endpoint_params={})


_CARD = f"""
[task_bindings.e2e_progress]
env = "{EMB}"
policy = "test_replan_progress_e2e:policy_provider"
planner = "test_replan_progress_e2e:vlm_planner_provider"
catalogue = "test_replan_progress_e2e:CATALOGUE"
records = "test_replan_progress_e2e:RECORDS"
oracles = "test_replan_progress_e2e:ORACLES"
max_actuations = 7
episodic = true
episode = "test_replan_progress_e2e:EPISODE"
segment_specs = "test_replan_progress_e2e:SEGMENT_SPECS"
"""
_CARD += _CARD.replace("e2e_progress", "e2e_tiny").replace("max_actuations = 7",
                                                            "max_actuations = 1")


def _graph(grab_executor: str) -> dict:
    return {"goal": "reach then grab",
            "nodes": [{"id": "reach-0", "skill": "reach", "kind": "segment", "args": {},
                       "after": [], "executor": "scripted"},
                      {"id": "grab-0", "skill": "grab", "kind": "segment", "args": {},
                       "after": ["reach-0"], "executor": grab_executor}],
            "verify": [{"after": "reach-0", "predicate": "seg_ok"},
                       {"after": "grab-0", "predicate": "seg_ok"}],
            "rationale": f"grab under {grab_executor}"}


G, G2 = _graph("scripted"), _graph("alt")
#: planner_vlm freezes a graph per (task, seed, fault): the second identical
#: fault replays G without consuming a reply, so each run consumes three.
_CANNED = [G, G, G2, G, G, G, G]


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    rt = _Runtime(tmp_path_factory.mktemp("runs"), card=_CARD, canned=_CANNED)
    yield rt
    rt.stop()


def test_identical_graph_is_rejected_once_then_a_new_executor_is_accepted(runtime):
    _, rows = runtime.run({"kind": "task", "task": "e2e_progress", "seed": 11, "arm": "auto"})
    plans = _kinds(rows, "task.plan")
    assert [p["legal"] for p in plans] == [True, True, False, True]
    assert plans[0]["graph_sha"] == plans[1]["graph_sha"] == plans[2]["graph_sha"]
    assert plans[3]["graph_sha"] != plans[0]["graph_sha"]
    rejected = _kinds(rows, "task.replan_rejected")
    assert len(rejected) == 1 and rejected[0]["replan"] == 2
    assert rejected[0]["reason"] == "no_progress"
    assert workload.NO_PROGRESS_HINT in rejected[0]["problems"][0]
    # the rejection is a fault the planner saw: original node + signature, hint, sha
    fault = plans[3]["fault"]
    assert fault["kind"] == "no_progress" and fault["node"] == "grab-0"
    assert fault["signature"] == "node_failure" and fault["graph_sha"] == plans[0]["graph_sha"]
    assert workload.NO_PROGRESS_HINT in fault["msg"] and fault["nodes_done"] == ["reach-0"]
    verify = _kinds(rows, "task.verify")
    assert [(v["node"], v["executor"], v["results"]["seg_ok"]) for v in verify] == [
        ("reach-0", "scripted", True), ("grab-0", "scripted", False),
        ("grab-0", "scripted", False), ("grab-0", "alt", True)]
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is True and end["replans"] == 3 and end["actuations"] == 4
    assert [f["kind"] for f in end["faults"]] == ["node_failure", "node_failure", "no_progress"]
    assert not _kinds(rows, "runtime.task_error")


def test_planner_that_never_changes_ends_the_task_with_no_progress(runtime):
    _, rows = runtime.run({"kind": "task", "task": "e2e_progress", "seed": 12, "arm": "auto"})
    plans = _kinds(rows, "task.plan")
    assert [p["legal"] for p in plans] == [True, True, False, False]
    assert len({p["graph_sha"] for p in plans}) == 1
    rejected = _kinds(rows, "task.replan_rejected")
    assert [(r["replan"], r["reason"]) for r in rejected] == [(2, "no_progress"), (3, "no_progress")]
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is False and end["replans"] == 3 and end["actuations"] == 3
    assert [f["kind"] for f in end["faults"]] == [
        "node_failure", "node_failure", "no_progress", "no_progress"]
    assert not _kinds(rows, "runtime.task_error")


def test_task_brief_default_max_actuations_comes_from_the_card(runtime):
    # e2e_tiny declares max_actuations = 1: the task default (3) would dispatch both
    # nodes; the card's value refuses the second before dispatch as a budget fault.
    _, rows = runtime.run({"kind": "task", "task": "e2e_tiny", "seed": 13, "arm": "auto",
                           "max_replans": 0})
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["actuations"] == 1 and [f["kind"] for f in end["faults"]] == ["budget"]
    assert "max_actuations=1" in end["faults"][0]["msg"]
