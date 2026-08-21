"""Search the recovery program, not just the trigger.

Why this round exists
---------------------
Through round 5 the only thing that evolved was a trigger: one feature, one
comparison, a threshold, a dwell and an arming step. The repair itself was a
hand-written constant. Calling that "self-evolving" would be generous -- Zetta
at least assembles recovery steps from a published tool catalog
(Zetta-Embodiment/zetta/evolution/models.py, ``RecoveryRule.steps``). So the
program becomes part of the search space here.

Cost control
------------
A recovery candidate cannot be screened offline: shadow replay predicts when a
TRIGGER fires on a recorded trajectory, but a different repair produces a
different trajectory, so the recording no longer describes it. Real rollouts are
therefore unavoidable, and the search is made affordable two ways: the baseline
arm is identical across all candidates and is computed once, and the space is
walked by coordinate descent from a working program rather than enumerated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from governor.env import EpisodeSpec
from plugins.rsi.governed import Bundle, RecoverySpec, Rule

#: Candidate durations per phase slot. The phase ORDER is fixed -- retreat, restage,
#: approach, close, lift -- because it encodes the physical structure of a regrasp;
#: what the search owns is how long to spend in each.
GRID: dict[str, tuple[int, ...]] = {
    "retreat": (10, 18, 26),
    "restage": (8, 15, 22),
    "approach": (20, 25, 32),
    "close": (10, 14, 20),
    "lift": (30, 40),
}
SLOTS = ("retreat", "restage", "approach", "close", "lift")
PHASE_OF = {"retreat": "descend", "restage": "above", "approach": "descend",
            "close": "close", "lift": "lift"}


def program_of(durations: dict[str, int]) -> tuple[tuple[str, int], ...]:
    """Assemble a recovery program from per-slot durations."""
    return tuple((PHASE_OF[s], int(durations[s])) for s in SLOTS)


DEFAULT_DURATIONS = {"retreat": 18, "restage": 15, "approach": 25, "close": 14, "lift": 40}


def _run(job):
    from plugins.rsi.governed import governed_rollout
    spec, bundle = job
    return governed_rollout(spec, bundle)


def _rate(specs: Sequence[EpisodeSpec], bundle: Bundle | None, workers: int,
          executor=None) -> float:
    from plugins.rsi.parallel import default_executor
    out = (executor or default_executor()).map(_run, [(s, bundle) for s in specs],
                                               workers=workers)
    return float(np.mean([r["success"] for r in out]))


@dataclass
class RecoverySearchResult:
    durations: dict[str, int]
    rate: float
    baseline_rate: float
    evaluations: int
    trace: list[tuple[str, int, float]]

    @property
    def program(self) -> tuple[tuple[str, int], ...]:
        return program_of(self.durations)


def search_recovery(
    specs: Sequence[EpisodeSpec], trigger, *, sensor_sd: float, critic_budget: int = 0,
    action_budget: int = 0, passes: int = 2, workers: int = 10, verbose: bool = True,
) -> RecoverySearchResult:
    """Coordinate descent over phase durations, scored on real rollouts.

    Starts from the hand-written program so the search can only improve on the
    status quo, and reports the full evaluation trace so a gain cannot be
    confused with grid luck.
    """
    baseline = _rate(specs, None, workers)
    durations = dict(DEFAULT_DURATIONS)

    def rate_for(d: dict[str, int]) -> float:
        rule = Rule("r", trigger, RecoverySpec(program=program_of(d), sensor_sd=sensor_sd))
        return _rate(specs, Bundle(rules=(rule,), critic_budget=critic_budget,
                                   action_budget=action_budget), workers)

    best = rate_for(durations)
    evals = 1
    trace: list[tuple[str, int, float]] = [("start", 0, best)]
    if verbose:
        print(f"  baseline {baseline:.1%} | hand-written program {best:.1%}")

    for p in range(passes):
        improved = False
        for slot in SLOTS:
            for value in GRID[slot]:
                if value == durations[slot]:
                    continue
                trial = {**durations, slot: value}
                r = rate_for(trial)
                evals += 1
                trace.append((slot, value, r))
                if r > best:
                    best, durations, improved = r, trial, True
                    if verbose:
                        print(f"    pass{p} {slot}={value} -> {r:.1%}  (accepted)")
        if not improved:
            if verbose:
                print(f"    pass{p}: no improvement; converged")
            break
    return RecoverySearchResult(durations, best, baseline, evals, trace)
