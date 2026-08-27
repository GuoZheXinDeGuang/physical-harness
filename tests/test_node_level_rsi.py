"""Node-level RSI on a persistent-episode mission (robocasa): the JUDGEMENT points
of the two structural gaps this round closed, pinned base-lane (no sim).

* A. recovery-primitive registration + 12-dim execution -- the robocasa card now
  registers repair shapes and its driver can EXECUTE them in PandaOmron's action
  space (the gap c5 named).
* B. the isolated-segment campaign rollout -- a segment target is scored in a
  fresh world per seed (prefix ungoverned to the precondition, target governed),
  the gap that left persistent missions without a campaign path.
* the node-level gate (c5 + isolation plumbing) that ties them together.

All pure functions / fakes: the real sim is exercised by the live run, not here.
"""

from __future__ import annotations

import numpy as np
import pytest

import plugins.embodiment_robocasa.drivers as D
from harness.spec import EpisodeSpec
from plugins.embodiment_robocasa.recovery import RobocasaRecoveryActor
from plugins.rsi import governed, repertoire
from plugins.rsi.governed import Bundle
from scripts.rsi_campaign import _segment_isolation, build_prereg, gate, recovery_support


# ── A. recovery primitives ────────────────────────────────────────────────────

def test_robocasa_strategies_registered_per_card():
    assert repertoire.strategies_for("embodiment_robocasa") == ["regrasp_kitchen", "redock_retry"]
    # the tabletop card's vocabulary is untouched -- no cross-card leak either way.
    rs = repertoire.strategies_for("embodiment_robosuite")
    assert "regrasp" in rs and "regrasp_kitchen" not in rs
    assert "redock_retry" not in rs


def test_regrasp_kitchen_is_arm_mode_release_then_close(monkeypatch):
    monkeypatch.setattr(D, "_eef", lambda env: np.array([1.0, 2.0, 3.0]))
    monkeypatch.setattr(D, "_base_pose", lambda env: (np.array([0.0, 0.0]), 0.0))
    monkeypatch.setattr(D, "_obj_pos", lambda env, n: np.array([1.1, 2.1, 0.9]))
    steps = repertoire.strategy("regrasp_kitchen").steps
    act = RobocasaRecoveryActor(object(), "regrasp_kitchen", steps, obj_name="meat")
    grips, modes = [], []
    while not act.done:
        a = act.act({})
        assert a.shape == (D.ADIM,)
        modes.append(a[D.MODE])
        grips.append(a[D.GRIP])
    # 松爪→抬升→重新垂降闭合: arm mode throughout, opens first, closes last.
    assert all(m == D.GRIP_OPEN for m in modes), ("regrasp is arm mode (MODE=-1)", modes)
    assert grips[0] == D.GRIP_OPEN and grips[-1] == D.GRIP_CLOSE


def test_redock_retry_is_base_mode(monkeypatch):
    monkeypatch.setattr(D, "_base_pose", lambda env: (np.array([0.0, 0.0]), 0.0))

    class _FakeNav:
        def __init__(self, fx):
            self.fx = fx

        def act(self, env, obs):
            a = D._zero()
            a[D.MODE] = D.GRIP_CLOSE  # +1 base mode
            return a

    monkeypatch.setattr(D, "NavigateDriver", _FakeNav)
    steps = repertoire.strategy("redock_retry").steps
    act = RobocasaRecoveryActor(object(), "redock_retry", steps, fixture_name="fridge")
    a0 = act.act({})
    assert a0[D.MODE] == D.GRIP_CLOSE and a0[7] < 0, ("backout reverses in base mode", a0)


def test_unknown_recovery_phase_fails_loud():
    with pytest.raises(ValueError):
        RobocasaRecoveryActor(object(), "x", (("teleport", 1, 0.0, 0.0),))


# ── c5. node-level recovery-primitive gate ────────────────────────────────────

def test_recovery_support_kitchen_grasp_is_governable():
    grasp = {"id": "grasp", "skill": "grasp", "kind": "segment",
             "after": ["at-fridge"], "args": {}}
    s = recovery_support("kitchen_thaw", grasp)
    assert s["supported"] and s["card"] == "embodiment_robocasa"
    assert s["repertoire"] == ["regrasp_kitchen", "redock_retry"]
    assert s["driver"] == "KitchenThawDriver" and not s["blockers"]


def _cal(base_rate=0.49, successes=74, per_ep=30.0, budget=0, n=150):
    return {"n": n, "base_rate": base_rate, "successes": successes,
            "seconds_per_episode": per_ep, "seconds_total": n * per_ep,
            "budget_exhaust": budget}


def test_gate_c5_decides_on_support():
    attr = {"governable_deaths": 76, "ungoverned_deaths": 0, "target": "grasp"}
    blocked = gate(_cal(), attr, {"supported": False, "reason": "no primitive"}, workers=10)
    assert not blocked["proceed"] and "c5_recovery_primitive" in blocked["failed"]
    passed = gate(_cal(), attr, {"supported": True, "reason": "ok"}, workers=10)
    assert passed["proceed"] and passed["target_node"] == "grasp", passed["failed"]


def test_gate_c1_still_catches_degenerate_node_rate():
    attr = {"governable_deaths": 0, "ungoverned_deaths": 0, "target": "grasp"}
    v = gate(_cal(base_rate=0.0, successes=0), attr,
             {"supported": True, "reason": "ok"}, workers=10)
    assert not v["proceed"] and "c1_base_degenerate" in v["failed"]


