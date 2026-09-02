"""The executor contract (harness/skill_executor.py): the normalized handshake,
step/segment discrimination, and the three step executors that satisfy it."""

import json
import sys

import numpy as np
import pytest

from harness.skill_executor import (
    InprocExecutor, SegmentExecutor, StepExecutor, is_segment, normalize_handshake,
)
from harness.skill_library import RECORDS, ROOT, load_records, rearm, segment_specs
from harness.skill_record import SkillRecordError, validate_capability

VLA = "plugins.policy_vla_remote:provider"
SHA = "08bae6a74c5d1ba90393f5bd59c9d0d1b39629a36d39e047fa7942361f3b1437"


def test_normalize_handshake_shape_and_sha_lookup():
    flat = normalize_handshake("mcp", "m:p", {"checkpoint_sha": SHA, "name": "svc"})
    nested = normalize_handshake("ssp", VLA, {"metadata": {"checkpoint_sha": SHA},
                                             "unverified": ["views"]})
    bare = normalize_handshake("inproc", "m:p")
    for hs in (flat, nested, bare):
        assert set(hs) == {"transport", "ref", "checkpoint_sha", "unverified", "ok", "meta"}
        assert hs["ok"] is True
    assert flat["checkpoint_sha"] == nested["checkpoint_sha"] == SHA
    assert bare["checkpoint_sha"] is None and bare["unverified"] == []
    assert nested["unverified"] == ["views"] and nested["meta"]["metadata"]["checkpoint_sha"] == SHA
    assert normalize_handshake("mcp", "m:p", {"ok": False})["ok"] is False
    with pytest.raises(ValueError, match="unknown executor transport 'grpc'"):
        normalize_handshake("grpc", "m:p")


def test_is_segment_discriminates():
    class Seg:
        def handshake(self): return normalize_handshake("mcp", "m:p")
        def run(self, spec, deadline_s): return {"ok": True, "diagnostics": {}}

    class Step(InprocExecutor):
        def act(self, obs): return obs

    assert is_segment(Seg()) and isinstance(Seg(), SegmentExecutor)
    assert not is_segment(Step()) and isinstance(Step(), StepExecutor)
    assert Step().handshake()["transport"] == "inproc" and Step().done() is False


def test_three_step_executors_conform():
    from plugins.embodiment_robocasa.kitchen_driver import KitchenThawDriver
    from plugins.policies.drivers import GraspPose, GraspPoseDriver, ScriptedDriver
    from plugins.policy_vla_remote import RemoteChunkDriver
    from harness.spec import EpisodeSpec

    spec = EpisodeSpec(task="lift", seed=0)
    grasp = GraspPoseDriver(spec, GraspPose(position=np.zeros(3), yaw=0.0, width=0.04))
    remote = RemoteChunkDriver(client=None, handshake={"metadata": {"checkpoint_sha": SHA},
                                                       "unverified": []})
    for drv in (ScriptedDriver(spec), grasp, KitchenThawDriver(), remote):
        assert isinstance(drv, StepExecutor) and not is_segment(drv), type(drv)
        hs = drv.handshake()
        assert set(hs) >= {"transport", "ref", "checkpoint_sha", "unverified", "ok"}
        json.dumps(hs)
        assert isinstance(drv.done(), bool) and isinstance(drv.diagnostics(), dict)
    assert grasp.handshake()["transport"] == "inproc"
    assert remote.handshake() == {"transport": "ssp", "ref": VLA, "checkpoint_sha": SHA,
                                  "unverified": [], "ok": True,
                                  "meta": {"metadata": {"checkpoint_sha": SHA},
                                           "unverified": []}}
    assert KitchenThawDriver().done() is True        # no stage armed -> exhausted


def test_records_carry_explicit_transport_and_rearm_hands_it_over():
    pol = RECORDS["place_meat"].bindings["robocasa"]["policies"]
    assert pol["scripted"]["transport"] == "inproc" and pol["pi05"]["transport"] == "ssp"
    for rec in RECORDS.values():
        for b in rec.bindings.values():
            for p in (b.get("policies") or {}).values():
                assert "transport" in p, rec.name
    spec = segment_specs(RECORDS, "robocasa")["place_meat"]
    assert rearm(spec, "pi05") == {
        "key": "pi05", "transport": "ssp", "ref": VLA, "checkpoint_sha": SHA,
        "params": {"checkpoint_sha": SHA},
        "spec": {"task": "place_meat", "policy_provider": VLA}}
    assert rearm(spec, "scripted") == {
        "key": "scripted", "transport": "inproc", "ref": None, "checkpoint_sha": None,
        "params": {}, "spec": {"task": "place_meat"}}
    mcp = {"task": "x", "policies": {"mcp_fake": {
        "transport": "mcp", "ref": "plugins.executor_mcp_segment:provider",
        "params": {"command": ["python", "svc.py"]}}}}
    assert rearm(mcp, "auto", "mcp_fake") == {
        "key": "mcp_fake", "transport": "mcp", "ref": "plugins.executor_mcp_segment:provider",
        "checkpoint_sha": None, "params": {"command": ["python", "svc.py"]},
        "spec": {"task": "x", "policy_provider": "plugins.executor_mcp_segment:provider"}}


def test_unknown_transport_refused_at_load_and_at_publish(tmp_path):
    rec = json.loads((ROOT / "place_meat.json").read_text())
    rec["bindings"]["robocasa"]["policies"]["pi05"]["transport"] = "grpc"
    (tmp_path / "place_meat.json").write_text(json.dumps(rec))
    with pytest.raises(ValueError, match="transport 'grpc'"):
        load_records(tmp_path)
    cap = {"kind": "capability", "skill": "s", "task": "t",
           "binding": {"ref": "m:f", "transport": "grpc"},
           "preconditions": ["m:p"], "effects": ["m:q"],
           "measured": {"predicate": "m:q", "successes": 1, "n": 1}}
    with pytest.raises(SkillRecordError, match="binding.transport"):
        validate_capability(cap)
    validate_capability({**cap, "binding": {"ref": "m:f", "transport": "mcp"}})


def test_mcp_client_survives_chatty_server():
    """The fake service flushes a notifications/message line together with each
    reply; the stdio client must still find the reply (a select()+readline()
    client died here with TimeoutError)."""
    from plugins.executor_mcp_segment import provider
    p = provider(command=[sys.executable, "tests/fakes/mcp_segment_service.py"])
    try:
        d = p.make_driver({})
        assert d.handshake()["transport"] == "mcp" and d.handshake()["ok"] is True
        assert d.run({"skill": "s", "args": {}, "sigma": {}}, deadline_s=1.0)["ok"] is True
        assert d.run({"skill": "s", "args": {"fail": True}, "sigma": {}}, deadline_s=1.0)["ok"] is False
    finally:
        p.close()
