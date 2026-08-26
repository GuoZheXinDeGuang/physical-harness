"""The kitchen_thaw episode horizon must cover the chain it has to drive.

Calibration r2 (2026-08-26, block 52150-52299) measured what happens when it does
not: the mission's horizon (2000) had fallen BELOW the six segment caps summed
(2350) after capability-r1 widened the grasp cap 260->900, so 110/150 episodes
died on the clock rather than on the policy -- tripping the generic RSI gate's
``c3_budget_exhaust_dominant`` and contaminating the first-death attribution
``c4_attribution`` reads. Raising it to 4000 dropped horizon-exhaust to 1/150.

The mission card cannot import the driver card (plugin boundary,
tests/test_boundaries.py), so its ``_NOMINAL_STEPS`` is a written-down copy of a
number that lives elsewhere. A test may import both, so the two halves are pinned
here -- otherwise the next cap change silently re-opens the same hole.
"""

from __future__ import annotations

from plugins.embodiment_robocasa.kitchen_driver import _STAGES
from plugins.mission_kitchen_thaw.planner import _NOMINAL_STEPS, EPISODE


def test_nominal_steps_tracks_the_driver_caps():
    assert sum(cap for _factory, cap in _STAGES.values()) == _NOMINAL_STEPS


def test_horizon_fits_one_clean_pass():
    assert EPISODE["horizon"] > _NOMINAL_STEPS, (
        "one clean pass through every sub-goal must fit under the horizon before "
        "any retry budget is spent")
