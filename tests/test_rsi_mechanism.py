"""The generic RSI chain's three JUDGEMENT points, pinned.

Everything else in scripts/rsi_campaign.py is plumbing that a real run exercises;
these three decide things, and a silent regression in any of them would be
invisible until it had already burned a block:

* ``allocate`` -- which seeds get claimed (an off-by-one here reuses a burned block)
* ``attribute`` -- which node the campaign will target (the caller must not choose)
* ``gate`` -- whether anything runs at all (the honest-NO-GO path)

All pure functions over dicts: no sim, no seeds burned, base lane.
"""

from __future__ import annotations

import pytest

from plugins.rsi import repertoire
from scripts.rsi_campaign import (
    CAL_N,
    DEV_N,
    HELDOUT_N,
    SEED_CEILING,
    allocate,
    attribute,
    gate,
    seeds,
)


def _ledger(*ranges):  # board.store.burned_blocks rows: (lo, hi, role, prereg_sha)
    return [(lo, hi, "gate", "deadbeef" * 8) for lo, hi, _state in ranges]


# ── a. allocation ────────────────────────────────────────────────────────────

def test_allocate_splits_one_contiguous_block_disjointly():
    blocks = allocate(_ledger((0, 999, "burned")))
    assert blocks["cal"] == (1000, 1000 + CAL_N - 1)
    assert blocks["dev"] == (1000 + CAL_N, 1000 + CAL_N + DEV_N - 1)
    assert blocks["heldout"][1] - blocks["heldout"][0] + 1 == HELDOUT_N
    # disjoint AND contiguous: the whole claim is one interval, no gaps to leak
    used = sorted(sum((seeds(b) for b in blocks.values()), []))
    assert used == list(range(1000, 1000 + CAL_N + DEV_N + HELDOUT_N))


def test_allocate_steps_over_burned_ranges():
    # a burned range sitting inside the naive first fit must push the claim past it
    blocks = allocate(_ledger((0, 99, "burned"), (200, 5000, "burned")))
    assert blocks["cal"][0] == 5001


def test_allocate_refuses_a_pinned_block_that_hits_burned():
    with pytest.raises(ValueError, match="hits burned"):
        allocate(_ledger((0, 999, "burned")), cal=(5000, 5149), dev=(900, 1199), heldout=(1200, 1399))


def test_allocate_honours_a_pinned_calibration_block():
    """Calibration never gates, so re-measuring an old block is legal and normal;
    pinning it must not disturb the dev/held-out claim."""
    auto = allocate(_ledger((0, 999, "burned")))
    pinned = allocate(_ledger((0, 999, "burned")), cal=(40000, 40149))
    assert pinned["cal"] == (40000, 40149)
    assert pinned["dev"] == auto["dev"] and pinned["heldout"] == auto["heldout"]


def test_allocate_refuses_pinned_blocks_that_overlap():
    """Pinning bypasses the contiguous split, so disjointness is re-asserted at
    claim time. Preregistration catches a dev/held-out overlap too, but only
    after the calibration set has already been paid for -- and it never sees the
    calibration block."""
    with pytest.raises(ValueError, match="overlaps"):
        allocate(_ledger((0, 999, "burned")), dev=(2000, 2299), heldout=(2200, 2399))
    with pytest.raises(ValueError, match="overlaps"):
        allocate(_ledger((0, 999, "burned")), cal=(2000, 2149), dev=(2100, 2399))


def test_allocate_refuses_past_the_seed_overflow_ceiling():
    """spec.seed*7919+11 overflows int32 above SEED_CEILING; a block handed back
    from up there would crash on its first episode, so refuse at claim time."""
    with pytest.raises(ValueError, match="overflow"):
        allocate(_ledger((0, SEED_CEILING - 10, "burned")))


# ── b/c. first-death attribution ─────────────────────────────────────────────

_GRAPH = [
    {"id": "survey", "skill": "survey", "kind": "perceive", "after": [], "args": {}},
    {"id": "grasp-cube", "skill": "grasp", "kind": "manipulate",
     "after": ["survey"], "args": {}},
    {"id": "verify-grasp", "skill": "verify_grasp", "kind": "verify",
     "after": ["grasp-cube"], "args": {}},
    {"id": "build-stack", "skill": "stack", "kind": "manipulate",
     "after": ["verify-grasp"], "args": {}},
]


def _cal(first_death: dict, *, n=150, successes=60, per_ep=3.0, budget_exhaust=0):
    return {"task": "t", "n": n, "successes": successes,
            "base_rate": successes / n, "graph": _GRAPH,
            "first_death_by_node": first_death, "budget_exhaust": budget_exhaust,
            "seconds_total": n * per_ep, "seconds_per_episode": per_ep,
            "episodes": []}


