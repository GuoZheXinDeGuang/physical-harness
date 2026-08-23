"""R2 stage overlay: stages are a measurement, never control.

stages=None must be a bit-identical no-op -- runs/demo and runs/demo-r1 are
the byte anchors, and the first test here is the unit-level pin for that. A
chain adds exactly one "stages" key, scored on a separate scorer view whose
privileged reads are accounted per stage and never touch the critic channel
(history/trace/assert_privilege_budget feed the zero-privilege search).
"""
from __future__ import annotations

import dataclasses as dc

import numpy as np
import pytest

from harness.spec import Clause, EpisodeSpec, StageSpec
from plugins.rsi.governed import Bundle, RecoverySpec, Rule, governed_rollout
from plugins.rsi.stats.search import Trigger

#: A clause no state can fail / no state can satisfy, for deterministic scoring.
ALWAYS = (Clause("observable.finger_gap", "gt", -1.0),)
NEVER = (Clause("observable.finger_gap", "lt", -1.0),)


def _assert_identical(a: dict, b: dict) -> None:
    assert set(a) == set(b)
    for key, value in a.items():
        if key == "trace":
            assert set(value) == set(b[key])
            for f, series in value.items():
                assert np.array_equal(series, b[key][f]), f
        else:
            assert value == b[key], key


# --- the rung's core guard: stages=None is a bit-identical no-op -------------

def test_stages_none_is_a_bit_identical_noop():
    """An unset chain and an explicit None produce the exact same output dict
    on a fixed seed -- same key set (no "stages" key), same trace bytes."""
    unset = governed_rollout(EpisodeSpec(seed=3), None)
    explicit = governed_rollout(EpisodeSpec(seed=3, stages=None), None)
    assert "stages" not in unset
    _assert_identical(unset, explicit)


def test_stage_scoring_is_a_pure_overlay_on_the_base_output():
    """A chain adds ONLY the "stages" key: every pre-existing key, including
    success (= embodiment.success, the gate/search/labels path), is byte-equal
    to the stageless run on the same seed."""
    chain = (StageSpec("grasp", ALWAYS, 62), StageSpec("place", NEVER, 38))
    plain = governed_rollout(EpisodeSpec(seed=3), None)
    staged = governed_rollout(EpisodeSpec(seed=3, stages=chain), None)
    assert set(staged) == set(plain) | {"stages"}
    _assert_identical(plain, {k: v for k, v in staged.items() if k != "stages"})


# --- definitions: identity is recursive asdict, op typos fail loudly ---------

def test_stage_definitions_cannot_hide_a_field_from_the_hash():
    """Ruling: no hand-written canonical(); identity IS recursive asdict via
    Preregistration.sha(), so completeness is structural. Pin that asdict sees
    every declared field, nested clauses included."""
    stage = StageSpec("grasp", (Clause("observable.finger_gap", "gt", 0.01),), 62)
    d = dc.asdict(stage)
    assert set(d) == {f.name for f in dc.fields(StageSpec)}
    assert set(d["success"][0]) == {f.name for f in dc.fields(Clause)}


def test_clause_rejects_an_unknown_operator():
    with pytest.raises(ValueError, match="operator"):
        Clause("observable.finger_gap", "ge", 0.01)


# --- preregistration: the chain is content, threaded into every spec ---------

def _prereg(**kw):
    from plugins.rsi.campaign import Preregistration

    return Preregistration(dev=(0, 1), heldout=(2, 3), percept_noise=0.02,
                           critic_budget=0, action_budget=0,
                           recovery_sensor_sd=0.02, max_generations=1, **kw)


def _chain(threshold=0.01):
    return (StageSpec("grasp", (Clause("observable.finger_gap", "gt", threshold),), 62),)


def test_stage_chain_enters_the_campaign_hash():
    """Changing a clause threshold moves prereg.sha(); equal chains hash equal."""
    assert _prereg(stages=_chain()).sha() != _prereg().sha()
    assert _prereg(stages=_chain(0.01)).sha() != _prereg(stages=_chain(0.02)).sha()
    assert _prereg(stages=_chain(0.01)).sha() == _prereg(stages=_chain(0.01)).sha()


