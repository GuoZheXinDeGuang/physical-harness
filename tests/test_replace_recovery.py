"""Round 90 rung 1: the `replace` recovery -- a place-shaped repair.

All fakes, no robosuite: the RecoveryActor and the drivers act on hand-built obs
dicts, and place_estimate is pure. The one non-negotiable is parity -- the sealed
grasp strategies replay byte-for-byte -- so the first test pins a regrasp program
against a golden captured before the change.
"""

import hashlib
import json

import numpy as np
import pytest

from harness.spec import STACK_PHASE_HEIGHT, STACK_SCHEDULE, EpisodeSpec

# Needs the embodiment card's onboard percept; skip when it is unplugged (R2).
pytest.importorskip("plugins.embodiment_robosuite.percept")

from plugins.embodiment_robosuite.percept import OnboardPercept
from plugins.policies.drivers import StackScriptedDriver
from plugins.rsi.governed import _is_place_recovery, _percept_object, _place_object
from plugins.rsi.recovery import RecoveryActor
from plugins.rsi.repertoire import strategy


# --- PARITY PIN --------------------------------------------------------------
def test_regrasp_replay_is_byte_identical():
    """A regrasp program replays exactly as before the height-merge/grip-set
    additions. Golden captured against the pre-change RecoveryActor with a fixed
    obs, so a drift in either sealed path fails here loudly."""
    target = np.array([0.11, -0.07, 0.83])
    obs = {"robot0_eef_pos": np.array([0.05, 0.02, 0.95])}
    a = RecoveryActor(strategy("regrasp").steps, target, height_offset=-0.01)
    acts = []
    while not a.done:
        acts.append([round(float(x), 9) for x in a.act(obs)])
    assert len(acts) == 111
    assert (hashlib.sha256(json.dumps(acts).encode()).hexdigest()
            == "31423e3f78c88b91fbb26b49764f35d7f3417a1660db351f59c1607d4ebc7c17")


# --- Blocker 1: grip stays closed through the place phases --------------------
def test_grip_resolves_per_phase_name():
    """over_b/place hold the cube (grip closed); release/retreat open it; the
    grasp vocabulary is unchanged. Mirrors StackScriptedDriver._GRIP_CLOSED."""
    prog = tuple((ph, 1, 0.0, 0.0) for ph in
                 ("lift", "over_b", "place", "release", "retreat"))
    a = RecoveryActor(prog, np.array([0.2, 0.1, 0.83]))
    obs = {"robot0_eef_pos": np.array([0.0, 0.0, 0.0])}
    grips = [float(a.act(obs)[-1]) for _ in range(5)]
    assert grips == [1.0, 1.0, 1.0, -1.0, -1.0]


# --- Blocker 2: place-phase heights resolve ----------------------------------
def test_height_resolves_from_stack_vocabulary():
    """Each place phase drives the eef to target_z + STACK_PHASE_HEIGHT[phase].
    eef is parked one cm under the goal so the vertical delta stays unclamped and
    the commanded goal_z can be read straight back out of the action."""
    for phase, h in STACK_PHASE_HEIGHT.items():
        target = np.array([0.0, 0.0, 0.83])
        goal_z = target[2] + h
        obs = {"robot0_eef_pos": np.array([0.0, 0.0, goal_z - 0.01])}
        a = RecoveryActor(((phase, 1, 0.0, 0.0),), target, height_offset=-0.01)
        act = a.act(obs)
        recovered = obs["robot0_eef_pos"][2] + act[2] / 8.0
        assert abs(recovered - goal_z) < 1e-9, f"{phase}: {recovered} != {goal_z}"


# --- Blocker 3a: the place goal is a fresh, independent cubeB estimate --------
def test_place_estimate_independent_deterministic_z_exact():
    est = OnboardPercept()
    obs = {"cubeA_pos": np.array([0.0, 0.0, 0.82]),
           "cubeB_pos": np.array([0.1, 0.1, 0.83])}
    spec = EpisodeSpec(seed=7, task="stack")

    b1 = est.place_estimate(obs, spec, 0.02, 1)
    b2 = est.place_estimate(obs, spec, 0.02, 1)
    assert np.array_equal(b1, b2), "same (seed, draw) must reproduce"
    assert not np.array_equal(b1, est.place_estimate(obs, spec, 0.02, 2)), "a new draw must move"
    assert b1[2] == 0.83, "z is never perturbed"
    assert np.array_equal(est.place_estimate(obs, spec, 0.0, 1), obs["cubeB_pos"]), "sd=0 is truth"

    # Independent of the grasp draw: same seed/draw, different object, different noise.
    a1 = est.object_estimate(obs, spec, 0.02, 1)  # cubeA
    assert not np.allclose(a1 - obs["cubeA_pos"], b1 - obs["cubeB_pos"]), "grasp/place draws not independent"


# --- Blocker 3b: threading + shape detection ---------------------------------
def test_place_goal_threading_and_shape_detection():
    assert _is_place_recovery(strategy("replace").steps)
    for grasp in ("regrasp", "settle", "lateral", "high_reset", "servo_regrasp", "probe_regrasp"):
        assert not _is_place_recovery(strategy(grasp).steps), grasp

    obs = {"cubeA_pos": np.array([0.0, 0.0, 0.82]),
           "cubeB_pos": np.array([0.1, 0.1, 0.83])}
    spec = EpisodeSpec(seed=7, task="stack")
    goal = _place_object(obs, spec, 0.02, 1)
    assert goal[2] == 0.83 and not np.allclose(goal[:2], [0.1, 0.1])  # cubeB, z exact, xy noised
    assert not np.allclose(goal, _percept_object(obs, spec, 0.02, 1))  # not the cubeA goal


# --- Blocker 3c: on_handback resume mapping ----------------------------------
def _cum(phase):
    acc = 0
    for name, dur in STACK_SCHEDULE:
        if name == phase:
            return acc
        acc += dur
    raise KeyError(phase)


def test_place_recovery_handback_resumes_at_retreat_never_replaces():
    """A replace ran the whole place half itself, so however deep into that half
    the critic fired, the driver must resume at `retreat` -- past place -- not
    re-place a seated cube."""
    retreat = _cum("retreat")
    goal = np.array([0.1, 0.1, 0.83])
    for fired_k in (_cum("lift") + 5, _cum("over_b") + 3, _cum("place") + 10):
        d = StackScriptedDriver(EpisodeSpec(seed=3, task="stack"))
        d.k = fired_k
        d.retarget_place(goal)
        assert np.array_equal(d.target_b, goal) and d._place_recovery
        d.on_handback()
        assert d.k == retreat, f"fired at {fired_k}: resumed {d.k}, expected {retreat}"
        assert not d._place_recovery


def test_grasp_recovery_handback_still_supersedes_one_phase():
    """The unchanged path: a grasp recovery (retarget, not retarget_place)
    supersedes only the interrupted phase."""
    d = StackScriptedDriver(EpisodeSpec(seed=3, task="stack"))
    d.k = _cum("descend") + 4
    d.retarget(np.array([0.0, 0.0, 0.82]))
    assert not d._place_recovery
    d.on_handback()
    assert d.k == _cum("close")  # end of descend == start of close
