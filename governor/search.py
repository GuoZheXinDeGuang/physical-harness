"""Automatic Earliest-Observable-Divergence and trigger search.

Motivation is empirical, not theoretical. Hand-picking a critic threshold from
end-of-episode statistics produced a rule that fired on 1 of 60 episodes and
bought +1.7% (not significant); the same feature with a threshold taken from a
proper divergence scan fired on 34 and bought +50%. Threshold and arming time
are not human-guessable, so the harness searches them.

What is searched
----------------
A trigger is ``(feature, op, threshold, dwell, arm_after)``: once step index
reaches ``arm_after``, the predicate must hold for ``dwell`` consecutive steps.
Search is restricted to features the privilege budget admits, so a zero-budget
campaign can only discover rules a real robot could evaluate.

Objective
---------
Detection is scored by recall on eventual failures, precision against eventual
successes, and *earliness* -- a trigger that only fires at the final step leaves
no time to recover, so it is worth little even at perfect accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

import numpy as np

from governor.features import REGISTRY, Privilege, privilege_cost

Op = Literal["lt", "gt"]


@dataclass(frozen=True, slots=True)
class Trigger:
    """A machine-evaluable activation rule over one declared feature."""

    feature: str
    op: Op
    threshold: float
    dwell: int
    arm_after: int

    @property
    def privilege(self) -> int:
        return privilege_cost([self.feature])

    def fire_step(self, trace: dict[str, np.ndarray]) -> int | None:
        """First step index at which this trigger fires, or None."""
        series = trace[self.feature]
        hit = series < self.threshold if self.op == "lt" else series > self.threshold
        consec = 0
        for t in range(len(series)):
            if t < self.arm_after:
                consec = 0
                continue
            consec = consec + 1 if hit[t] else 0
            if consec >= self.dwell:
                return t
        return None

    def describe(self) -> str:
        sym = "<" if self.op == "lt" else ">"
        return (f"{self.feature} {sym} {self.threshold:.5g} for {self.dwell} steps, "
                f"armed from t={self.arm_after} (privilege={self.privilege})")


@dataclass(frozen=True, slots=True)
class TriggerScore:
    """How a trigger separates eventual failures from eventual successes."""

    trigger: Trigger
    recall: float          # fraction of failed episodes that fire
    false_positive: float  # fraction of successful episodes that fire
    median_fire: float     # median firing step among true detections
    lead: float            # median control steps between firing and episode end
    score: float

    def line(self) -> str:
        return (f"score={self.score:.3f} recall={self.recall:.2f} fp={self.false_positive:.2f} "
                f"fire@{self.median_fire:.0f} lead={self.lead:.0f}  {self.trigger.describe()}")


def _quantile_grid(values: np.ndarray, n: int = 12) -> np.ndarray:
    qs = np.linspace(0.05, 0.95, n)
    grid = np.unique(np.quantile(values, qs).round(6))
    return grid


def divergence_profile(traces, labels, feature: str) -> np.ndarray:
    """Per-step standardized separation between failing and succeeding episodes.

    Positive means successes read higher. The first step whose magnitude clears
    a threshold is the Earliest Observable Divergence for that feature.
    """
    ok = np.stack([t[feature] for t, y in zip(traces, labels) if y])
    bad = np.stack([t[feature] for t, y in zip(traces, labels) if not y])
    pooled = np.sqrt((ok.var(axis=0) + bad.var(axis=0)) / 2) + 1e-9
    return (ok.mean(axis=0) - bad.mean(axis=0)) / pooled


def earliest_divergence(traces, labels, feature: str, sigma: float = 2.0) -> int | None:
    """First control step where `feature` separates the two outcome groups."""
    prof = np.abs(divergence_profile(traces, labels, feature))
    idx = np.flatnonzero(prof >= sigma)
    return int(idx[0]) if idx.size else None


def search_triggers(
    traces: Sequence[dict[str, np.ndarray]],
    labels: Sequence[bool],
    *,
    privilege_budget: int = 0,
    dwells: Iterable[int] = (1, 2, 3),
    top_k: int = 8,
    min_recall: float = 0.5,
) -> list[TriggerScore]:
    """Rank triggers by detection quality under the privilege budget.

    Only features whose privilege cost fits `privilege_budget` are considered,
    which is what makes a zero-budget campaign structurally unable to discover a
    rule that a real robot could not evaluate.
    """
    labels = np.asarray(labels, dtype=bool)
    if labels.all() or (~labels).any() is False:
        raise ValueError("trigger search needs both successful and failed episodes")
    n_steps = len(next(iter(traces[0].values())))
    admissible = [n for n, f in REGISTRY.items()
                  if (0 if f.privilege is Privilege.OBSERVABLE else 1) <= privilege_budget]
    out: list[TriggerScore] = []
    for feature in sorted(admissible):
        eod = earliest_divergence(traces, labels, feature)
        if eod is None:
            continue
        # Arm at or after the divergence: firing before the signal exists is noise.
        arms = sorted({eod, min(eod + 2, n_steps - 1), min(eod + 6, n_steps - 1)})
        values = np.concatenate([t[feature][eod:] for t in traces])
        for thr in _quantile_grid(values):
            for op in ("lt", "gt"):
                for dwell in dwells:
                    for arm in arms:
                        trig = Trigger(feature, op, float(thr), int(dwell), int(arm))
                        fires = [trig.fire_step(t) for t in traces]
                        tp = [f for f, y in zip(fires, labels) if not y and f is not None]
                        fp = [f for f, y in zip(fires, labels) if y and f is not None]
                        n_bad = int((~labels).sum()); n_ok = int(labels.sum())
                        recall = len(tp) / max(n_bad, 1)
                        fpr = len(fp) / max(n_ok, 1)
                        if recall < min_recall:
                            continue
                        med = float(np.median(tp)) if tp else float(n_steps)
                        lead = n_steps - med
                        # earliness is worth real weight: a trigger with no lead
                        # cannot be recovered from, however accurate it is.
                        score = recall - 1.2 * fpr + 0.25 * (lead / n_steps)
                        out.append(TriggerScore(trig, recall, fpr, med, lead, score))
    out.sort(key=lambda s: -s.score)
    # keep the best trigger per (feature, op) so the head is not one rule's neighbours
    seen: set[tuple[str, str]] = set()
    deduped: list[TriggerScore] = []
    for s in out:
        key = (s.trigger.feature, s.trigger.op)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
        if len(deduped) >= top_k:
            break
    return deduped
