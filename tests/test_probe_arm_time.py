"""Offline unit tests for scripts/probe_arm_time.py -- no simulation.

Two things are pinned. (1) The candidate arm set the search enumerates for a
feature is {eod, eod+2, eod+6} anchored at the divergence ONSET, so on traces
that diverge at t=10 and peak at t=50 the set excludes 50 -- that structural gap
is the whole reason the probe exists. (2) score_trigger reproduces the score
search_triggers assigned to a candidate it enumerated itself, so scoring the
sealed rules under the objective is the same arithmetic the search applied.

The end-to-end run_probe is exercised with gate._run monkeypatched (the
test_round25_rerun trick) over a fake round25_rerun-shaped store, so the
rule reconstruction, the P1/P2 glue, the sealed-anchor guard and the artifact
shape all run without a rollout.
"""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

import plugins.embodiment_robosuite.features  # noqa: F401  registry population
from plugins.rsi import gate
from plugins.rsi.campaign import CampaignStore, Preregistration, sha_json
from plugins.rsi.governed import RecoverySpec, Rule
from plugins.rsi.stats.search import Trigger, search_triggers
from scripts import probe_arm_time

_N = 80


def _finger_gap(seed: int) -> np.ndarray:
    """Failing (even) episodes peel finger_gap away from 0.04 starting at t=10,
    with the biggest gap at t=50; succeeding (odd) ones hold 0.04 throughout."""
    fg = np.full(_N, 0.04)
    if seed % 2 == 0:
        for t in range(10, _N):
            diff = 0.03 * (t - 9) / 41 if t <= 50 else 0.03 * (_N - t) / 30
            fg[t] = 0.04 - diff
    return fg


def _trace(seed: int) -> dict[str, np.ndarray]:
    return {
        "observable.finger_gap": _finger_gap(seed),
        "observable.eef_z": np.linspace(1.0, 0.9, _N),
        "observable.joint_speed": np.zeros(_N),
        "observable.gripper_effort": np.zeros(_N),
    }


def _traces_and_labels(seeds):
    traces = [_trace(s) for s in seeds]
    labels = [s % 2 == 1 for s in seeds]  # even seeds fail
    return traces, labels


# --- (1) the arm set excludes the sigma peak --------------------------------

def test_candidate_arm_set_excludes_the_peak():
    traces, labels = _traces_and_labels(range(43000, 43020))
    eod, arms = probe_arm_time.arm_candidates(traces, labels, "observable.finger_gap", "value")
    assert eod == 10               # divergence ONSET, not the peak
    assert arms == (10, 12, 16)    # {eod, eod+2, eod+6}
    assert 50 not in arms          # the peak the naive picker would arm at is unreachable


# --- (2) score_trigger reproduces an enumerated candidate's score -----------

def test_score_trigger_reproduces_the_enumerated_score():
    traces, labels = _traces_and_labels(range(43000, 43020))
    ranked = search_triggers(traces, labels, privilege_budget=0, top_k=8)
    assert ranked
    top = ranked[0]
    got = probe_arm_time.score_trigger(top.trigger, traces, labels,
                                       fp_penalty=1.2, earliness=0.0)
    assert got["score"] == top.score
    assert got["recall"] == top.recall
    assert got["false_positive"] == top.false_positive


def test_tie_count_at_top_counts_shared_top_score():
    traces, labels = _traces_and_labels(range(43000, 43020))
    ranked = search_triggers(traces, labels, privilege_budget=0, top_k=50)
    ties, top = probe_arm_time.tie_count_at_top(ranked)
    assert top == ranked[0].score
    assert ties == sum(1 for s in ranked if s.score == top)
    assert ties >= 1


# --- end-to-end run_probe, no rollouts --------------------------------------

class _FakeExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


def _fake_run(job):
    """Even seeds fail ungoverned; any bundle with rules fixes them. Dev traces
    carry the diverging finger_gap so P1's search + arm scan have real signal."""
    spec, bundle = job
    failing = spec.seed % 2 == 0
    governed = bundle is not None and bool(bundle.rules)
    success = (not failing) or governed
    return {"success": success, "fired_at": 0 if governed else None,
            "trace": _trace(spec.seed)}


