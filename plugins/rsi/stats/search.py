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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from harness.features import REGISTRY, Privilege, privilege_cost

Op = Literal["lt", "gt"]

#: How a trigger reduces the feature history before comparing.
#:
#: ``value`` is the instantaneous reading -- the only form through round 13, and
#: enough for a failure that is bimodal in one observable at a predictable step.
#: The cloned policy has no fixed schedule, so its failures land at different
#: steps and a per-step scan averages misaligned events into nothing (peak 1.16
#: sigma). A RUNNING reduction is invariant to when something happened:
#: ``observable.eef_z`` separates the clone's outcomes at Cohen d = 1.43 under
#: ``min`` while showing nothing instantaneously. All four stay O(1) per step,
#: so they remain evaluable at action frequency.
Reducer = Literal["value", "min", "max", "range"]
REDUCERS: tuple[Reducer, ...] = ("value", "min", "max", "range")


def reduce_series(series: np.ndarray, reducer: Reducer) -> np.ndarray:
    """Running reduction: element t is the reduction over steps 0..t inclusive."""
    if reducer == "value":
        return series
    if reducer == "min":
        return np.minimum.accumulate(series)
    if reducer == "max":
        return np.maximum.accumulate(series)
    if reducer == "range":
        return np.maximum.accumulate(series) - np.minimum.accumulate(series)
    raise ValueError(f"unknown reducer {reducer!r}")


