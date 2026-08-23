"""R8: the 验货 template parameterizes the existing evidence machine over a claim.

The load-bearing test is that a stack-shaped claim rebuilds ``stack_prereg()``
byte-for-byte -- proof the template runs the SAME machine (no new statistics),
just with the hand-written preregistration moved into manifest DATA. The rest
pins the claim -> Preregistration mapping (seed-block expansion, field overlay,
loud on a typo). No campaign is run here -- a real run is robosuite (R9 dogfood).
"""

from __future__ import annotations

import pytest

from scripts.acceptance_campaign import build_prereg, load_claim, main
from scripts.stack_campaign import stack_prereg

_STACK_CLAIM = {
    "task": "stack",
    "policy": "plugins.policies:stack_scripted_provider",
    "dev": [41000, 42000],
    "heldout": [42000, 42200],
    "stages": "plugins.embodiment_robosuite.env:stack_stages",
}


def test_build_prereg_reproduces_stack_prereg_byte_for_byte():
    assert build_prereg(_STACK_CLAIM).sha() == stack_prereg().sha()


def test_seed_blocks_expand_half_open():
    p = build_prereg(_STACK_CLAIM)
    assert p.dev == tuple(range(41000, 42000))
    assert p.heldout == tuple(range(42000, 42200))


def test_claim_overrides_prereg_fields():
    # a place-shaped claim: privileged budget + the place-shaped repair, exactly
    # what place_campaign.place_prereg set by hand.
    p = build_prereg(dict(_STACK_CLAIM, critic_budget=1, recovery_name="replace"))
    assert p.critic_budget == 1 and p.recovery_name == "replace"


def test_policy_ref_is_the_mount_not_the_label():
    # claim["policy"] is the driver MOUNT ref; the prereg label stays "scripted"
    # unless policy_label overrides it.
    assert build_prereg(_STACK_CLAIM).policy == "scripted"
    assert build_prereg(dict(_STACK_CLAIM, policy_label="clone")).policy == "clone"


def test_unknown_claim_key_is_loud():
    with pytest.raises(ValueError, match="not a Preregistration field"):
        build_prereg(dict(_STACK_CLAIM, bogus=1))


def test_missing_required_key_is_loud():
    claim = dict(_STACK_CLAIM)
    del claim["dev"]
    with pytest.raises(ValueError, match="missing required key"):
        build_prereg(claim)


def test_empty_seed_block_is_loud():
    with pytest.raises(ValueError, match="empty"):
        build_prereg(dict(_STACK_CLAIM, dev=[42000, 42000]))


def _card(tmp_path):
    card = tmp_path / "skill_stack"
    card.mkdir()
    (card / "manifest.toml").write_text(
        '[claim]\ntask = "stack"\n'
        'policy = "plugins.policies:stack_scripted_provider"\n'
        'dev = [41000, 42000]\nheldout = [42000, 42200]\n'
        'stages = "plugins.embodiment_robosuite.env:stack_stages"\n')
    return card


def test_load_claim_reads_the_manifest_table(tmp_path):
    assert load_claim(_card(tmp_path))["task"] == "stack"


def test_missing_claim_table_is_loud(tmp_path):
    card = tmp_path / "no_claim"
    card.mkdir()
    (card / "manifest.toml").write_text('actuation = "sim"\n')
    with pytest.raises(ValueError, match="no .claim. table"):
        load_claim(card)


def test_dry_run_prints_the_prereg_sha_without_mounting(tmp_path, capsys):
    rc = main(["--claim", str(_card(tmp_path)), "--dry-run"])
    assert rc == 0
    assert stack_prereg().sha()[:12] in capsys.readouterr().out


# ── the committed skill cards' claims re-run the SAME machine (R9 dogfood) ────

def test_committed_stack_card_claim_rebuilds_stack_prereg():
    """The stack skill card (plugins/task) is a real 说明书: acceptance_campaign
    rebuilds stack_prereg() byte-for-byte from its [claim]."""
    assert build_prereg(load_claim("plugins/task")).sha() == stack_prereg().sha()


def test_committed_place_card_claim_rebuilds_place_prereg():
    """The place skill card's [claim] rebuilds place_prereg() byte-for-byte -- the
    place-shaped overrides on the stack baseline, moved into manifest data."""
    from scripts.place_campaign import place_prereg
    assert build_prereg(load_claim("plugins/skill_place")).sha() == place_prereg().sha()


def test_claim_sealed_table_is_ignored_by_build_prereg():
    """[claim.sealed] is evidence for --verify-claim, never a prereg input -- it
    must not trip the loud unknown-field guard."""
    claim = dict(_STACK_CLAIM, sealed={"store": "runs/stack-g1", "skills": ["x"]})
    assert build_prereg(claim).sha() == stack_prereg().sha()
