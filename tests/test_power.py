"""Per-generation sample sizing."""
import pytest

from governor.power import discordant_needed, mcnemar_p, plan_generation, power_at


def test_mcnemar_matches_the_round_17_case():
    """9 fixed / 2 broken is the split that was rejected at n=120."""
    assert round(mcnemar_p(9, 2), 4) == 0.0654
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(10, 0) < 0.01


def test_power_rises_with_discordant_pairs():
    prev = -1.0
    for d in (5, 10, 20, 40):
        now = power_at(d, 0.8, 0.05)
        assert now >= prev
        prev = now
    assert power_at(40, 0.8, 0.05) > 0.95


def test_weaker_effects_need_more_pairs():
    assert discordant_needed(0.9, 0.05, 0.8) < discordant_needed(0.8, 0.05, 0.8)
    assert discordant_needed(0.8, 0.05, 0.8) < discordant_needed(0.7, 0.05, 0.8)


def test_round_17_was_underpowered():
    """11 discordant pairs against the 20 an 80%-power test needs."""
    assert discordant_needed(0.8, 0.05, 0.8) > 9 + 2


def test_sample_size_grows_as_residual_failures_shrink():
    """The whole point: later generations see smaller effects and need more seeds."""
    plans = [plan_generation(g, resid, 400, 400)
             for g, resid in ((1, 213), (2, 127), (3, 102))]
    sizes = [p.seeds_used for p in plans]
    assert sizes == sorted(sizes), f"sizing must escalate, got {sizes}"
    assert sizes[0] < sizes[-1]


def test_sizing_never_reads_the_candidate():
    """Signature-level guard: plan_generation sees only already-run episodes."""
    import inspect
    params = set(inspect.signature(plan_generation).parameters)
    for forbidden in ("candidate", "rule", "trigger", "p_value", "result"):
        assert forbidden not in params, (
            f"{forbidden!r} would make sizing depend on the candidate under test, "
            "which is optional stopping"
        )


def test_reservoir_cap_is_reported_not_silent():
    plan = plan_generation(9, 2, 400, reservoir=30)
    assert plan.capped and plan.seeds_used == 30