# ── isolation plumbing ────────────────────────────────────────────────────────

_KITCHEN_GRAPH = [
    {"id": "survey", "skill": "survey", "kind": "perceive", "after": [], "args": {}},
    {"id": "plan", "skill": "plan", "kind": "decide", "after": ["survey"], "args": {}},
    {"id": "nav-fridge", "skill": "nav_fridge", "kind": "segment", "after": ["plan"], "args": {}},
    {"id": "at-fridge", "skill": "v_at_fridge", "kind": "verify", "after": ["nav-fridge"], "args": {}},
    {"id": "grasp", "skill": "grasp", "kind": "segment", "after": ["at-fridge"], "args": {}},
    {"id": "grasped", "skill": "v_grasped", "kind": "verify", "after": ["grasp"], "args": {}},
]
_GRASP = _KITCHEN_GRAPH[4]


def test_segment_isolation_chains_prefix_then_target():
    mission, subgoals, horizon = _segment_isolation(
        "kitchen_thaw", _GRASP, {"graph": _KITCHEN_GRAPH})
    # the target's segment ancestors, oldest first, mapped through SEGMENT_SPECS.
    assert mission == "kitchen_thaw"
    assert subgoals == ("nav_fridge", "grasp_meat")
    assert horizon >= 1150  # above the summed nav+grasp caps so grasp never truncates


def test_build_prereg_threads_segment_isolate():
    cal = {"graph": _KITCHEN_GRAPH, "episodes": []}
    allowed = repertoire.strategies_for("embodiment_robocasa")
    p = build_prereg("kitchen_thaw", _GRASP, cal, (52300, 52301), (52600, 52601), allowed)
    assert p.task == "kitchen_thaw"                      # mission env, not "grasp_meat"
    assert p.segment_isolate == ("nav_fridge", "grasp_meat")
    assert p.horizon >= 1150
    assert p.recovery_name == "regrasp_kitchen"          # the card's grasp repair
    assert p.critic_budget == 0                          # zero-privilege trigger search


# ── B. the isolated-segment campaign rollout ──────────────────────────────────

class _FakeEnv:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fake_episodic_driver(success_by_task):
    class _FD:
        identity = "fake_kitchen@v1"

        def enter_segment(self, env, spec):
            self.cur = spec.task

        def segment_success(self, env):
            return success_by_task[self.cur]

    return _FD()


def _install_fakes(monkeypatch, driver, seen):
    env = _FakeEnv()
    monkeypatch.setattr(governed, "open_episode",
                        lambda spec: ("emb", env, {"o": 0}, driver))

    def fake_seg(e, obs, drv, spec, bundle, *, step_budget):
        seen.append((spec.task, bundle))
        return {"obs": {"o": 1}, "steps": 5, "policy_steps": 5, "fires": [],
                "chain": None, "critic_privilege_used": 0,
                "trace": {"observable.finger_gap": np.array([0.04, 0.01])}}

    monkeypatch.setattr(governed, "governed_segment", fake_seg)
    return env


def test_isolated_rollout_prefix_ungoverned_target_governed(monkeypatch):
    seen: list = []
    driver = _fake_episodic_driver({"nav_fridge": True, "grasp_meat": True})
    env = _install_fakes(monkeypatch, driver, seen)
    bundle = Bundle(rules=(), critic_budget=0, action_budget=0)
    spec = EpisodeSpec(seed=1, task="kitchen_thaw",
                       segment_isolate=("nav_fridge", "grasp_meat"))
    out = governed.isolated_segment_rollout(spec, bundle)
    # prefix drives UNGOVERNED (bundle None); only the target segment is governed.
    assert seen == [("nav_fridge", None), ("grasp_meat", bundle)]
    assert out["success"] is True
    assert "observable.finger_gap" in out["trace"]   # the target's trace, for the search
    assert env.closed is True                          # ONE close at mission end


def test_isolated_rollout_scores_only_the_target(monkeypatch):
    seen: list = []
    # nav arrives, grasp fails -> the ISOLATED node is False even though prefix held.
    driver = _fake_episodic_driver({"nav_fridge": True, "grasp_meat": False})
    _install_fakes(monkeypatch, driver, seen)
    spec = EpisodeSpec(seed=2, task="kitchen_thaw",
                       segment_isolate=("nav_fridge", "grasp_meat"))
    out = governed.isolated_segment_rollout(spec, None)
    assert out["success"] is False and len(seen) == 2


def test_isolated_rollout_short_circuits_failed_prefix(monkeypatch):
    seen: list = []
    driver = _fake_episodic_driver({"nav_fridge": False, "grasp_meat": True})
    _install_fakes(monkeypatch, driver, seen)
    spec = EpisodeSpec(seed=3, task="kitchen_thaw",
                       segment_isolate=("nav_fridge", "grasp_meat"))
    out = governed.isolated_segment_rollout(spec, None)
    # a failed precondition segment never runs the target (rare, but scored honestly).
    assert out["success"] is False and seen == [("nav_fridge", None)]


def test_governed_rollout_dispatches_isolated(monkeypatch):
    called = {}
    monkeypatch.setattr(governed, "isolated_segment_rollout",
                        lambda spec, bundle: called.setdefault("hit", (spec, bundle)) or {"ok": 1})
    spec = EpisodeSpec(seed=1, task="kitchen_thaw", segment_isolate=("nav_fridge", "grasp_meat"))
    governed.governed_rollout(spec, None)
    assert "hit" in called