def test_verify_deaths_are_charged_back_to_the_node_they_verify():
    """A verify node has nothing of its own to govern -- it is the oracle that
    caught the preceding sub-goal dropping what it claimed. Its deaths belong to
    that sub-goal, which is where a recovery would fire."""
    att = attribute(_cal({"grasp-cube": 10, "verify-grasp": 25, "none": 60}))
    assert att["governable"] == {"grasp-cube": 35}
    assert att["target"] == "grasp-cube"


def test_perceive_and_decide_deaths_are_charged_to_nobody():
    """The M6 c3 attribution pivot: a chain dying at an ungoverned node is not an
    RSI problem, and must not be laundered onto a downstream governable node."""
    att = attribute(_cal({"survey": 40, "grasp-cube": 5, "none": 105}))
    assert att["ungoverned"] == {"survey": 40}
    assert att["governable_deaths"] == 5


def test_target_is_the_most_deadly_governable_node_not_the_first():
    att = attribute(_cal({"grasp-cube": 5, "build-stack": 30, "none": 115}))
    assert att["target"] == "build-stack"
    assert att["ranked"][0] == ("build-stack", 30)


def test_none_is_never_a_death():
    att = attribute(_cal({"none": 150}))
    assert att["target"] is None and att["governable_deaths"] == 0


# ── c. the gate verdict ──────────────────────────────────────────────────────

_OK_SUPPORT = {"supported": True, "reason": "driver ok",
               "repertoire": repertoire.strategies_for("embodiment_robosuite")}


def _verdict(cal, support=_OK_SUPPORT):
    return gate(cal, attribute(cal), support, workers=10)


def test_gate_passes_a_healthy_calibration():
    v = _verdict(_cal({"grasp-cube": 60, "none": 90}, successes=90))
    assert v["proceed"] and v["failed"] == [] and v["target_node"] == "grasp-cube"


@pytest.mark.parametrize("successes,criterion", [(0, "c1_base_degenerate"),
                                                 (150, "c1_base_degenerate"),
                                                 (140, "c2_base_ceiling")])
def test_gate_refuses_a_degenerate_or_ceilinged_base_rate(successes, criterion):
    """0%/100% has no residual to learn from; >=0.90 is an honest null. Either
    way not one dev seed is worth burning."""
    deaths = {"grasp-cube": 150 - successes} if successes < 150 else {}
    v = _verdict(_cal({**deaths, "none": successes}, successes=successes))
    assert not v["proceed"] and criterion in v["failed"]
    assert v["target_node"] is None


def test_gate_refuses_when_budget_exhaustion_dominates_the_failures():
    """A mission dying on its own budget measures the budget, not the policy --
    the fix is config, never RSI."""
    v = _verdict(_cal({"grasp-cube": 90, "none": 60}, successes=60,
                      budget_exhaust=50))
    assert not v["proceed"] and "c3_budget_exhaust_dominant" in v["failed"]


def test_gate_refuses_when_the_chain_mostly_dies_ungoverned():
    v = _verdict(_cal({"survey": 60, "grasp-cube": 30, "none": 60}, successes=60))
    assert not v["proceed"] and "c4_attribution" in v["failed"]


def test_gate_refuses_when_the_embodiment_registers_no_recovery_primitive():
    """The honest boundary. An embodiment with nothing in the repertoire gets
    told so verbatim -- a tabletop program is never substituted for it."""
    none_registered = {"supported": False, "repertoire": [],
                       "reason": "该本体（卡 embodiment_robocasa）无注册恢复原语"}
    v = _verdict(_cal({"grasp-cube": 90, "none": 60}, successes=60), none_registered)
    assert not v["proceed"] and "c5_recovery_primitive" in v["failed"]
    assert "无注册恢复原语" in " ".join(v["missing_capability"])


def test_gate_refuses_a_calibration_plus_one_generation_over_the_time_budget():
    v = _verdict(_cal({"grasp-cube": 90, "none": 60}, successes=60, per_ep=600.0))
    assert not v["proceed"] and "c6_wall_clock" in v["failed"]


def test_a_no_go_verdict_names_the_missing_capability():
    """A NO-GO is a finished result, not an error: it must READ as one."""
    v = _verdict(_cal({"none": 150}, successes=150))
    assert v["missing_capability"] and all(isinstance(m, str)
                                           for m in v["missing_capability"])


# ── the repertoire declaration the boundary rests on ─────────────────────────

