"""The proposer seam, and the fact that a proposal is untrusted input.

Every refusal below is one a real language model can produce. None of them are
hypothetical: naming a feature that does not exist, reaching past the privilege
budget, and returning prose instead of JSON are the three failure modes any
schema-constrained generation has to survive.
"""
import numpy as np
import pytest

import plugins.embodiment_robosuite.features  # noqa: F401  registry population (post-inversion)
from governor.proposer import (
    LlmProposer,
    ProposerError,
    SearchProposer,
    build_brief,
    catalog_for_prompt,
    parse_proposal,
    scripted_transport,
)
from plugins.rsi.repertoire import names as _strategy_names

#: The vocabulary the rsi side supplies at every call site since round 76;
#: tests play the supplier role explicitly.
STRATEGIES = tuple(_strategy_names())

VALID = {
    "feature": "observable.finger_gap", "op": "lt", "threshold": 0.005,
    "dwell": 1, "arm_after": 58, "reducer": "min", "recovery": "regrasp",
}


def _parse(payload, budget=0):
    return parse_proposal(payload, generation=1, privilege_budget=budget,
                          strategies=STRATEGIES,
                          recovery_sensor_sd=0.02, n_steps=100)


def test_a_valid_proposal_becomes_a_rule():
    rule = _parse(VALID)
    assert rule.trigger.feature == "observable.finger_gap"
    assert rule.trigger.reducer == "min"
    assert rule.declared_privilege() == 0


def test_json_string_and_dict_are_both_accepted():
    import json
    assert _parse(json.dumps(VALID)).trigger.threshold == pytest.approx(0.005)


# --- refusals ---------------------------------------------------------------

def test_invented_feature_is_refused():
    with pytest.raises(ProposerError, match="unknown feature"):
        _parse({**VALID, "feature": "observable.object_is_grasped"})


def test_privileged_feature_over_budget_is_refused():
    """The load-bearing one: a proposal cannot spend privilege it was not given."""
    with pytest.raises(ProposerError, match="privilege"):
        _parse({**VALID, "feature": "privileged.object_z"}, budget=0)
    ok = _parse({**VALID, "feature": "privileged.object_z"}, budget=1)
    assert ok.declared_privilege() >= 1


def test_prose_instead_of_json_is_refused():
    with pytest.raises(ProposerError, match="not valid JSON"):
        _parse("Sure! I'd suggest watching the gripper closely.")


@pytest.mark.parametrize("bad,match", [
    ({"op": "approximately"}, "operator"),
    ({"reducer": "median"}, "reducer"),
    ({"threshold": "low"}, "non-numeric"),
    ({"threshold": float("nan")}, "finite"),
    ({"dwell": 0}, "dwell"),
    ({"arm_after": -5}, "arm_after"),
    ({"arm_after": 100000}, "arm_after"),
    ({"recovery": "teleport"}, "unknown recovery"),
])
def test_malformed_fields_are_refused(bad, match):
    with pytest.raises(ProposerError, match=match):
        _parse({**VALID, **bad})


def test_missing_field_is_refused():
    payload = dict(VALID)
    del payload["threshold"]
    with pytest.raises(ProposerError, match="missing"):
        _parse(payload)


# --- the brief --------------------------------------------------------------

def _traces(n=20):
    traces, labels = [], []
    for i in range(n):
        failing = i % 2 == 0
        gap = np.full(80, 0.001 if failing else 0.04)
        traces.append({
            "observable.finger_gap": gap,
            "observable.eef_z": np.linspace(1.0, 0.9, 80),
            "observable.gripper_effort": np.zeros(80),
            "observable.joint_speed": np.zeros(80),
        })
        labels.append(not failing)
    return traces, labels


def test_brief_never_offers_a_feature_outside_the_budget():
    traces, labels = _traces()
    brief = build_brief(traces, labels, generation=1, privilege_budget=0)
    assert all(c["privilege_cost"] == 0 for c in brief["catalog"])
    assert all(not s["feature"].startswith("privileged.") for s in brief["separations"])
    assert not any("privileged" in c["name"] for c in catalog_for_prompt(0))


def test_brief_carries_statistics_not_episodes():
    traces, labels = _traces()
    brief = build_brief(traces, labels, generation=1, privilege_budget=1)
    flat = str(brief)
    assert "episodes" in brief and isinstance(brief["episodes"], int)
    assert len(flat) < 20000, "the brief should be a summary, not a transcript"


# --- the LLM provider end to end -------------------------------------------

def test_llm_proposer_retries_past_a_refusal_then_succeeds():
    traces, labels = _traces()
    p = LlmProposer(transport=scripted_transport([
        ('{"feature": "observable.does_not_exist", "op": "lt", "threshold": 0.1,'
         ' "dwell": 1, "arm_after": 10, "reducer": "value", "recovery": "regrasp"}'),
        VALID,
    ]))
    rule = p.propose(traces, labels, generation=1, privilege_budget=0,
                     strategies=STRATEGIES, recovery_sensor_sd=0.02)
    assert rule is not None
    assert len(p.rejections) == 1 and "unknown feature" in p.rejections[0]


def test_llm_proposer_gives_up_rather_than_inventing():
    traces, labels = _traces()
    p = LlmProposer(transport=scripted_transport(["not json at all"]))
    assert p.propose(traces, labels, generation=1, privilege_budget=0,
                     recovery_sensor_sd=0.02) is None
    assert len(p.rejections) == p.attempts


