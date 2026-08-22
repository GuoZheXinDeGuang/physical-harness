"""The beam promotion gate, driven without a simulator.

beam had zero test coverage until round 77: parity_check refuses beam stores
and nothing in tests/ imported it, so nothing would have caught a branch
promoting on the vs-parent gate alone. These stubs are beam's only guard. They
pin the rung's one discipline -- a branch generation must out-net its blind
twin, the same judgement gate run_campaign applies -- and pin that
`require_judgement=False` reproduces the pre-round-77 promotion rule exactly
(no blind arm is even run).
"""
from types import SimpleNamespace

from plugins.rsi import beam
from plugins.rsi.campaign import CampaignStore, Preregistration
from plugins.rsi.gate import PairedResult
from plugins.rsi.stats.search import Trigger


def _prereg(**overrides) -> Preregistration:
    kwargs = {"dev": tuple(range(30)), "heldout": tuple(range(200, 210)),
              "percept_noise": 0.02, "critic_budget": 0, "action_budget": 0,
              "recovery_sensor_sd": 0.02, "max_generations": 1}
    kwargs.update(overrides)
    return Preregistration(**kwargs)


def _run_stubbed(monkeypatch, tmp_path, *, require_judgement):
    """One branch, one generation, no rollouts: vs parent the candidate clears
    the gate easily, vs its blind twin it is a dead tie -- the round-45 failure
    shape (an unconditional recovery does just as well fired blind)."""
    baselines = []

    def fake_gate(specs, bundle, baseline=None, workers=10, executor=None):
        baselines.append(baseline)
        if baseline is None:  # vs parent at gen 1, selection block, held-out
            return PairedResult(n=len(specs), base_rate=0.5, governed_rate=0.8,
                                fixed=9, broken=1, fires=9, p_value=0.001,
                                declared_privilege=0)
        return PairedResult(n=len(specs), base_rate=0.8, governed_rate=0.8,
                            fixed=5, broken=5, fires=9, p_value=1.0,
                            declared_privilege=0)

    monkeypatch.setattr(beam, "paired_gate", fake_gate)
    monkeypatch.setattr(beam, "ablation_curve", lambda *a, **k: [])
    monkeypatch.setattr(beam, "_measure",
                        lambda specs, bundle, workers, executor=None:
                        [{"trace": {}, "success": False} for _ in specs])
    monkeypatch.setattr(beam, "search_triggers",
                        lambda *a, **k: [SimpleNamespace(trigger=Trigger(
                            "observable.finger_gap", "lt", 0.0018, 1, 41, "value"))])
    result = beam.run_beam_campaign(
        _prereg(require_judgement=require_judgement),
        CampaignStore(tmp_path / "store"),
        beam_width=1, selection=(500, 501, 502, 503), workers=1, verbose=False)
    return result, baselines


def test_beam_require_judgement_gates_an_unconditional_rule(monkeypatch, tmp_path):
    result, baselines = _run_stubbed(monkeypatch, tmp_path, require_judgement=True)

    hist = result["branches"][0]["history"]
    assert len(hist) == 1
    entry = hist[0]
    # the vs-parent gate alone would have promoted; only the blind tie stops it
    assert entry["gate"]["p_value"] < 0.05 < entry["blind_gate"]["p_value"]
    assert entry["promoted"] is False
    assert any(b is not None for b in baselines), "the blind arm never ran"


def test_beam_without_judgement_reproduces_the_pre_change_rule(monkeypatch, tmp_path):
    result, baselines = _run_stubbed(monkeypatch, tmp_path, require_judgement=False)

    entry = result["branches"][0]["history"][0]
    assert entry["promoted"] is True
    # pre-change beam never built a blind arm: every gate call must be
    # vs-ungoverned/vs-parent, or the switch changed old runs' cost and numbers
    assert all(b is None for b in baselines)


def test_beam_rejected_seed_rule_does_not_leak_into_selection(monkeypatch, tmp_path):
    """Round 77 rung 1: a gen-1 seed rule the gate REJECTS was left in the
    branch's bundle -- only `alive` was cleared -- so the branch still entered
    the surviving pool: it was scored on the selection block and eligible for
    held-out. A rejected branch must roll back to its parent and take no part in
    selection, while still being reported for audit."""
    def reject_gate(specs, bundle, baseline=None, workers=10, executor=None):
        # every arm says no: not significant, no net fix
        return PairedResult(n=len(specs), base_rate=0.5, governed_rate=0.5,
                            fixed=1, broken=1, fires=2, p_value=1.0,
                            declared_privilege=0)

    monkeypatch.setattr(beam, "paired_gate", reject_gate)
    monkeypatch.setattr(beam, "ablation_curve", lambda *a, **k: [])
    monkeypatch.setattr(beam, "_measure",
                        lambda specs, bundle, workers, executor=None:
                        [{"trace": {}, "success": False} for _ in specs])
    monkeypatch.setattr(beam, "search_triggers",
                        lambda *a, **k: [SimpleNamespace(trigger=Trigger(
                            "observable.finger_gap", "lt", 0.0018, 1, 41, "value"))])
    result = beam.run_beam_campaign(
        _prereg(require_judgement=False),
        CampaignStore(tmp_path / "store"),
        beam_width=1, selection=(500, 501, 502, 503), workers=1, verbose=False)

    entry = result["branches"][0]
    assert entry["history"][0]["promoted"] is False       # the gate rejected the seed
    assert entry["selection"] is None, "rejected branch was scored in the selection pool"
    assert result["selected"] is None, "a rejected branch reached the selection winner"


def test_beam_records_the_blind_gate_evidence(monkeypatch, tmp_path):
    on, _ = _run_stubbed(monkeypatch, tmp_path, require_judgement=True)
    off, _ = _run_stubbed(monkeypatch, tmp_path, require_judgement=False)

    for entry in on["branches"][0]["history"]:
        assert entry["blind_gate"] is not None, "judgement evidence must be auditable"
    for entry in off["branches"][0]["history"]:
        assert entry["blind_gate"] is None
