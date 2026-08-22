"""Round 92 rung 2: used/consec keyed by CHAIN POSITION, not rule_id.

Nothing seals a bundle before promotion, so nothing forbids two rules sharing an
id. When governed_rollout keyed the invocation counter and dwell accumulator by
rule_id, two rules with the same id merged both -- the round-92 place-g1 false
negative: the appended place rule (id "g1") could never fire because the parent
grasp rule (also "g1") reset the shared dwell every step and ate the shared
invocation. Keying by chain position de-fangs that structurally.

Sealed-parity proof (unique-id bundles are a bijection between id- and
index-keying, so behavior is identical by construction): the byte-for-byte
goldens already in the suite --
    tests/test_determinism.py::test_same_seed_is_bit_identical
    tests/test_determinism.py::test_governed_episode_never_steps_a_terminated_env
        (runs a UNIQUE-id four-rule governed bundle g1..g4)
    tests/test_replace_recovery.py::test_regrasp_replay_is_byte_identical
    tests/test_rescore_heldout.py::test_stack_g1_rebuilds_to_its_sealed_final_sha
"""

import numpy as np

from harness.spec import EpisodeSpec
from plugins.rsi.governed import Bundle, RecoverySpec, Rule, governed_rollout
from plugins.rsi.stats.search import Trigger


def test_colliding_rule_ids_do_not_merge_dwell_or_invocation():
    """Two rules, SAME id "g1". rule[0] never crosses (eef_z > 1e9 is impossible)
    but, being armed, re-zeros its dwell counter every step; rule[1] needs three
    consecutive hits. Keyed by rule_id, rule[0]'s zeroing is rule[1]'s zeroing --
    rule[1]'s dwell can never exceed 1, so it never fires. Keyed by chain
    position, rule[1] keeps its own accumulator and fires."""
    never = Rule("g1", Trigger("observable.eef_z", "gt", 1e9, 1, 0),
                 RecoverySpec(sensor_sd=0.02))
    dweller = Rule("g1", Trigger("observable.eef_z", "gt", -1e9, 3, 0),
                   RecoverySpec(sensor_sd=0.02))
    bundle = Bundle(rules=(never, dweller), critic_budget=0, action_budget=0)

    r = governed_rollout(EpisodeSpec(seed=7), bundle)
    assert r["fires"], "the second colliding-id rule never fired: rule_id-keyed dwell/invocation merged"


def test_unique_id_two_rule_bundle_stays_reproducible():
    """The index-keying anchor: a UNIQUE-id two-rule bundle (g1 always fires, g2
    never) is bit-reproducible across same-seed reruns. Parity with the pre-change
    id-keyed path is by construction (a bijection for unique ids); the sealed
    goldens named in the module docstring pin it byte-for-byte."""
    rules = (Rule("g1", Trigger("observable.eef_z", "gt", -1e9, 1, 0), RecoverySpec(sensor_sd=0.02)),
             Rule("g2", Trigger("observable.eef_z", "gt", 1e9, 1, 0), RecoverySpec(sensor_sd=0.02)))
    bundle = Bundle(rules=rules, critic_budget=0, action_budget=0)

    a = governed_rollout(EpisodeSpec(seed=3), bundle)
    b = governed_rollout(EpisodeSpec(seed=3), bundle)
    assert a["fires"] == b["fires"] and a["success"] == b["success"]
    da = np.concatenate([a["trace"][k] for k in sorted(a["trace"])])
    db = np.concatenate([b["trace"][k] for k in sorted(b["trace"])])
    assert np.array_equal(da, db), "same-seed reruns of the unique-id bundle diverged"
    assert a["fires"], "g1 (always-true) must still fire on the unique-id path"
