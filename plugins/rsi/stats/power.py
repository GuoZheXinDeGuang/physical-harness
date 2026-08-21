"""Sample size for a paired gate, computed per generation.

Why a constant dev size is wrong
--------------------------------
Later generations face smaller residual effects: generation 1 attacks the whole
failure set, generation 2 only what generation 1 left. With a fixed
``rollout_count`` the gate is progressively underpowered, and round 17 measured
the consequence -- a candidate worth +6.5pp on held-out was rejected at p=0.065
on 120 dev seeds, where the same 9:2 discordant split clears comfortably at 180.
Zetta's ``EvolutionProtocol.rollout_count`` is likewise a constant 50.

Why this is not optional stopping
---------------------------------
The size for generation g is computed from the residual failure rate measured
BEFORE the candidate is evaluated, and seeds are drawn as a prefix of a
preregistered ordered reservoir. Nothing about the candidate's own outcome
influences how many episodes it is judged on. Growing a sample until a p-value
crosses alpha would be a different procedure with a different, inflated error
rate; this is not that.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb


def mcnemar_p(fixed: int, broken: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs."""
    n = fixed + broken
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(fixed, broken) + 1))
    return min(1.0, 2.0 * tail / 2**n)


def power_at(discordant: int, fix_share: float, alpha: float) -> float:
    """Probability the exact test rejects, given `discordant` pairs.

    `fix_share` is the true probability that a discordant pair is a fix rather
    than a regression -- the effect size in the only terms this test sees.
    """
    total = 0.0
    for fixed in range(discordant + 1):
        p_obs = comb(discordant, fixed) * fix_share**fixed * (1 - fix_share) ** (discordant - fixed)
        if mcnemar_p(fixed, discordant - fixed) < alpha:
            total += p_obs
    return total


def discordant_needed(fix_share: float, alpha: float, power: float, cap: int = 400) -> int:
    """Smallest number of discordant pairs reaching `power` at `alpha`."""
    for d in range(2, cap + 1):
        if power_at(d, fix_share, alpha) >= power:
            return d
    return cap


@dataclass(frozen=True, slots=True)
class PowerPlan:
    """What a generation needs, decided before its candidate is judged."""

    generation: int
    residual_failures: int
    residual_rate: float
    discordant_needed: int
    seeds_needed: int
    seeds_used: int
    capped: bool
    #: Discordant pairs expected per residual failure, measured from the last
    #: completed generation rather than assumed.
    discordance_yield: float = 0.7
    #: Fraction of the generation's episodes the proposer's search actually sees.
    search_fraction: float = 1.0

    def line(self) -> str:
        return (f"gen {self.generation}: residual {self.residual_failures} "
                f"({self.residual_rate:.1%}) -> need {self.discordant_needed} discordant "
                f"-> {self.seeds_needed} seeds, using {self.seeds_used}"
                + f" [yield {self.discordance_yield:.2f}/failure]"
                + ("" if self.search_fraction >= 1.0
                   else f" [search sees {self.search_fraction:.0%}]")
                + ("  [CAPPED]" if self.capped else ""))


def plan_generation(
    generation: int, residual_failures: int, n_observed: int, reservoir: int, *,
    fix_share: float = 0.80, alpha: float = 0.05, power: float = 0.80,
    discordance_yield: float = 0.7, minimum: int = 60,
    search_fraction: float = 1.0,
) -> PowerPlan:
    """Seeds this generation should be gated on.

    A discordant pair needs an episode that currently FAILS *and* whose outcome
    the candidate actually changes, so the expected yield per seed is
    ``residual_rate * discordance_yield``. Both inputs come from episodes
    already run, never from the candidate under test.

    Until round 32 this second factor was a fixed 0.7 named ``engage_rate``,
    which conflated engaging with an episode and changing its outcome. That
    holds where recovery almost always works (the scripted policy fixes what it
    fires on) and fails badly where it does not: the cloned policy repairs 27%
    of what it fires on, so a generation sized for 20 discordant pairs returned
    3, and a candidate with the right sign died of Type II error. The caller
    measures this from the last completed generation instead of assuming it.
    """
    residual_rate = residual_failures / max(n_observed, 1)
    need_d = discordant_needed(fix_share, alpha, power)
    yield_per_seed = max(residual_rate * discordance_yield, 1e-6)
    seeds = max(minimum, int(round(need_d / yield_per_seed)))  # noqa: RUF046  parity-pinned numeric path
    # Out-of-sample screening shows the SEARCH only half the episodes. Sizing a
    # screened generation like an unscreened one silently halves the sample the
    # proposer actually learns from, which is a different experiment from the
    # one the plan claims to be running.
    seeds = int(round(seeds / max(search_fraction, 1e-6)))  # noqa: RUF046  parity-pinned numeric path
    used = min(seeds, reservoir)
    return PowerPlan(generation, residual_failures, residual_rate, need_d, seeds, used,
                     capped=seeds > reservoir, discordance_yield=discordance_yield,
                     search_fraction=search_fraction)
