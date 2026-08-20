"""Out-of-sample screening of trigger candidates."""
import numpy as np
import pytest

from governor.screen import screen, _score_offline
from governor.search import Trigger


def _trace(gap_series):
    return {"observable.finger_gap": np.asarray(gap_series, dtype=float),
            "observable.gripper_effort": np.zeros(len(gap_series)),
            "observable.eef_z": np.linspace(0.9, 1.1, len(gap_series)),
            "observable.joint_speed": np.zeros(len(gap_series))}


def test_score_offline_counts_recall_and_false_positives():
    trig = Trigger("observable.finger_gap", "lt", 0.01, 1, 0)
    traces = [_trace([0.04] * 20), _trace([0.001] * 20)]
    labels = [True, False]
    recall, fp, med = _score_offline(trig, traces, labels)
    assert recall == 1.0 and fp == 0.0 and med == 0.0


def test_screen_demotes_a_candidate_that_only_fits_the_search_half():
    """Even-indexed episodes carry a spurious separation; odd-indexed ones do not."""
    traces, labels = [], []
    for i in range(24):
        failing = i % 4 in (2, 3)   # both halves then carry both classes
        if i % 2 == 0:                       # search half: clean separation
            series = [0.001] * 20 if failing else [0.04] * 20
        else:                                # screen half: the signal is absent
            series = [0.04] * 20
        traces.append(_trace(series))
        labels.append(not failing)
    out = screen(traces, labels, privilege_budget=0, pool=8)
    assert out, "screening produced no candidates"
    best = out[0]
    assert best.out_recall <= best.in_recall
    assert any(s.shrinkage > 0 for s in out), "no candidate lost anything out of sample"


def test_screen_returns_empty_when_a_half_has_one_class():
    traces = [_trace([0.04] * 20) for _ in range(8)]
    assert screen(traces, [True] * 8, privilege_budget=0) == []