def test_every_repertoire_strategy_is_declared_by_an_embodiment_card():
    declared = (repertoire.strategies_for("embodiment_robosuite")
            + repertoire.strategies_for("embodiment_robocasa"))
    assert sorted(declared) == sorted(repertoire.names())
    # the kitchen card declares its own two, folded even though it is
    # enabled = false (a second-simulator card permanently is)
    assert repertoire.strategies_for("embodiment_robocasa") == [
        "regrasp_kitchen", "redock_retry", "reapproach", "base_nudge", "release_reset"]


def test_an_undeclaring_card_has_no_recovery_primitives():
    """The whole point: absence is reportable, not fillable. Both installed
    embodiment cards now declare their own, so a card that declares none at all
    is the probe -- and it gets [], never another card's shapes."""
    assert repertoire.strategies_for("embodiment_nonexistent") == []
    assert repertoire.strategies_for("embodiment_libero") == []


def test_every_folded_strategy_satisfies_the_contract():
    from harness.contracts import RecoveryStrategy

    assert repertoire.names(), "the fold must surface the robosuite repertoire"
    for name in repertoire.names():
        assert isinstance(repertoire.strategy(name), RecoveryStrategy)


# ── the per-episode wall cap: a hung probe returns an honest row ─────────────


def test_hung_probe_is_capped_and_returns_a_wall_timeout_row(monkeypatch):
    import time as _time

    import scripts.rsi_campaign as rc

    monkeypatch.setattr(rc, "EPISODE_WALL_S", 1)
    monkeypatch.setattr(rc, "_probe_one_uncapped",
                        lambda *a: _time.sleep(30) or {})
    t0 = _time.perf_counter()
    row = rc._probe_one(("kitchen_thaw", 7, 3, 40))
    assert _time.perf_counter() - t0 < 5
    assert row["success"] is False and row["wall_timeout"] is True
    assert row["first_death"] == "wall_timeout" and row["seed"] == 7


def test_wall_timeout_deaths_are_ungoverned_never_the_target():
    from scripts.rsi_campaign import attribute

    cal = {"graph": [{"id": "a", "kind": "segment", "after": []}],
           "first_death_by_node": {"a": 3, "wall_timeout": 5, "worker_died": 2}}
    att = attribute(cal)
    assert att["target"] == "a"
    assert att["ungoverned"] == {"wall_timeout": 5, "worker_died": 2}


def test_calibrate_survives_a_worker_no_signal_can_reach(monkeypatch):
    """The 2026-08-28 loss, reproduced small: a probe wedged where its own
    SIGALRM cannot run. The PARENT's hard cap ends it and the block still folds
    -- and the summary's node kinds come from a surviving episode, not from
    whichever seed happened to sort first."""
    import os
    import signal as _signal
    import time as _time

    import scripts.rsi_campaign as rc

    graph = [{"id": "a", "kind": "manipulate", "after": [], "skill": "s", "args": {}}]

    def wedged(task, seed, *_a):
        if seed == 100:                      # the lowest seed is the wedged one
            os.kill(os.getpid(), _signal.SIGSTOP)
        return {"seed": seed, "success": False, "first_death": "a", "graph": graph,
                "node_ok": {"a": False}, "node_stages": {}, "replans": 0,
                "actuations": 1, "budget_exhaust": False, "seconds": 0.1}

    monkeypatch.setattr(rc, "EPISODE_HARD_WALL_S", 2)
    monkeypatch.setattr(rc, "_probe_one_uncapped", wedged)
    t0 = _time.perf_counter()
    cal = rc.calibrate("kitchen_thaw", (100, 103), workers=4)
    assert _time.perf_counter() - t0 < 30

    assert cal["n"] == 4
    assert cal["first_death_by_node"] == {"a": 3, "wall_timeout": 1}
    assert cal["graph"] == graph, "an empty timeout graph must not blank the table"
    assert rc.attribute(cal)["target"] == "a"


def test_frames_arm_is_single_writer_and_opt_in(monkeypatch, tmp_path):
    import scripts.rsi_campaign as rc

    monkeypatch.setattr(rc, "_FRAMES", False)
    monkeypatch.delenv("PH_RSI_FRAMES", raising=False)
    assert rc._maybe_arm_frames() is False          # opt-in: no env, no overlay

    dest = tmp_path / "frame.jpg"
    monkeypatch.setenv("PH_RSI_FRAMES", str(dest))
    assert rc._maybe_arm_frames() is True           # first caller wins the lock
    assert rc._maybe_arm_frames() is True           # idempotent in the winner

    monkeypatch.setattr(rc, "_FRAMES", False)       # a SECOND worker, same pid
    lock = dest.with_suffix(".lock")
    lock.write_text("999999999")                    # lock held by a dead pid
    assert rc._maybe_arm_frames() is True           # stale lock is stolen
    assert lock.read_text().strip() != "999999999"
