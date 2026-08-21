"""Out-of-sample screening for trigger candidates, before they cost real rollouts.

The offline search already ranks candidates without touching the simulator, but
it ranks them on the very episodes it searched, so its ordering is in-sample.
The expensive step is the gate: 2 x N real episodes per candidate. Screening
splits the recorded dev episodes, searches on one half, and re-scores the
survivors on the other half by shadow replay -- an out-of-sample estimate that
costs nothing and can demote a candidate that only fit the search half.

This is the honest version of Zetta's shadow-replay stage
(Zetta-Embodiment/zetta/evolution/shadow_replay.py). It applies to TRIGGERS only:
a recovery changes the trajectory, so a recording cannot say what a different
repair would have done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from plugins.rsi.stats.search import (DEFAULT_EARLINESS, DEFAULT_FP_PENALTY,
                                      Trigger, search_triggers)


@dataclass(frozen=True, slots=True)
class Screened:
    """A candidate with both its in-sample and out-of-sample scores."""

    trigger: Trigger
    in_recall: float
    in_fp: float
    out_recall: float
    out_fp: float
    out_median_fire: float | None
    out_score: float

    @property
    def shrinkage(self) -> float:
        """How much detection quality was lost out of sample. Large means overfit."""
        return (self.in_recall - self.in_fp) - (self.out_recall - self.out_fp)

    def line(self) -> str:
        med = f"{self.out_median_fire:.0f}" if self.out_median_fire is not None else "-"
        return (f"in(r={self.in_recall:.2f},fp={self.in_fp:.2f}) "
                f"out(r={self.out_recall:.2f},fp={self.out_fp:.2f},fire@{med}) "
                f"shrink={self.shrinkage:+.2f}  {self.trigger.describe()}")


def _score_offline(trigger: Trigger, traces, labels) -> tuple[float, float, float | None]:
    """Recall, false-positive rate and median fire step on recorded episodes."""
    fires = [trigger.fire_step(t) for t in traces]
    tp = [f for f, y in zip(fires, labels) if not y and f is not None]
    fp = [f for f, y in zip(fires, labels) if y and f is not None]
    n_bad = sum(1 for y in labels if not y)
    n_ok = sum(1 for y in labels if y)
    return (len(tp) / max(n_bad, 1), len(fp) / max(n_ok, 1),
            float(np.median(tp)) if tp else None)


def screen(
    traces: Sequence[dict], labels: Sequence[bool], *, privilege_budget: int = 0,
    pool: int = 8, holdout_fraction: float = 0.5, n_steps: int | None = None,
    earliness: float = DEFAULT_EARLINESS, fp_penalty: float = DEFAULT_FP_PENALTY,
) -> list[Screened]:
    """Search on one half of the recorded episodes, re-score on the other.

    Returns candidates ranked by their OUT-OF-SAMPLE score, so a rule that only
    fit the search half sinks before anyone spends a rollout on it.
    """
    labels = list(labels)
    idx = np.arange(len(traces))
    cut = int(len(idx) * (1 - holdout_fraction))
    # deterministic interleave rather than a shuffle: both halves then carry the
    # same mix of easy and hard seeds without needing a seeded RNG here.
    search_idx = [i for i in idx if i % 2 == 0][: max(cut, 2)]
    screen_idx = [i for i in idx if i % 2 == 1]
    s_traces = [traces[i] for i in search_idx]
    s_labels = [labels[i] for i in search_idx]
    h_traces = [traces[i] for i in screen_idx]
    h_labels = [labels[i] for i in screen_idx]
    # A split that leaves either half single-class cannot produce an
    # out-of-sample estimate. The caller falls back to in-sample ranking; this
    # is a degenerate split, not an error.
    if not any(s_labels) or all(s_labels) or not any(h_labels) or all(h_labels):
        return []

    candidates = search_triggers(s_traces, s_labels, privilege_budget=privilege_budget,
                                 top_k=pool, earliness=earliness, fp_penalty=fp_penalty)
    steps = n_steps or max(len(next(iter(t.values()))) for t in traces)
    out: list[Screened] = []
    for cand in candidates:
        in_r, in_fp, _ = _score_offline(cand.trigger, s_traces, s_labels)
        o_r, o_fp, o_med = _score_offline(cand.trigger, h_traces, h_labels)
        lead = steps - (o_med if o_med is not None else steps)
        # The out-of-sample rescore must use the SAME objective the search used;
        # these were literals until round 29, so after round 26 lowered the
        # earliness default the screen was still ranking under the old weights.
        out.append(Screened(cand.trigger, in_r, in_fp, o_r, o_fp, o_med,
                            o_r - fp_penalty * o_fp + earliness * (lead / steps)))
    out.sort(key=lambda s: -s.out_score)
    return out
