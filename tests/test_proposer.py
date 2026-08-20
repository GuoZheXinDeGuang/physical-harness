"""The proposer seam, and the fact that a proposal is untrusted input.

Every refusal below is one a real language model can produce. None of them are
hypothetical: naming a feature that does not exist, reaching past the privilege
budget, and returning prose instead of JSON are the three failure modes any
schema-constrained generation has to survive.
"""
import numpy as np
import pytest

from governor.proposer import (
    LlmProposer, ProposerError, SearchProposer, build_brief, catalog_for_prompt,
    parse_proposal, scripted_transport,
)

VALID = {
    "feature": "observable.finger_gap", "op": "lt", "threshold": 0.005,
    "dwell": 1, "arm_after": 58, "reducer": "min", "recovery": "regrasp",
}


def _parse(payload, budget=0):
    return parse_proposal(payload, generation=1, privilege_budget=budget,
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
        '{"feature": "observable.does_not_exist", "op": "lt", "threshold": 0.1,'
        ' "dwell": 1, "arm_after": 10, "reducer": "value", "recovery": "regrasp"}',
        VALID,
    ]))
    rule = p.propose(traces, labels, generation=1, privilege_budget=0, recovery_sensor_sd=0.02)
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
                                recovery_sensor_sd=0.02)
        assert rule is not None and rule.declared_privilege() == 0
        assert provider.identity
