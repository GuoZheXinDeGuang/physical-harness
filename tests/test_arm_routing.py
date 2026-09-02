"""Executor arms over one skill graph: a record's ``bindings.<emb>.policies`` names
a provider per arm; ``arm=pi05`` runs the segments it binds under that provider
and the rest scripted (handover), and the node seal attributes the segment to
the provider ref + its handshake (checkpoint_sha)."""

from types import SimpleNamespace
from typing import ClassVar

import pytest

from harness.skill_library import ARMS, RECORDS, rearm, segment_specs
from harness.spec import EpisodeSpec
from plugins.mission_kitchen_thaw.planner import SEGMENT_SPECS
from plugins.task import workload
from plugins.task.workload import EpisodeContext, NodeCtx, _segment, _segment_spec

VLA = "plugins.policy_vla_remote:provider"
#: the pinned weights identity travels from the record into policy_params
PIN = RECORDS["place_meat"].bindings["robocasa"]["policies"]["pi05"]["checkpoint_sha"]


def test_segment_specs_route_by_arm():
    assert ARMS == {"scripted", "pi05"}
    pi05 = segment_specs(RECORDS, "robocasa", "pi05")
    assert pi05["place_meat"] == {"task": "place_meat", "policy_provider": VLA,
                                  "policy_params": {"checkpoint_sha": PIN}}
    assert pi05["grasp_meat"] == {"task": "grasp_meat"}          # handover: scripted
    scripted = segment_specs(RECORDS, "robocasa", "scripted")
    assert scripted["place_meat"] == {"task": "place_meat"}
    assert all("policy_provider" not in s for s in scripted.values())
    # the mission constant carries the arms along for the runtime to rearm per brief
    assert rearm(SEGMENT_SPECS["place_meat"], "pi05") == pi05["place_meat"]
    with pytest.raises(ValueError, match="unknown arm 'nope'"):
        segment_specs(RECORDS, "robocasa", "nope")


def test_unknown_arm_refused_at_brief_validation():
    with pytest.raises(ValueError, match="unknown arm 'nope'"):
        workload.run({"arm": "nope"}, kernel=None, seed=0)


class _Driver:
    def __init__(self):
        self.entered = []

    def enter_segment(self, env, spec, executor=None):
        self.entered.append((spec.task, executor))

    exhausted = True

    def segment_success(self, env):
        return True


class _Remote:
    handshake: ClassVar[dict] = {"metadata": {"checkpoint_sha": "ab" * 32}}
    resets = 0

    def reset(self):
        self.resets += 1


def _run(monkeypatch, arm, executor=None):
    spec = EpisodeSpec(seed=0, task="kitchen_thaw", policy_provider="scripted:provider")
    ep = EpisodeContext(SimpleNamespace(), env=object(), driver=_Driver(), spec=spec, obs={})
    ctx = NodeCtx(seed=0, env_ref="e", policy_ref=spec.policy_provider, skills=(),
                  nodes_out={}, predicates={}, episode=ep, segment_specs=SEGMENT_SPECS, arm=arm)
    loads = []
    monkeypatch.setattr(workload, "mount_params", lambda ref: {"chunk": 10})
    monkeypatch.setattr(workload, "load_provider", lambda ref, params: (
        loads.append((ref, params)), SimpleNamespace(make_driver=lambda spec: _Remote()))[1])
    monkeypatch.setattr(workload, "assemble_bundle", lambda skills, task: (None, []))
    monkeypatch.setattr(workload, "_governed_segment",
                        lambda ep, spec, bundle, *, step_budget, executor=None: (
                            ep.driver.enter_segment(ep.env, spec, executor=executor),
                            {"success": True, "steps": 1, "stages": [], "obs": {}})[1])
    def node(skill):
        n = {"id": skill, "skill": skill, "kind": "segment", "args": {}}
        return {**n, "executor": executor} if executor and skill == "place_meat" else n
    return ep, ctx, loads, [_segment(node(s), ctx) for s in ("grasp_meat", "place_meat", "place_meat")]


def test_pi05_arm_hands_place_to_the_provider_and_seals_it(monkeypatch):
    ep, ctx, loads, out = _run(monkeypatch, "pi05")
    assert _segment_spec({"id": "g", "skill": "grasp_meat", "args": {}}, ep, ctx).policy_provider \
        == ep.spec.policy_provider
    assert loads == [(VLA, {"chunk": 10, "checkpoint_sha": PIN})]   # connected once per episode, gate pinned
    grasp, place, _ = out
    assert "driver" not in grasp                      # scripted: the episode's driver
    assert place["driver"] == {"ref": VLA, "handshake": _Remote.handshake}
    assert [r["executor"] for r in out] == ["scripted", "pi05", "pi05"]
    execs = [x for _, x in ep.driver.entered]
    assert execs[0] is None and isinstance(execs[1], _Remote) and execs[2] is not execs[1]


def test_scripted_arm_is_unchanged(monkeypatch):
    ep, _, loads, out = _run(monkeypatch, "scripted")
    assert loads == [] and all("driver" not in r for r in out)
    assert all(x is None for _, x in ep.driver.entered)
    assert all(r["executor"] == "scripted" for r in out)


def test_explicit_executor_wins_over_arm(monkeypatch):
    # explicit pi05 under the scripted arm routes place_meat to the provider
    _, _, loads, out = _run(monkeypatch, "scripted", executor="pi05")
    assert loads == [(VLA, {"chunk": 10, "checkpoint_sha": PIN})]
    assert out[1]["executor"] == "pi05" and out[1]["driver"]["ref"] == VLA
    # explicit scripted on the two-policy record stays under the episode driver
    _, _, loads, out = _run(monkeypatch, "pi05", executor="scripted")
    assert loads == [] and all(r["executor"] == "scripted" and "driver" not in r for r in out)


def test_auto_arm_falls_back_to_scripted_without_node_executor(monkeypatch):
    _, _, loads, out = _run(monkeypatch, "auto")
    assert loads == [] and all(r["executor"] == "scripted" for r in out)
    assert rearm(SEGMENT_SPECS["place_meat"], "auto") == {"task": "place_meat"}
    assert rearm(SEGMENT_SPECS["place_meat"], "auto", "pi05")["policy_provider"] == VLA
    with pytest.raises(ValueError, match="unknown executor 'nope'"):
        rearm(SEGMENT_SPECS["place_meat"], "auto", "nope")
    with pytest.raises(ValueError, match="unknown executor 'pi05'"):
        rearm(SEGMENT_SPECS["grasp_meat"], "auto", "pi05")     # explicit key the record lacks

