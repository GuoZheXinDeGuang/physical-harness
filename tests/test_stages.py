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