def _recovery() -> RecoverySpec:
    return RecoverySpec(program=(("descend", 10), ("lift", 40)), sensor_sd=0.02)


def _fake_store(root, *, search_fixed: int):
    """A round25_rerun-shaped store: real prereg, real rule canonicals. The naive
    rule uses reducer 'value' (arm scan needs a reducer that diverges on these
    traces) and arms at 95, which the search cannot reach."""
    prereg = Preregistration(
        dev=tuple(range(43000, 43020)), heldout=tuple(range(43200, 43210)),
        percept_noise=0.02, critic_budget=0, action_budget=0,
        recovery_sensor_sd=0.02, max_generations=1, task="lift", policy="scripted")
    search_rule = Rule("g1", Trigger("observable.finger_gap", "lt", 0.02, 1, 43, "value"),
                       _recovery())
    naive_rule = Rule("g1", Trigger("observable.finger_gap", "lt", 0.005, 1, 95, "value"),
                      _recovery())
    store = CampaignStore(root)
    store.put("preregistration", dataclasses.asdict(prereg))
    # The anchor guard now checks the WHOLE discordant signature (fixed, broken,
    # fires): _fake_run fires on every governed episode (10) and breaks none.
    store.put("round25_rerun", {"arena": "lift", "arms": {
        "search": {"rule": search_rule.canonical(),
                   "heldout": {"fixed": search_fixed, "broken": 0, "fires": 10}},
        "naive_mock": {"rule": naive_rule.canonical(), "heldout": {"fixed": 5}}}})
    return prereg


def test_run_probe_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    # 5 even seeds in [43200, 43210): the governed arm fixes exactly those.
    prereg = _fake_store(tmp_path / "sealed", search_fixed=5)

    out = probe_arm_time.run_probe(tmp_path / "sealed", tmp_path / "probe",
                                   workers=1, verbose=False, executor=_FakeExecutor())

    assert out["grade"] == "diagnostic"
    assert out["source_preregistration_sha"] == sha_json(dataclasses.asdict(prereg))
    # P1: the peak-armed naive rule is unreachable, and 0.005 is below the grid.
    assert out["p1"]["arm_set"]["earliest_divergence"] == 10
    assert out["p1"]["arm_set"]["naive_arm"] == 95
    assert out["p1"]["arm_set"]["naive_arm_reachable"] is False
    assert out["p1"]["grid_check"]["in_grid"] is False
    assert out["p1"]["tie_count_at_top"] >= 1
    assert set(out["p1"]["rule_scores"]) == {"search", "naive"}
    # P2: four arms, the sealed anchor reproduces fixed=5.
    assert set(out["p2"]["arms"]) == {"search_arm43", "search_arm70",
                                      "search_arm95", "naive_arm43"}
    assert out["p2"]["arms"]["search_arm43"]["fixed"] == 5
    # Sealed content-addressed, one artifact, kind arm_time_probe.
    index = [json.loads(line) for line in (tmp_path / "probe" / "index.jsonl").open()]
    assert [row["kind"] for row in index] == ["arm_time_probe"]
    stored = json.loads(
        (tmp_path / "probe" / "artifacts" / f"{index[0]['sha']}.json").read_text())
    assert stored == json.loads(json.dumps(out))
    assert index[0]["sha"] == sha_json(out)


def test_run_probe_stops_on_anchor_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    _fake_store(tmp_path / "sealed", search_fixed=999)  # the fake fixes only 5
    with pytest.raises(SystemExit, match="anchor drift"):
        probe_arm_time.run_probe(tmp_path / "sealed", tmp_path / "probe",
                                 workers=1, verbose=False, executor=_FakeExecutor())
    # Stopped before writing: no artifact store on disk.
    assert not (tmp_path / "probe" / "index.jsonl").exists()
