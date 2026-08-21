"""Stage vocabulary: a measurement overlay over the frozen policy's schedule.

A ``StageSpec`` chain hangs on ``EpisodeSpec.stages`` / ``Preregistration.stages``
and is consumed by the governed rollout as a SCORER, never a controller: stage
boundaries are derived from the policy's own schedule clock, and the predicate
neither advances a stage nor feeds the critic. Kernel-side and stdlib-pure,
sibling to spec_tabletop.py, so the kernel boundary test covers it for free.

Identity is recursive ``dataclasses.asdict`` through ``Preregistration.sha()``;
there is deliberately NO hand-written canonical() form, so a future field
cannot hide from the hash (the round-34 blind spot exists only for
hand-written forms). Thresholds are authored literals, never computed values:
a computed threshold is the round-10 table-height fragility.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Clause:
    """One machine-evaluable condition over a declared registry feature."""

    feature: str
    op: str
    threshold: float

    def __post_init__(self) -> None:
        # A typo fails where it is written, not mid-episode (Trigger's rule).
        if self.op not in ("lt", "gt"):
            raise ValueError(f"unknown operator {self.op!r}; expected 'lt' or 'gt'")


@dataclass(frozen=True, slots=True)
class StageSpec:
    """A contiguous group of policy-schedule steps and its success predicate.

    ``budget`` is the stage's span in policy-schedule steps; ``success`` is an
    instantaneous AND over clauses, evaluated at stage exit -- the same shape
    as the embodiment's lifted() check. No reducer/dwell machinery until a
    stage measurably needs a temporal hold.
    """

    name: str
    success: tuple[Clause, ...]
    budget: int

    def holds(self, view) -> bool:
        """Instantaneous AND of the clauses over one feature view."""
        return all(
            (view[c.feature] < c.threshold) if c.op == "lt" else (view[c.feature] > c.threshold)
            for c in self.success
        )
