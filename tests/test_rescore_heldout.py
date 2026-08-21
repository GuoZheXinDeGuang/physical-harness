"""Offline end-to-end of scripts/rescore_heldout.py -- no simulation.

gate._run is monkeypatched with a seed-deterministic fake (the
test_round25_rerun trick) and the executor is serial, so BOTH paired gates --
vs the ungoverned baseline and vs the whole-chain blind twin -- run through
the real ``paired_gate`` / ``blind_twin`` code over synthetic rollouts. The
sealed-store fixture is built with the real Preregistration/Rule/Bundle
types, so the child_sha chain the script asserts against is a REAL content
hash, not a stub.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

import plugins.embodiment_robosuite.features  # noqa: F401  registry population
from plugins.rsi import gate
from plugins.rsi.campaign import CampaignStore, Preregistration, sha_json
from plugins.rsi.governed import Bundle, RecoverySpec, Rule
from plugins.rsi.stats.search import Trigger
from scripts import parity_check, rescore_heldout

REPO_ROOT = Path(__file__).resolve().parent.parent

_GATE = {"n": 10, "base_rate": 0.5, "governed_rate": 0.9, "fixed": 4, "broken": 0,
         "fires": 4, "p_value": 0.01, "declared_privilege": 0}


class _FakeExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


def _fake_run(job):
    """Seed-deterministic stand-in for governed_rollout: even seeds fail
    ungoverned; the real bundle fixes every failure; the blind twin fixes
    nothing and additionally breaks seeds % 4 == 1, so the head-to-head
    judgement comparison separates cleanly."""
    spec, bundle = job
    failing = spec.seed % 2 == 0
    if bundle is None or not bundle.rules:
        success = not failing
    elif all(r.rule_id.endswith("-blind") for r in bundle.rules):
        success = (not failing) and spec.seed % 4 != 1
    else:
        success = True
    return {"success": success,
            "fired_at": 0 if bundle is not None and bundle.rules else None,
            "trace": {}}


def _rule(rule_id: str, arm_after: int) -> Rule:
    return Rule(rule_id, Trigger("observable.finger_gap", "lt", 0.0018, 1, arm_after, "value"),
                RecoverySpec(program=(("descend", 10), ("lift", 40)), sensor_sd=0.02))


def _sealed_store(root: Path, *, require_judgement: bool = True,
                  corrupt_child_sha: bool = False, promote_first: bool = True):
    """A run_campaign-shaped store: real prereg, real rule canonicals, real
    sha chain; one promoted generation and one rejected one."""
    prereg = Preregistration(
        dev=tuple(range(41000, 41020)), heldout=tuple(range(42000, 42010)),
        percept_noise=0.02, critic_budget=0, action_budget=0,
        recovery_sensor_sd=0.02, max_generations=2, task="lift", policy="scripted",
        require_judgement=require_judgement)
    parent = Bundle(rules=(), critic_budget=0, action_budget=0)
    child = parent.append(_rule("g1", 41))
    store = CampaignStore(root)
    prereg_sha = store.put("preregistration", dataclasses.asdict(prereg))
    store.put("generation", {
        "preregistration_sha": prereg_sha, "generation": 1, "rule": _rule("g1", 41).canonical(),
        "parent_sha": parent.sha(),
        "child_sha": "0" * 64 if corrupt_child_sha else child.sha(),
        "dev_gate": _GATE, "blind_gate": _GATE, "promoted": promote_first,
        "reason": "promoted" if promote_first else "rejected"})
    store.put("generation", {
        "preregistration_sha": prereg_sha, "generation": 2, "rule": _rule("g2", 58).canonical(),
        "parent_sha": child.sha(), "child_sha": child.append(_rule("g2", 58)).sha(),
        "dev_gate": _GATE, "blind_gate": _GATE, "promoted": False, "reason": "rejected"})
    store.put("campaign_result", {
        "preregistration_sha": prereg_sha, "generations": 2,
        "promoted": 1 if promote_first else 0,
        "final_sha": child.sha() if promote_first else parent.sha(),
        "rules": ["g1"] if promote_first else []})
    return prereg, child


def test_end_to_end_artifact_and_child_sha_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    prereg, child = _sealed_store(tmp_path / "sealed")

    out = rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 43000,
                                      workers=1, verbose=False, executor=_FakeExecutor())

    # The rebuilt bundle is the promoted chain only: g1, not the rejected g2.
    assert out["bundle_sha"] == child.sha()
    assert out["source_preregistration_sha"] == sha_json(dataclasses.asdict(prereg))
    assert out["source_store"] == str(tmp_path / "sealed")
    assert out["block"] == 43000
    assert out["n"] == len(prereg.heldout)
    # Even seeds (5 of 10) fail ungoverned; the governed bundle fixes them all.
    assert out["paired"]["n"] == 10
    assert out["paired"]["base_rate"] == 0.5
    assert out["paired"]["governed_rate"] == 1.0
    assert out["paired"]["fixed"] == 5 and out["paired"]["broken"] == 0
    # The blind twin also drops seeds % 4 == 1 (three odd seeds in the block).
    assert out["vs_blind"]["base_rate"] == 0.2
    assert out["vs_blind"]["fixed"] == 8 and out["vs_blind"]["broken"] == 0
    assert out["vs_blind"]["p_value"] == pytest.approx(2 / 2**8)
    assert out["judgement"] is True

    # The artifact landed in the new store, content-addressed, kind heldout_rescore.
    index = [json.loads(line) for line in (tmp_path / "rescore" / "index.jsonl").open()]
    assert [row["kind"] for row in index] == ["heldout_rescore"]
    stored = json.loads(
        (tmp_path / "rescore" / "artifacts" / f"{index[0]['sha']}.json").read_text())
    assert stored == json.loads(json.dumps(out))  # what came back is what was sealed
    assert index[0]["sha"] == sha_json(out)


def test_judgement_skipped_when_not_preregistered(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    _sealed_store(tmp_path / "sealed", require_judgement=False)

    out = rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 43000,
                                      workers=1, verbose=False, executor=_FakeExecutor())

    assert out["vs_blind"] is None
    assert out["judgement"] is None
    assert out["paired"]["fixed"] == 5


def test_explicit_n_overrides_heldout_length(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    _sealed_store(tmp_path / "sealed")

    out = rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 43000,
                                      n=4, workers=1, verbose=False, executor=_FakeExecutor())

    assert out["n"] == 4 and out["paired"]["n"] == 4


def test_corrupt_child_sha_is_rejected_before_any_rollout(tmp_path):
    # gate._run is NOT patched: the reconstruction must fail before rollouts.
    _sealed_store(tmp_path / "sealed", corrupt_child_sha=True)
    with pytest.raises(AssertionError, match="does not reproduce the sealed bundle"):
        rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 43000,
                                    workers=1, verbose=False)


def test_no_promoted_generations_is_rejected(tmp_path):
    _sealed_store(tmp_path / "sealed", promote_first=False)
    with pytest.raises(ValueError, match="nothing to rescore"):
        rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 43000,
                                    workers=1, verbose=False)


def test_existing_out_store_is_rejected(tmp_path):
    _sealed_store(tmp_path / "sealed")
    (tmp_path / "rescore").mkdir()
    with pytest.raises(FileExistsError):
        rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 43000,
                                    workers=1, verbose=False)


def test_block_overlapping_campaign_seeds_is_rejected(tmp_path):
    _sealed_store(tmp_path / "sealed")
    with pytest.raises(ValueError, match="must be fresh"):
        rescore_heldout.run_rescore(tmp_path / "sealed", tmp_path / "rescore", 41000,
                                    workers=1, verbose=False)


# --- rule_from_canonical, both program shapes -------------------------------

def test_rule_from_canonical_round_trips_an_explicit_program():
    rule = _rule("g1", 41)
    rebuilt = rescore_heldout.rule_from_canonical(rule.canonical())
    assert rebuilt.canonical() == rule.canonical()


def test_rule_from_canonical_falls_back_to_the_named_strategy_for_offsets():
    """'lateral' carries dx=0.03 steps, which an explicit program cannot
    express; the rebuild must go through the repertoire name instead."""
    rule = Rule("g1", Trigger("observable.finger_gap", "lt", 0.0018, 1, 41, "value"),
                RecoverySpec(name="lateral", sensor_sd=0.02))
    rebuilt = rescore_heldout.rule_from_canonical(rule.canonical())
    assert rebuilt.recovery.program is None
    assert rebuilt.canonical() == rule.canonical()


# --- sanity against the real sealed campaign, if present --------------------

def test_stack_g1_rebuilds_to_its_sealed_final_sha():
    root = REPO_ROOT / "runs" / "stack-g1"
    if not root.exists():
        pytest.skip("runs/stack-g1 not present in this checkout")
    archived = parity_check.read_store_artifacts(root)
    prereg = parity_check.rebuild_preregistration(archived["preregistration"][0])
    bundle = rescore_heldout.rebuild_final_bundle(prereg, archived["generation"])
    assert bundle.sha() == archived["campaign_result"][0]["final_sha"]


def test_help_smoke():
    proc = subprocess.run(
        [sys.executable, "scripts/rescore_heldout.py", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0
    assert "--block" in proc.stdout and "--out" in proc.stdout