def test_specs_threads_the_stage_chain():
    from plugins.rsi.campaign import _specs

    chain = _chain()
    assert _specs([0], _prereg(stages=chain))[0].stages == chain
    assert _specs([0], _prereg())[0].stages is None


def test_terminal_label_enters_the_campaign_hash():
    """Round 79: swapping the success criterion is a new claim, so it must move
    prereg.sha(); the default (False) stays byte-identical to the legacy hash."""
    assert _prereg(terminal_label=True).sha() != _prereg().sha()
    assert _prereg(terminal_label=False).sha() == _prereg().sha()


def test_specs_threads_the_terminal_label():
    from plugins.rsi.campaign import _specs

    assert _specs([0], _prereg(terminal_label=True))[0].terminal_label is True
    assert _specs([0], _prereg())[0].terminal_label is False


# --- accounting semantics on real rollouts -----------------------------------

def test_stage_accounting_on_the_grasp_only_policy():
    """Exit-at-boundary, exit-at-exact-exhaust, and never-reached, in one
    episode: the scripted lift policy owns exactly 100 schedule steps, so a
    (62, 38, 50) chain exercises all three outcomes deterministically."""
    chain = (StageSpec("grasp", ALWAYS, 62),
             StageSpec("place", NEVER, 38),
             StageSpec("retreat", ALWAYS, 50))
    r = governed_rollout(EpisodeSpec(seed=3, stages=chain), None)
    grasp, place, retreat = r["stages"]
    assert grasp["reached"] and grasp["success"] and grasp["exited_step"] == 62
    assert place["reached"] and not place["success"] and place["exited_step"] == 100
    assert retreat == {"name": "retreat", "entered_step": None, "exited_step": None,
                       "success": False, "reached": False, "privilege_used": 0}


def test_privileged_stage_scoring_never_touches_the_critic_channel():
    """A scorer is not a critic: a privileged stage predicate is accounted in
    the stage's own privilege_used and leaves the trace, the chain digest, and
    the critic budget untouched -- byte-equal to an observable-only chain."""
    obs_chain = (StageSpec("grasp", ALWAYS, 62),)
    priv_chain = (StageSpec("grasp", (Clause("privileged.object_z", "gt", 0.0),), 62),)
    a = governed_rollout(EpisodeSpec(seed=3, stages=obs_chain), None)
    b = governed_rollout(EpisodeSpec(seed=3, stages=priv_chain), None)
    assert a["critic_privilege_used"] == b["critic_privilege_used"] == 0
    assert not any(name.startswith("privileged.") for name in b["trace"])
    _assert_identical({k: v for k, v in a.items() if k != "stages"},
                      {k: v for k, v in b.items() if k != "stages"})
    assert a["stages"][0]["privilege_used"] == 0
    assert b["stages"][0]["privilege_used"] == 1


def test_handback_jump_scores_every_crossed_stage():
    """on_handback supersedes the interrupted phase, so the schedule clock can
    jump several cumulative budgets in one hop; each crossed stage must be
    scored (on the post-recovery obs), not just the first.

    Trace: the rule fires at policy step 30 (driver clock 31, mid-descend);
    hand-back supersedes to 50, so the next policy-owned step (k=32, driver
    clock 51) crosses BOTH the 40 and 45 boundaries at once."""
    always = Rule("g1", Trigger("observable.eef_z", "gt", -1e9, 1, 30),
                  RecoverySpec(sensor_sd=0.02))
    chain = (StageSpec("s1", ALWAYS, 40), StageSpec("s2", ALWAYS, 5),
             StageSpec("s3", ALWAYS, 55))
    r = governed_rollout(EpisodeSpec(seed=7, stages=chain),
                         Bundle(rules=(always,), critic_budget=0, action_budget=0))
    assert r["fires"] and r["fires"][0]["step"] == 30
    s1, s2, s3 = r["stages"]
    assert s1["reached"] and s2["reached"] and s3["reached"]
    assert s1["exited_step"] == s2["exited_step"] == 32
    # recovery steps do not advance k, so s3's span is the remaining 49
    # schedule steps (driver clock 51..100): exit at k = 32 + 49.
    assert s3["exited_step"] == 81


