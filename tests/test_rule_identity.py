"""The reducer changes what a rule does. If it is not in the canonical form,
the parent-freeze assertion cannot see a parent being swapped underneath it."""
import pytest
from governor.governed import Bundle, RecoverySpec, Rule
from governor.search import Trigger


def _rule(reducer, rule_id="g1"):
    return Rule(rule_id=rule_id,
                trigger=Trigger("observable.finger_gap", "lt", 0.0032, 1, 59, reducer),
                recovery=RecoverySpec(sensor_sd=0.010))


def test_reducer_is_part_of_a_rules_identity():
    assert _rule("value").canonical() != _rule("min").canonical()


def test_parent_freeze_catches_a_swapped_reducer():
    """Faithful to the real path: a legitimate append, then a parent swapped.

    parent_sha cannot help here -- it is computed from the same canonical form,
    so a dropped field blinds both checks at once.
    """
    parent = Bundle(rules=(_rule("value"),), critic_budget=0, action_budget=0)
    child = parent.append(_rule("value", "g2"))
    tampered = Bundle(rules=(_rule("min"),), critic_budget=0, action_budget=0)
    assert tampered.sha() != parent.sha(), "the bundle hash ignores the reducer"
    with pytest.raises(ValueError, match="frozen"):
        child.assert_atomic_child_of(tampered)