@dataclass(frozen=True, slots=True)
class Trigger:
    """A machine-evaluable activation rule over one declared feature."""

    feature: str
    op: Op
    threshold: float
    dwell: int
    arm_after: int
    reducer: Reducer = "value"

    def __post_init__(self) -> None:
        # Same principle as the feature contract: a name that is not in the
        # vocabulary is rejected where it is written, not where it is read.
        # `reduce_series` would raise eventually, but a trigger that only fails
        # once an episode is already running is a worse error than one that
        # cannot be constructed.
        if self.reducer not in REDUCERS:
            raise ValueError(f"unknown reducer {self.reducer!r}; expected one of {REDUCERS}")
        if self.op not in ("lt", "gt"):
            raise ValueError(f"unknown operator {self.op!r}")

    @property
    def privilege(self) -> int:
        return privilege_cost([self.feature])

    def fire_step(self, trace: dict[str, np.ndarray]) -> int | None:
        """First step index at which this trigger fires, or None."""
        series = reduce_series(trace[self.feature], self.reducer)
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
        subject = self.feature if self.reducer == "value" else f"running_{self.reducer}({self.feature})"
        return (f"{subject} {sym} {self.threshold:.5g} for {self.dwell} steps, "
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


#: Minimum real (unheld) samples per group before a step is allowed to carry a verdict.
MIN_GROUP_SAMPLES = 8


def align(series: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Pad variable-length episode traces to a common length by holding the last value.

    Governed episodes are NOT fixed length: a fired recovery splices extra phases
    into the schedule, so a generation-2 population mixes 100-step and 212-step
    episodes. Absolute step index is still the right axis -- a trigger's
    ``arm_after`` is an absolute index -- so traces are right-padded with their
    final value (a settled arm holds its state) and a validity count is returned
    so the tail, where only a few long episodes have real data, can be masked.
    """
    n = max(len(x) for x in series)
    padded = np.stack([
        np.concatenate([x, np.full(n - len(x), x[-1])]) if len(x) < n else x
        for x in series
    ])
    valid = np.zeros(n, dtype=int)
    for x in series:
        valid[: len(x)] += 1
    return padded, valid


def divergence_profile(traces, labels, feature: str) -> np.ndarray:
    """Per-step standardized separation between failing and succeeding episodes.

    Positive means successes read higher. Steps where either group has fewer
    than :data:`MIN_GROUP_SAMPLES` real (unheld) observations are NaN: a tail
    driven by two surviving episodes is noise, not divergence.
    """
    ok, ok_valid = align([t[feature] for t, y in zip(traces, labels) if y])
    bad, bad_valid = align([t[feature] for t, y in zip(traces, labels) if not y])
    n = max(ok.shape[1], bad.shape[1])

    def _fit(arr, valid):
        if arr.shape[1] == n:
            return arr, valid
        pad = n - arr.shape[1]
        return (np.concatenate([arr, np.repeat(arr[:, -1:], pad, axis=1)], axis=1),
                np.concatenate([valid, np.zeros(pad, dtype=int)]))

    ok, ok_valid = _fit(ok, ok_valid)
    bad, bad_valid = _fit(bad, bad_valid)
    pooled = np.sqrt((ok.var(axis=0) + bad.var(axis=0)) / 2) + 1e-9
    prof = (ok.mean(axis=0) - bad.mean(axis=0)) / pooled
    enough = (ok_valid >= min(MIN_GROUP_SAMPLES, ok.shape[0])) & \
             (bad_valid >= min(MIN_GROUP_SAMPLES, bad.shape[0]))
    return np.where(enough, prof, np.nan)


#: Separation a feature must reach for the scan to propose an arming step.
#:
#: This is a GENERATION-time heuristic, not a decision. It exists to pick where
#: to arm a trigger, and a strict value here silently suppresses candidates
#: before anything gets to judge them: at 2.0 the cloned policy produced zero
#: candidates although `observable.eef_z` separates its outcomes at Cohen d =
#: 1.43, a large effect. Filtering belongs downstream, where it is paired,
#: out-of-sample and significance-tested. Kept permissive on purpose.
GENERATION_SIGMA = 1.0


def earliest_divergence(traces, labels, feature: str, sigma: float = GENERATION_SIGMA) -> int | None:
    """First control step where `feature` separates the two outcome groups."""
    prof = np.abs(divergence_profile(traces, labels, feature))
    idx = np.flatnonzero(np.nan_to_num(prof, nan=0.0) >= sigma)
    return int(idx[0]) if idx.size else None


#: Weight on how much lead time a trigger leaves for a repair. ZERO, measured.
#:
#: Hand-picked at 0.25 in round 2 and never calibrated. It encodes an assumption
#: the design later made false: a repair had to fit in the episode's remaining
#: steps back when the recovery program was spliced into the policy's schedule.
#: Since round 17 the recovery OWNS its steps, so a late trigger costs nothing --
#: measured, total env steps peak at 209 against a horizon of 900 and no recovery
#: is ever truncated.
#:
#: Swept over 0 / 0.1 / 0.25 / 0.5 on a three-way split. W=0 selects a different
#: trigger and scores +28.7% on the selection block against +21.3% for every
#: positive weight, and +23.5pp against +18.5pp on a fresh held-out block. The
#: weight was worth -5.0pp.
DEFAULT_EARLINESS = 0.0
#: Weight on false positives. Swept 0 / 0.6 / 1.2 / 2.5 / 5.0 and INSENSITIVE:
#: every value at or above 0.6 selects the same trigger and scores identically
#: (+24.0%, zero regressions, on the selection block). Its one job is to exclude
#: the degenerate candidate -- at 0.0 the search picks `eef_z > 0.839`, recall
#: 1.00 with false-positive rate 1.00, which fires on every episode and scores
#: +1.3% with 36 fixed against 34 broken. That is precisely the blind-firing arm
#: round 4 used as a control (-4.0pp). Any positive value does the job; 1.2 stays.
DEFAULT_FP_PENALTY = 1.2


def search_triggers(
    traces: Sequence[dict[str, np.ndarray]],
    labels: Sequence[bool],
    *,
    privilege_budget: int = 0,
    dwells: Iterable[int] = (1, 2, 3),
    top_k: int = 8,
    min_recall: float = 0.5,
    earliness: float = DEFAULT_EARLINESS,
    fp_penalty: float = DEFAULT_FP_PENALTY,
) -> list[TriggerScore]:
    """Rank triggers by detection quality under the privilege budget.

    Only features whose privilege cost fits `privilege_budget` are considered,
    which is what makes a zero-budget campaign structurally unable to discover a
    rule that a real robot could not evaluate.
    """
    labels = np.asarray(labels, dtype=bool)
    if labels.all() or (~labels).any() is False:
        raise ValueError("trigger search needs both successful and failed episodes")
    n_steps = max(len(next(iter(t.values()))) for t in traces)
    admissible = [n for n, f in REGISTRY.items()
                  if (0 if f.privilege is Privilege.OBSERVABLE else 1) <= privilege_budget]
    out: list[TriggerScore] = []
    for feature in sorted(admissible):
      for reducer in REDUCERS:
        reduced = [{feature: reduce_series(t[feature], reducer)} for t in traces]
        eod = earliest_divergence(reduced, labels, feature)
        if eod is None:
            continue
        traces_r = reduced
        # Arm at or after the divergence: firing before the signal exists is noise.
        arms = sorted({eod, min(eod + 2, n_steps - 1), min(eod + 6, n_steps - 1)})
        values = np.concatenate([t[feature][eod:] for t in traces_r if len(t[feature]) > eod])
        for thr in _quantile_grid(values):
            for op in ("lt", "gt"):
                for dwell in dwells:
                    for arm in arms:
                        trig = Trigger(feature, op, float(thr), int(dwell), int(arm), reducer)
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
                        score = recall - fp_penalty * fpr + earliness * (lead / n_steps)
                        out.append(TriggerScore(trig, recall, fpr, med, lead, score))
    out.sort(key=lambda s: -s.score)
    # keep the best trigger per (feature, op) so the head is not one rule's neighbours
    seen: set[tuple[str, str, str]] = set()
    deduped: list[TriggerScore] = []
    for s in out:
        key = (s.trigger.feature, s.trigger.op, s.trigger.reducer)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
        if len(deduped) >= top_k:
            break
    return deduped
