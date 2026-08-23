"""R7: the reasoner seam is LIVE, model identity is content, the card degrades.

Until R7 ``run_campaign`` hard-called ``propose_rule`` and the mounted
``reasoner.proposer`` was never consulted -- a dead seam. This pins the fix:

* a mounted reasoner is actually invoked by ``run_campaign`` and sees the brief;
* the default (reasoner=None) path is byte-identical to the old direct call, so
  every sealed campaign still rebuilds;
* a reasoner's ``identity`` (the qwen card) enters the prereg content hash,
  closing the QWEN38_MODEL/QWEN38_BASE_URL env-var smuggling;
* a prereg that predates the ``reasoner`` field rebuilds byte-identical (fold);
* the qwen card is doctorable (Tier A shape) and SKIPs loudly (Tier B) when its
  endpoint is down -- exactly how scripts/round25_rerun degrades its qwen arm;
* the enabled=false qwen card sits beside plugins/reasoner without tripping the
  duplicate-capability guard, and does not move the base plan sha.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from harness.config import resolve_plan
from harness.manifest import discover
from plugins.rsi import gate
from plugins.rsi.campaign import (
    CampaignStore,
    Preregistration,
    propose_rule,
    run_campaign,
)
from profiles import base_profile
from scripts.plugin_doctor import check

_REPO = Path(__file__).resolve().parent.parent
_SEALED_BASE_SHA = "b905a5119415de325c8c1d13f7c8a552eb3b2e8565e0a8af4ef7ca9c5f4c1a7c"


class _SerialExecutor:
    def map(self, fn, items, *, workers):
        return [fn(item) for item in items]


def _fake_run(job):
    """Even seeds fail ungoverned and are repaired by any armed rule; peaked
    finger_gap traces give the search a candidate. No simulator, no episodes."""
    spec, bundle = job
    failing = spec.seed % 2 == 0
    real = 0 if bundle is None else sum(
        1 for r in bundle.rules if not r.rule_id.endswith("-blind"))
    fg = np.full(60, 0.04)
    if failing:
        for t in range(10, 60):
            fg[t] = 0.04 - 0.035 * min(t - 9, 40) / 40
    return {"success": (not failing) or real >= 1,
            "fired_at": 0 if real and failing else None,
            "trace": {"observable.finger_gap": fg,
                      "observable.eef_z": np.linspace(1.0, 0.9, 60),
                      "observable.gripper_effort": np.zeros(60),
                      "observable.joint_speed": np.zeros(60)}}


def _prereg(**kw):
    base = {"dev": tuple(range(90000, 90040)), "heldout": tuple(range(90100, 90140)),
            "percept_noise": 0.02, "critic_budget": 0, "action_budget": 0,
            "recovery_sensor_sd": 0.02, "max_generations": 1,
            "task": "lift", "policy": "scripted"}
    base.update(kw)
    return Preregistration(**base)


class _RecordingReasoner:
    """Records every brief it is handed, then defers to the deterministic
    proposer so the campaign behaves exactly as the default would."""

    def __init__(self, identity=None):
        self.briefs = []
        if identity is not None:
            self.identity = identity

    def propose(self, brief):
        self.briefs.append(brief)
        return {"rule": propose_rule(
            brief["traces"], brief["labels"], generation=brief["generation"],
            prereg=brief["prereg"], dev_specs=brief["dev_specs"],
            executor=brief["executor"], workers=brief["workers"],
            parent=brief["parent"], store=brief["store"])}


# --- (1) the seam is live: the mounted reasoner is invoked with the brief ----

def test_run_campaign_invokes_the_mounted_reasoner_with_the_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    reasoner = _RecordingReasoner()
    store = CampaignStore(tmp_path / "c")
    run_campaign(_prereg(), store, workers=1, verbose=False,
                 executor=_SerialExecutor(), reasoner=reasoner)
    assert reasoner.briefs, "the mounted reasoner was never called -- the seam is dead"
    brief = reasoner.briefs[0]
    assert brief["generation"] == 1
    assert set(brief) >= {"traces", "labels", "generation", "prereg",
                          "dev_specs", "executor", "workers", "parent", "store"}
    assert len(brief["traces"]) == len(brief["labels"]) == 40


# --- (2) parity: the default seam path == the old direct propose_rule call ----

def test_default_reasoner_is_byte_identical_to_the_old_direct_call(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    a = run_campaign(_prereg(), CampaignStore(tmp_path / "a"), workers=1,
                     verbose=False, executor=_SerialExecutor())
    b = run_campaign(_prereg(), CampaignStore(tmp_path / "b"), workers=1, verbose=False,
                     executor=_SerialExecutor(), reasoner=_RecordingReasoner())
    assert a["final_sha"] == b["final_sha"]
    assert a["preregistration_sha"] == b["preregistration_sha"]
    assert a["rules"] == b["rules"] and a["promoted"] == b["promoted"]


# --- (3) model identity is content: it moves the prereg sha; None folds out ---

def test_reasoner_identity_moves_the_prereg_hash():
    """The core of closing the env-var smuggling, same shape as the percept-ref
    guard: which model proposed the rules is content, the default carries none."""
    assert _prereg().sha() == _prereg(reasoner=None).sha()          # default folds
    assert _prereg().sha() != _prereg(reasoner="qwen38(model=x)").sha()


def test_run_campaign_stamps_the_reasoner_identity_into_the_seal(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_run", _fake_run)
    ident = "qwen38(model=qwen3-8b,base=http://h:30000/v1,temp=0.0,seed=0,attempts=2)"
    store = CampaignStore(tmp_path / "c")
    res = run_campaign(_prereg(), store, workers=1, verbose=False,
                       executor=_SerialExecutor(), reasoner=_RecordingReasoner(ident))
    prereg_art = store.read(res["preregistration_sha"])
    assert prereg_art["reasoner"] == ident
    # and it actually moved the sha off the default-reasoner run's
    default = run_campaign(_prereg(), CampaignStore(tmp_path / "d"), workers=1,
                           verbose=False, executor=_SerialExecutor())
    assert res["preregistration_sha"] != default["preregistration_sha"]


# --- (4) a prereg predating the field rebuilds byte-identical (hash fold) -----

def test_prereg_predating_the_reasoner_field_rebuilds_byte_identical():
    from plugins.rsi.rebuild import rebuild_preregistration

    sealed = _prereg()
    payload = sealed._hash_payload()             # a sealed archive: no reasoner key
    assert "reasoner" not in payload
    rebuilt = rebuild_preregistration(payload)
    assert rebuilt.reasoner is None
    assert rebuilt.sha() == sealed.sha()


# --- (5) the qwen card: doctorable, degrades, unfolded, sha-neutral ----------

def test_doctor_greens_the_qwen_card_committed():
    rep = check(_REPO / "plugins" / "model_qwen")
    assert rep.green, [(r.tier, r.name, r.detail) for r in rep.results if r.status == "FAIL"]
    a = [r for r in rep.results if r.tier == "A" and r.name == "reasoner.proposer"]
    assert a and a[0].status == "PASS"           # Tier A shape always runs
    b = [r for r in rep.results if r.tier == "B" and r.name == "reasoner.proposer"]
    assert b and b[0].status in ("SKIP", "PASS")  # SKIP when the endpoint is down


def test_doctor_skips_the_qwen_reasoner_when_the_endpoint_is_down(tmp_path):
    # point it at a dead port so available() is deterministically False here.
    body = ('needs_sim = false\n[mounts."reasoner.proposer"]\n'
            'ref = "plugins.model_qwen:provider"\n'
            'params = { base_url = "http://127.0.0.1:9/v1", attempts = 1 }\n')
    d = tmp_path / "qwen_dead"
    d.mkdir()
    (d / "manifest.toml").write_text(body)
    rep = check(d)
    assert rep.green, [(r.tier, r.name, r.detail) for r in rep.results if r.status == "FAIL"]
    b = [r for r in rep.results if r.tier == "B" and r.name == "reasoner.proposer"]
    assert b and b[0].status == "SKIP" and "unreachable" in b[0].detail


def test_enabled_false_qwen_card_is_unfolded_and_sha_neutral():
    """The committed card claims reasoner.proposer (plugins/reasoner owns it), yet
    discover() does not raise and the incumbent still owns the seam -- because the
    card is enabled=false. The base plan sha is untouched."""
    reg = discover()                              # would raise on a folded duplicate
    reasoner_mounts = [m for m in reg.mounts if m.capability == "reasoner.proposer"]
    assert len(reasoner_mounts) == 1
    assert reasoner_mounts[0].provider == "plugins.reasoner:provider"
    assert resolve_plan(base_profile()).sha() == _SEALED_BASE_SHA


def test_qwen_provider_reports_a_model_identity():
    from harness.contracts import Reasoner
    from harness.registry import load_provider

    p = load_provider("plugins.model_qwen:provider",
                      {"model": "qwen3-8b", "base_url": "http://h:30000/v1"})
    assert isinstance(p, Reasoner)
    assert "qwen3-8b" in p.identity and "h:30000" in p.identity