# --- R2 rung C: the stack scoring pieces -------------------------------------

def test_stack_residual_features_are_registered_relational_privileged():
    """The relation lives in the FEATURE (grasp_error precedent): both
    residuals are cubeA-relative-to-cubeB, declared in the privileged
    namespace, with the 0.045 seat literal being cube geometry, not a scene
    fact like table height."""
    from harness.features import REGISTRY, Privilege  # base catalog populates on import (R2)

    for name in ("privileged.stack_xy_residual", "privileged.stack_z_residual"):
        assert name in REGISTRY and REGISTRY[name].privilege is Privilege.PRIVILEGED
    obs = {"cubeA_pos": np.array([0.10, 0.00, 0.865]),
           "cubeB_pos": np.array([0.10, 0.03, 0.825])}
    assert REGISTRY["privileged.stack_xy_residual"].extract(obs) == pytest.approx(0.03)
    assert REGISTRY["privileged.stack_z_residual"].extract(obs) == pytest.approx(
        0.865 - (0.825 + 0.045))


def test_stack_residuals_degrade_to_nan_off_the_stack_scene():
    """project() is EAGER: a budget-1 scorer view on a LIFT obs extracts every
    registered privileged feature, so off-scene residuals must degrade to NaN,
    never raise -- otherwise registering them would break every lift-task
    stage overlay above. NaN also fails both lt and gt, so an off-scene
    clause is honestly false."""
    import math

    from harness.percept import PrivilegePolicy, project

    lift_obs = {
        "robot0_gripper_qpos": np.array([0.02, -0.02]),
        "robot0_eef_pos": np.array([0.0, 0.0, 1.0]),
        "robot0_joint_vel": np.zeros(7),
        "robot0_gripper_qvel": np.zeros(2),
        "cube_pos": np.array([0.0, 0.0, 0.83]),
    }
    v = project(lift_obs, PrivilegePolicy(critic_budget=1).critic_names(), step=0, episode="e")
    assert math.isnan(v["privileged.stack_xy_residual"])
    assert math.isnan(v["privileged.stack_z_residual"])
    chain = (StageSpec("place", (Clause("privileged.stack_xy_residual", "lt", 0.025),), 1),)
    assert not chain[0].holds(v)
    chain = (StageSpec("place", (Clause("privileged.stack_xy_residual", "gt", 0.025),), 1),)
    assert not chain[0].holds(v)


def test_stack_stages_chain_structure_is_frozen():
    """Frozen-values guard for the calibration chain: budgets come from
    STACK_SCHEDULE's cumulative halves and the clause thresholds are the
    authored tolerance literals -- a typo here would surface only as a silent
    attribution shift mid-campaign."""
    from harness.spec import STACK_SCHEDULE
    from plugins.embodiment_robosuite.env import stack_stages

    grasp, place = stack_stages()
    assert grasp.budget == 92 and place.budget == 85          # 25+25+12+30 / 25+25+10+25
    assert grasp.budget + place.budget == sum(d for _, d in STACK_SCHEDULE)
    assert dc.asdict(grasp) == {"name": "grasp", "budget": 92, "success": (
        {"feature": "observable.finger_gap", "op": "gt", "threshold": 0.01},
        {"feature": "privileged.stack_z_residual", "op": "gt", "threshold": 0.04},
    )}
    assert dc.asdict(place) == {"name": "place", "budget": 85, "success": (
        {"feature": "privileged.stack_xy_residual", "op": "lt", "threshold": 0.025},
        {"feature": "privileged.stack_z_residual", "op": "lt", "threshold": 0.02},
        {"feature": "observable.finger_gap", "op": "gt", "threshold": 0.05},
    )}