def test_both_providers_satisfy_the_same_contract():
    traces, labels = _traces()
    for provider in (SearchProposer(),
                     LlmProposer(transport=scripted_transport([VALID]))):
        rule = provider.propose(traces, labels, generation=1, privilege_budget=0,
                                strategies=STRATEGIES, recovery_sensor_sd=0.02)
        assert rule is not None and rule.declared_privilege() == 0
        assert provider.identity


# --- repair-aware tie-break (round 88 part C) -------------------------------

def _peaked(n=40, N=80, span=0.035):
    """Failing (even) episodes peel finger_gap from 0.04 at t=10, widest at t=50;
    diverges at the onset AND peaks late, so many arm variants tie at score 1.0."""
    from harness.spec import EpisodeSpec
    traces, labels, specs = [], [], []
    for s in range(n):
        failing = s % 2 == 0
        fg = np.full(N, 0.04)
        if failing:
            for t in range(10, N):
                diff = span * (t - 9) / 41 if t <= 50 else span * (N - t) / 30
                fg[t] = 0.04 - diff
        traces.append({
            "observable.finger_gap": fg,
            "observable.eef_z": np.linspace(1.0, 0.9, N),
            "observable.gripper_effort": np.zeros(N),
            "observable.joint_speed": np.zeros(N),
        })
        labels.append(not failing)
        specs.append(EpisodeSpec(seed=s))
    return traces, labels, specs


class _CountingExecutor:
    def __init__(self):
        self.map_calls = 0

    def map(self, fn, items, *, workers):
        self.map_calls += 1
        return [fn(item) for item in items]


def test_tiebreak_picks_max_fixed_capped_and_dropped_logged(monkeypatch):
    """Detection saturates, so the objective ties many arm variants at the top
    score and the dedup keeps the earliest arm. The tie-break replays the ties on
    the dev block, keeps the max-fixed, caps replays at TIE_BREAK_CAP, and logs
    what the cap dropped."""
    from governor.proposer import TIE_BREAK_CAP, SearchProposer
    from plugins.rsi import gate

    traces, labels, dev_specs = _peaked()

    def _fake_run(job):
        # A failing dev seed is repaired ONLY by a late arm (>= 40); later arms
        # fix more, so the tie-break must reject the early-arm default.
        spec, bundle = job
        failing = spec.seed % 2 == 0
        fixed = failing and bundle.rules[0].trigger.arm_after >= 40
        return {"success": (not failing) or fixed, "fired_at": 0, "trace": {}}
    monkeypatch.setattr(gate, "_run", _fake_run)

    ex = _CountingExecutor()
    p = SearchProposer()
    rule = p.propose(traces, labels, generation=1, privilege_budget=0,
                     recovery_sensor_sd=0.02, dev_specs=dev_specs, executor=ex)
    sel = p.selection

    assert rule.trigger.arm_after >= 40, "tie-break must arm at the peak, not the onset"
    assert sel["pick_fixed"] == 20 == max(y["fixed"] for y in sel["yields"])
    assert sel["tied"] > TIE_BREAK_CAP, "fixture must actually exercise the cap"
    assert sel["replayed"] == TIE_BREAK_CAP
    assert ex.map_calls == TIE_BREAK_CAP, "one dev replay per capped candidate"
    assert len(sel["dropped"]) == sel["tied"] - TIE_BREAK_CAP

    # determinism: an identical call makes the identical pick.
    p2 = SearchProposer()
    rule2 = p2.propose(traces, labels, generation=1, privilege_budget=0,
                       recovery_sensor_sd=0.02, dev_specs=dev_specs, executor=_CountingExecutor())
    assert rule2.trigger == rule.trigger


def test_tiebreak_residual_ties_keep_enumeration_order(monkeypatch):
    """When every tied candidate fixes the SAME number, the pick is the first in
    enumeration order -- the dedup default -- so a genuine tie is never reshuffled
    nondeterministically."""
    from governor.proposer import SearchProposer
    from plugins.rsi import gate
    from plugins.rsi.stats.search import search_triggers

    traces, labels, dev_specs = _peaked()
    # nobody is fixed: every candidate ties at 0 fixed.
    monkeypatch.setattr(gate, "_run", lambda job: {
        "success": job[0].seed % 2 == 1, "fired_at": None, "trace": {}})

    class _Ex:
        def map(self, fn, items, *, workers):
            return [fn(item) for item in items]

    default = search_triggers(traces, labels, privilege_budget=0, top_k=3)[0].trigger
    p = SearchProposer()
    rule = p.propose(traces, labels, generation=1, privilege_budget=0,
                     recovery_sensor_sd=0.02, dev_specs=dev_specs, executor=_Ex())
    assert rule.trigger == default
    assert p.selection["pick_fixed"] == 0


def test_search_without_dev_block_keeps_the_ranked_head(monkeypatch):
    """No dev_specs/executor supplied -> no replay, byte-for-byte the old pick
    (ranked[0]). This is the reasoner-adapter and no-rollout path."""
    from governor.proposer import SearchProposer
    from plugins.rsi import gate
    from plugins.rsi.stats.search import search_triggers

    traces, labels, _ = _peaked()
    monkeypatch.setattr(gate, "_run", lambda job: (_ for _ in ()).throw(
        AssertionError("no replay must run without a dev block")))
    default = search_triggers(traces, labels, privilege_budget=0, top_k=3)[0].trigger
    rule = SearchProposer().propose(traces, labels, generation=1, privilege_budget=0,
                                    recovery_sensor_sd=0.02)
    assert rule.trigger == default
