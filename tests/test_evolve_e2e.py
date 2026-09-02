"""The lightweight evolve loop end to end: an ``evolve`` brief through the REAL
scripts/harness_runtime.py (evolution mode) spawning the REAL scripts/evolve.py.
No simulator: a tmp task card (PH_PLUGINS_EXTRA) whose ``grab`` record binds
scripted + an ``alt`` executor; the scripted driver fails ``grab`` deterministically,
``alt`` succeeds, and the record's ``by_executor`` evidence says so -- so round 1
switches grab-0's executor (published: 0/2 -> 2/2), round 2 has nothing to try.
Then cancel mid-run and resubmit: the loop resumes from cursor (round 3).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from test_mission_e2e import SESSION, _Runtime, _kinds, _wait

from board import mcp_server as ms
from board import store as bs
from board import storecli
from harness import fakes, media, protocol
from harness.skill_library import segment_specs

EMB = "test_evolve_e2e:env_provider"
ALT = "test_evolve_e2e:alt_provider"

RECORDS = {
    "reach": {"id": "reach", "name": "reach", "kind": "segment", "args": {},
              "bindings": {EMB: {"task": "reach"}}},
    "grab": {"id": "grab", "name": "grab", "kind": "segment", "args": {},
             "bindings": {EMB: {"policies": {"scripted": {"task": "grab"},
                                             "alt": {"ref": ALT}}}},
             "evidence": {EMB: {"n": 4, "k": 0, "by_executor": {"alt": {"n": 4, "k": 4}}}}},
}
CATALOGUE = {"reach": {}, "grab": {}}
ORACLES = ("seg_ok",)
EPISODE = {"task": "reach", "horizon": 20}
SEGMENT_SPECS = segment_specs(
    {k: protocol.SkillRecordV0.from_dict(v) for k, v in RECORDS.items()}, EMB)


_OBS = {"robot0_gripper_qpos": [0.03, -0.03], "robot0_gripper_qvel": [0.0, 0.0],
        "robot0_joint_vel": [0.0] * 7, "robot0_eef_pos": [0.0, 0.0, 1.0],
        "cubeA_pos": [0.0, 0.0, 0.1]}   # what the governed loop's step features read


class _Handle(fakes._FakeEnvHandle):
    """The stdlib fake env (synthetic 128px ``frame()`` for the media recorder),
    slowed so a cancel lands mid-campaign."""

    def reset(self):
        time.sleep(0.2)
        super().reset()
        return dict(_OBS)

    def step(self, action):
        self.t += 1
        return dict(_OBS), 0.0, False, {}


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
    """Scripted episode driver: grab succeeds only under a non-scripted executor.
    Each segment drives STEPS env steps (so the media recorder sees frames)."""
    STEPS = 8
    n = 0

    @property
    def exhausted(self):
        return self.n >= self.STEPS

    def observe_once(self, obs):
        pass

    def on_handback(self):
        pass

    def act(self, obs):
        self.n += 1
        return (0.0,)

    def enter_segment(self, env, spec, executor=None):
        self.n = 0
        self.ok = spec.task != "grab" or executor is not None

    def segment_success(self, env):
        return self.ok


class _Policy:
    def make_driver(self, spec):
        return _Driver()


class _Alt:
    def make_driver(self, spec):
        return object()


class _Planner:
    identity = "evolve_e2e:fixed"

    def plan(self, brief):
        return {"goal": "reach then grab",
                "nodes": [{"id": "reach-0", "skill": "reach", "kind": "segment", "args": {}, "after": []},
                          {"id": "grab-0", "skill": "grab", "kind": "segment", "args": {},
                           "after": ["reach-0"]}],
                "verify": [{"after": "reach-0", "predicate": "seg_ok"},
                           {"after": "grab-0", "predicate": "seg_ok"}],
                "rationale": "fixed"}


def env_provider():
    return _Env()


def policy_provider():
    return _Policy()


def alt_provider(**params):
    return _Alt()


def planner_provider():
    return _Planner()


_CARD = f"""
[task_bindings.e2e_evolve]
env = "{EMB}"
policy = "test_evolve_e2e:policy_provider"
planner = "test_evolve_e2e:planner_provider"
catalogue = "test_evolve_e2e:CATALOGUE"
records = "test_evolve_e2e:RECORDS"
oracles = "test_evolve_e2e:ORACLES"
episodic = true
episode = "test_evolve_e2e:EPISODE"
segment_specs = "test_evolve_e2e:SEGMENT_SPECS"
max_replans = 1
"""

TASK = "e2e_evolve"


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    rt = _Runtime(tmp_path_factory.mktemp("runs"), card=_CARD, mode="evolution")
    rt.campaign = rt.session / "campaigns" / f"evolve-{TASK}" / "campaign.json"
    yield rt
    rt.stop()


def _doc(runtime) -> dict:
    return json.loads(runtime.campaign.read_text())


@pytest.fixture(scope="module")
def two_rounds(runtime):
    return runtime.run({"kind": "evolve", "task": TASK, "seeds": [1, 2], "rounds": 2,
                        "arm": "auto"})


def test_two_rounds_land_in_campaign_json_and_the_chain(runtime, two_rounds):
    name, rows = two_rounds
    doc = _doc(runtime)
    assert doc["status"] == "done" and doc["cursor"] == 2 and doc["best"] == 2
    assert doc["task"] == TASK and doc["seeds"] == [1, 2] and doc["arm"] == "auto"
    r1, r2 = doc["rounds"]
    assert r1["tried"]["kind"] == "executor" and r1["tried"]["node"] == "grab-0"
    assert r1["tried"]["detail"]["from"] == "scripted" and r1["tried"]["detail"]["to"] == "alt"
    assert (r1["before"], r1["after"], r1["published"], r1["best"]) == (0, 2, True, 2)
    assert r2["tried"]["kind"] == "none" and (r2["before"], r2["after"], r2["published"]) == (2, 2, False)
    assert all(len(r["suite_sha"]) == 64 for r in (r1, r2))
    # media: only verified segments were kept (both nodes, both seeds after the switch),
    # synthetic frames encoded under 1 MB, session-relative paths as rsi_frames returns them
    assert set(r1["media"]) == set(r2["media"]) == {
        f"media/{TASK}/{seed}/{node}.gif" for seed in (1, 2) for node in ("reach-0", "grab-0")}
    for rel in r1["media"]:
        f = runtime.session / rel
        assert f.is_file() and 0 < f.stat().st_size <= media.MAX_BYTES, rel
    for seed in (1, 2):
        idx = media.index_of(runtime.session / "media", TASK, seed)
        assert set(idx) == {"reach-0", "grab-0"} and all(v["frames"] > 0 for v in idx.values())
    # published = the record with its measured by_executor row, through the skills-root door
    rec = json.loads((runtime.session / "skills" / f"{r1['tried']['detail']['digest']}.json").read_text())
    assert rec["name"] == "grab" and rec["evidence"][EMB]["by_executor"]["alt"] == {"n": 6, "k": 6}
    steps = _kinds(rows, "rsi_step")
    assert [(s["round"], s["before"], s["after"], s["published"]) for s in steps] == \
        [(1, 0, 2, True), (2, 2, 2, False)]
    assert all(s["brief"] == name and s["task"] == TASK and s["suite_sha"] for s in steps)
    assert not _kinds(rows, "runtime.task_error")


def test_three_faces_agree_on_the_real_campaign(runtime, two_rounds, capsys):
    """rsi_run / rsi_series / rsi_frames byte-equal across library, CLI and MCP on
    the campaign.json the real run wrote (not a fixture)."""
    sd = runtime.session
    ms.configure(runtime.runs)
    base = ["--runs", str(runtime.runs), "--session", SESSION]
    cases = [
        (["rsi_run", TASK], bs.rsi_run(sd, TASK), ms.rsi_run(TASK)),
        (["rsi_series", TASK], bs.rsi_series(sd, TASK), ms.rsi_series(TASK)),
        (["rsi_frames", TASK, "--round", "1"], bs.rsi_frames(sd, TASK, 1), ms.rsi_frames(TASK, 1)),
    ]
    for argv, lib, mcp in cases:
        code = storecli.main(argv + base)
        out = capsys.readouterr().out.rstrip("\n")
        assert code == 0 and out == json.dumps(lib) == json.dumps(mcp), argv
    doc = _doc(runtime)
    assert bs.rsi_run(sd, TASK) == {**doc, "latest": doc["rounds"][-1]}
    assert [s["after"] for s in bs.rsi_series(sd, TASK)] == [2, 2]
    assert bs.rsi_frames(sd, TASK, 1) == doc["rounds"][0]["media"]


def test_cancel_lands_and_resubmit_resumes_from_cursor(runtime, two_rounds):
    before = len(bs.chain_rows(runtime.session))
    name = bs.submit_brief(runtime.runs, json.dumps(
        {"kind": "evolve", "task": TASK, "seeds": [1, 2], "rounds": 4}), session=SESSION)["submitted"]
    _wait(lambda: (runtime.session / "processing" / name).exists(), 30, "claim")
    assert bs.cancel_brief(runtime.session, name)["requested"] is True
    _wait(lambda: (runtime.session / "cancelled" / name).exists(), 60, "cancelled filing")
    rows = bs.chain_rows(runtime.session)[before:]
    assert _kinds(rows, "runtime.task_cancelled")[0]["brief"] == name
    doc = _doc(runtime)
    assert doc["status"] == "cancelled" and doc["cursor"] == 2 and len(doc["rounds"]) == 2
    # resume: same task again -> continues at round 3
    _, rows = runtime.run({"kind": "evolve", "task": TASK, "rounds": 3})
    doc = _doc(runtime)
    assert doc["status"] == "done" and doc["cursor"] == 3
    assert [r["round"] for r in doc["rounds"]] == [1, 2, 3]
    assert doc["rounds"][2]["tried"]["kind"] == "none" and doc["rounds"][2]["best"] == 2
    assert [s["round"] for s in _kinds(rows, "rsi_step")] == [3]


def test_evolve_is_refused_outside_evolution_mode(tmp_path_factory):
    rt = _Runtime(tmp_path_factory.mktemp("runs"), card=_CARD)
    try:
        _, rows = rt.run({"kind": "evolve", "task": TASK, "rounds": 1}, expect="failed")
        assert "evolution mode" in _kinds(rows, "runtime.task_error")[0]["error"]
    finally:
        rt.stop()