def test_terminal_success_is_an_optional_contract_extension():
    """Ruling 4: the gate only ever consumes the full-task terminal boolean.
    Stack reaches the simulator's own predicate through the env handle and
    fails LOUDLY without one; lift falls back to the shared sub-goal; and a
    provider without the method still satisfies EnvProvider -- the extension
    must not force a future Isaac embodiment."""
    from harness.contracts import EnvProvider
    from harness.registry import load_provider

    p = load_provider("plugins.embodiment_robosuite:provider")
    lift_obs = {"cube_pos": np.array([0.0, 0.0, 0.90]),
                "robot0_gripper_qpos": np.array([0.02, -0.02])}
    spec = EpisodeSpec(seed=0)
    assert p.terminal_success(lift_obs, spec, 0.82) is p.success(lift_obs, spec, 0.82) is True

    class _FakeEnv:
        def _check_success(self):
            return True

    stack_spec = EpisodeSpec(seed=0, task="stack")
    assert p.terminal_success({}, stack_spec, 0.0, env=_FakeEnv()) is True
    with pytest.raises(ValueError, match="env"):
        p.terminal_success({}, stack_spec, 0.0)

    class _Minimal:
        def make_env(self, spec): ...
        def tasks(self): return ("lift",)
        def object_key(self, spec): return "cube_pos"
        def success(self, obs, spec, start_z): return False

    assert isinstance(_Minimal(), EnvProvider)


def _fake_embodiment(*, has_terminal: bool):
    """A real Lift env under the hood (so the rollout runs), but success() and
    terminal_success() return DIFFERENT constants so the branch selection is
    observable in result['success']. terminal_success is present only when
    has_terminal, so the missing-method path is reachable too."""
    from harness.registry import load_provider

    real = load_provider("plugins.embodiment_robosuite:provider")

    class _Fake:
        def make_env(self, spec):
            return real.make_env(spec)

        def object_key(self, spec):
            return real.object_key(spec)

        def success(self, obs, spec, start_z):
            return False

    fake = _Fake()
    if has_terminal:
        fake.terminal_success = lambda obs, spec, start_z, env=None: True
    return fake


def test_terminal_label_selects_the_terminal_predicate(monkeypatch):
    """Round 79: terminal_label=True reads embodiment.terminal_success (here
    True) rather than embodiment.success (here False); =False reads success
    bit-for-bit; True with no terminal_success on the embodiment fails loud."""
    from plugins.rsi import governed

    monkeypatch.setattr(governed, "_embodiment",
                        lambda spec: _fake_embodiment(has_terminal=True))
    on = governed_rollout(EpisodeSpec(seed=3, terminal_label=True), None)
    assert on["success"] is True                       # terminal, not success's False
    off = governed_rollout(EpisodeSpec(seed=3, terminal_label=False), None)
    assert off["success"] is False                     # the original success() path

    monkeypatch.setattr(governed, "_embodiment",
                        lambda spec: _fake_embodiment(has_terminal=False))
    with pytest.raises(ValueError, match="terminal_success"):
        governed_rollout(EpisodeSpec(seed=3, terminal_label=True), None)


def test_terminal_label_default_leaves_the_lift_success_untouched():
    """Red line: the switch defaults False, so a plain lift spec still reads
    embodiment.success. And because lift's terminal_success FALLS BACK to that
    same sub-goal, flipping the label on lift cannot move result['success']
    either -- only stack, whose terminal predicate differs, can."""
    off = governed_rollout(EpisodeSpec(seed=3), None)
    on = governed_rollout(EpisodeSpec(seed=3, terminal_label=True,
                                      env_provider="plugins.embodiment_robosuite:provider"), None)
    assert off["success"] == on["success"]


def test_calibrate_stack_script_help_smoke():
    """The real sweeps belong to the orchestration layer; CI only proves the
    script parses its CLI (pass/height/sd/N all reachable)."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "scripts/calibrate_stack.py", "--help"],
                       capture_output=True, text=True, cwd=root, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    for flag in ("--pass", "--heights", "--sd", "--sds", "--n", "--first-seed"):
        assert flag in r.stdout
