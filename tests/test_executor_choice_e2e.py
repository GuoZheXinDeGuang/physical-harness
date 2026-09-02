"""Per-node executor choice end to end: a tmp task card (PH_PLUGINS_EXTRA) whose
``grab`` record binds two policies (scripted + a stub ``stub2`` provider that
records it was mounted), arm ``auto``, planner_vlm over the fake endpoint
replying a canned graph that picks ``stub2`` for grab and ``scripted`` for reach,
all through the REAL scripts/harness_runtime.py subprocess. Asserted: the
task.verify rows carry the executor per node, the stub provider was actually
invoked (marker file + handshake in ``driver``), ``storecli skill_evidence``
counts per (skill, executor); a graph naming an executor the record lacks is
sealed illegal with a ``bound:`` problem and never dispatched; the projection the
planner saw (prompt_sha reconstructed from the sealed inputs) lists both
executors. No simulator, nothing under runs/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import protocol
from harness.skill_library import segment_specs
from test_mission_e2e import REPO, SESSION, _Runtime, _kinds

EMB = "test_executor_choice_e2e:env_provider"
STUB2 = "test_executor_choice_e2e:stub2_provider"
SHA = "cd" * 32

# --- the card's vocabulary (reached by the runtime by ref) ---------------------

RECORDS = {
    "reach": {"id": "reach", "name": "reach", "kind": "segment", "args": {},
              "bindings": {EMB: {"task": "reach"}}},
    "grab": {"id": "grab", "name": "grab", "kind": "segment", "args": {},
             "bindings": {EMB: {"policies": {
                 "scripted": {"task": "grab"},
                 "stub2": {"ref": STUB2, "checkpoint_sha": SHA}}}},
             "evidence": {EMB: {"n": 10, "k": 6, "by_executor": {"stub2": {"n": 4, "k": 3}}}}},
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
        raise AssertionError("heterogeneous segment path never reads object_key")

    def success(self, obs, spec, start_z):
        return True

    def terminal_success(self, obs, spec, start_z, env=None):
        return True


class _Driver:
    """The episode's scripted driver: heterogeneous (enter_segment /
    segment_success), exhausted at once so the real governed loop drives 0 steps."""
    exhausted = True

    def observe_once(self, obs):
        pass

    def on_handback(self):
        pass

    def act(self, obs):
        return (0.0,)

    def enter_segment(self, env, spec, executor=None):
        self.executor = executor

    def segment_success(self, env):
        return True


class _Policy:
    def make_driver(self, spec):
        return _Driver()


class _Stub2Driver:
    def __init__(self, params):
        self.handshake = {"metadata": {"checkpoint_sha": params.get("checkpoint_sha")}}


class _Stub2:
    """The second executor: mounting a driver leaves a marker the test reads."""

    def __init__(self, **params):
        self.params = params

    def make_driver(self, spec):
        Path(os.environ["PH_E2E_STUB2_MARKER"]).write_text(
            json.dumps({"task": spec.task, **self.params}))
        return _Stub2Driver(self.params)


def env_provider():
    return _Env()


def policy_provider():
    return _Policy()


def stub2_provider(**params):
    return _Stub2(**params)


def vlm_planner_provider():
    from plugins.planner_vlm import provider
    return provider(endpoint="plugins.model_endpoint:fake_provider", endpoint_params={})


_CARD = f"""
[task_bindings.e2e_exec]
env = "{EMB}"
policy = "test_executor_choice_e2e:policy_provider"
planner = "test_executor_choice_e2e:vlm_planner_provider"
catalogue = "test_executor_choice_e2e:CATALOGUE"
records = "test_executor_choice_e2e:RECORDS"
oracles = "test_executor_choice_e2e:ORACLES"
episodic = true
episode = "test_executor_choice_e2e:EPISODE"
segment_specs = "test_executor_choice_e2e:SEGMENT_SPECS"
"""


def _graph(grab_executor: str) -> dict:
    return {"goal": "reach then grab",
            "nodes": [{"id": "reach-0", "skill": "reach", "kind": "segment", "args": {},
                       "after": [], "executor": "scripted"},
                      {"id": "grab-0", "skill": "grab", "kind": "segment", "args": {},
                       "after": ["reach-0"], "executor": grab_executor}],
            "verify": [{"after": "reach-0", "predicate": "seg_ok"},
                       {"after": "grab-0", "predicate": "seg_ok"}],
            "rationale": f"grab under {grab_executor}"}


#: reply sequence: case 1's graph, then case 2's plan + its one replan
_CANNED = [_graph("stub2"), _graph("nope"), _graph("nope")]


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    rt = _Runtime(runs, card=_CARD, canned=_CANNED,
                  env={"PH_E2E_STUB2_MARKER": str(runs / "stub2.marker")})
    rt.marker = runs / "stub2.marker"
    yield rt
    rt.stop()


@pytest.fixture(scope="module")
def chosen(runtime):
    return runtime.run({"kind": "task", "task": "e2e_exec", "seed": 11, "arm": "auto"})


def test_executor_per_node_is_dispatched_and_sealed(runtime, chosen):
    _, rows = chosen
    plan = _kinds(rows, "task.plan")[0]
    assert plan["legal"] is True and plan["arm"] == "auto", plan["problems"]
    verify = {v["node"]: v for v in _kinds(rows, "task.verify")}
    assert verify["reach-0"] == {"node": "reach-0", "results": {"seg_ok": True},
                                 "executor": "scripted"}
    assert verify["grab-0"]["executor"] == "stub2"
    assert verify["grab-0"]["driver"] == {"ref": STUB2,
                                          "handshake": {"metadata": {"checkpoint_sha": SHA}}}
    # the stub provider really mounted a driver for the segment, under the record's pin
    assert json.loads(runtime.marker.read_text()) == {"task": "grab", "checkpoint_sha": SHA}
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is True and set(end["nodes"]) == {"reach-0", "grab-0"}
    assert not _kinds(rows, "runtime.task_error")


def test_storecli_skill_evidence_counts_per_executor(runtime, chosen):
    res = subprocess.run(
        [sys.executable, "-m", "board.storecli", "skill_evidence", SESSION,
         "--runs", str(runtime.runs)],
        cwd=str(REPO), capture_output=True, text=True, check=True)
    assert json.loads(res.stdout) == [
        {"skill": "grab", "embodiment": EMB, "executor": "stub2", "n": 1, "k": 1},
        {"skill": "reach", "embodiment": EMB, "executor": "scripted", "n": 1, "k": 1}]


def test_unbound_executor_is_refused_and_never_dispatched(runtime, chosen):
    runtime.marker.unlink()
    _, rows = runtime.run({"kind": "task", "task": "e2e_exec", "seed": 12, "arm": "auto",
                           "max_replans": 1})
    plans = _kinds(rows, "task.plan")
    assert [p["legal"] for p in plans] == [False, False]
    assert all("bound: node 'grab-0' names executor 'nope'" in p["problems"][0] for p in plans)
    assert _kinds(rows, "task.replan_rejected")
    assert _kinds(rows, "task.verify") == [] and not runtime.marker.exists()
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is False and end["actuations"] == 0
    assert {f["kind"] for f in end["faults"]} == {"invalid_plan"}


def test_projection_the_planner_saw_lists_both_executors(chosen):
    from plugins.planner_vlm import _RULES, VlmPlanner
    _, rows = chosen
    plan = _kinds(rows, "task.plan")[0]
    # the same pure inputs the runtime fed the planner, off the sealed row
    brief = {"task": "e2e_exec", "records": RECORDS, "facts": plan["facts"],
             "objects": plan["objects"], "scene": plan["sigma0"], "oracles": ORACLES,
             "budget": 3, "arm": "auto"}
    payload = VlmPlanner()._payload(brief)
    cards = {c["name"]: c for c in payload["skills"]}
    assert cards["grab"]["executors"] == {"scripted": {"evidence": None},
                                          "stub2": {"evidence": None, "checkpoint_sha": SHA}}
    assert cards["reach"]["executors"] == {"scripted": {"evidence": None}}
    assert "executor" in payload["output_schema"]["nodes"][0]
    messages = [{"role": "system", "content": _RULES},
                {"role": "user", "content": "Planning input:\n"
                 + json.dumps(payload, sort_keys=True)
                 + "\n\nOutput ONLY the plan JSON object for this input now."}]
    assert protocol.content_id(messages) == plan["graph"]["planner"]["prompt_sha"]
    # measured per-executor evidence surfaces only when asked, never invented
    shown = protocol.vlm_projection(RECORDS, plan["facts"], plan["objects"], (), None,
                                    show_evidence=True)
    ex = {c["name"]: c["executors"] for c in shown["skills"]}["grab"]
    assert ex["stub2"]["evidence"] == protocol.evidence_interval({"n": 4, "k": 3})
    assert ex["scripted"]["evidence"] is None
