"""The MCP segment executor end to end (base lane, no simulator): a tmp task card
whose ``grab`` record binds ``policies.mcp_fake = {transport: mcp, ref:
plugins.executor_mcp_segment:provider, params: {command: [...]}}`` (the fake stdio
MCP service tests/fakes/mcp_segment_service.py), arm ``auto``, planner_vlm over the
fake endpoint picking ``mcp_fake`` for grab, all through the REAL
scripts/harness_runtime.py subprocess. Asserted: task.verify.driver.handshake is
the normalized shape with transport "mcp", the service was called with the node's
spec, and ok=false yields task.fault + a replan. Nothing under runs/.
"""

from __future__ import annotations

import json
import sys

import pytest

from harness import protocol
from harness.skill_library import segment_specs
from test_mission_e2e import REPO, _Runtime, _kinds

EMB = "test_executor_mcp_e2e:env_provider"
REF = "plugins.executor_mcp_segment:provider"
FAKE = REPO / "tests" / "fakes" / "mcp_segment_service.py"

RECORDS = {
    "reach": {"id": "reach", "name": "reach", "kind": "segment", "args": {},
              "bindings": {EMB: {"task": "reach"}}},
    "grab": {"id": "grab", "name": "grab", "kind": "segment", "args": {"fail": "bool"},
             "bindings": {EMB: {"policies": {
                 "scripted": {"task": "grab"},
                 "mcp_fake": {"transport": "mcp", "ref": REF,
                              "params": {"command": [sys.executable, str(FAKE)]}}}}}},
}
CATALOGUE = {"reach": {}, "grab": {"fail": bool}}
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


def env_provider():
    return _Env()


def policy_provider():
    return _Policy()


def vlm_planner_provider():
    from plugins.planner_vlm import provider
    return provider(endpoint="plugins.model_endpoint:fake_provider", endpoint_params={})


_CARD = f"""
[task_bindings.e2e_mcp]
env = "{EMB}"
policy = "test_executor_mcp_e2e:policy_provider"
planner = "test_executor_mcp_e2e:vlm_planner_provider"
catalogue = "test_executor_mcp_e2e:CATALOGUE"
records = "test_executor_mcp_e2e:RECORDS"
oracles = "test_executor_mcp_e2e:ORACLES"
episodic = true
episode = "test_executor_mcp_e2e:EPISODE"
segment_specs = "test_executor_mcp_e2e:SEGMENT_SPECS"
"""


def _graph(fail: bool) -> dict:
    return {"goal": "reach then grab",
            "nodes": [{"id": "reach-0", "skill": "reach", "kind": "segment", "args": {},
                       "after": [], "executor": "scripted"},
                      {"id": "grab-0", "skill": "grab", "kind": "segment",
                       "args": {"fail": fail}, "after": ["reach-0"], "executor": "mcp_fake"}],
            "verify": [{"after": "reach-0", "predicate": "seg_ok"},
                       {"after": "grab-0", "predicate": "seg_ok"}],
            "rationale": "grab over mcp"}


#: case 1's graph, then case 2's plan + its one replan (the same failing graph)
_CANNED = [_graph(False), _graph(True), _graph(True)]


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    rt = _Runtime(runs, card=_CARD, canned=_CANNED,
                  env={"PH_FAKE_MCP_LOG": str(runs / "mcp_calls.json")})
    rt.log = runs / "mcp_calls.json"
    yield rt
    rt.stop()


def test_mcp_segment_runs_and_seals_normalized_handshake(runtime):
    _, rows = runtime.run({"kind": "task", "task": "e2e_mcp", "seed": 21, "arm": "auto"})
    assert _kinds(rows, "task.plan")[0]["legal"] is True
    verify = {v["node"]: v for v in _kinds(rows, "task.verify")}
    assert verify["reach-0"]["executor"] == "scripted" and "driver" not in verify["reach-0"]
    grab = verify["grab-0"]
    assert grab["executor"] == "mcp_fake" and grab["results"] == {"seg_ok": True}
    assert grab["driver"]["ref"] == REF
    assert grab["driver"]["handshake"] == {
        "transport": "mcp", "ref": REF, "checkpoint_sha": None, "unverified": [], "ok": True,
        "meta": {"name": "ph-fake-mcp-segment", "version": "0"}}
    # the service was really called, with the node's spec
    calls = json.loads(runtime.log.read_text())
    assert len(calls) == 1
    assert calls[0]["skill"] == "grab" and calls[0]["args"] == {"fail": False}
    assert calls[0]["deadline_s"] > 0 and isinstance(calls[0]["sigma"], dict)
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is True and end["nodes"]["grab-0"]["diagnostics"]["served"] == "fake"
    assert not _kinds(rows, "runtime.task_error")


def test_mcp_ok_false_faults_and_replans(runtime):
    runtime.log.unlink()
    _, rows = runtime.run({"kind": "task", "task": "e2e_mcp", "seed": 22, "arm": "auto",
                           "max_replans": 1})
    assert len(_kinds(rows, "task.plan")) == 2  # plan + one replan, both legal
    faults = _kinds(rows, "task.fault")
    assert [f["node"] for f in faults] == ["grab-0", "grab-0"]
    assert all(f["signature"] == "node_failure" for f in faults)
    grabs = [v for v in _kinds(rows, "task.verify") if v["node"] == "grab-0"]
    assert [v["results"] for v in grabs] == [{"seg_ok": False}] * 2
    assert all(v["driver"]["handshake"]["transport"] == "mcp" for v in grabs)
    calls = json.loads(runtime.log.read_text())
    assert [c["args"] for c in calls] == [{"fail": True}] * 2
    end = _kinds(rows, "task.plan_complete")[0]
    assert end["success"] is False and end["replans"] == 1
    assert not _kinds(rows, "runtime.task_error")
