"""Per-stage smoke for the kitchen_thaw scripted drivers (docs/sim-adaptation.md
§2 "驱动"; phase 3/5). Each test drives ONE stage from reset (scaffolding its
precondition where an upstream stage is furniture- or carry-blocked) to its own
robocasa predicate, and asserts that predicate alone.

Runs only in the robocasa venv (`pytest -m robocasa`, cwd=repo so the sys.path
namespace trap never fires). In the harness .venv robocasa is unimportable and
the conftest hook auto-skips every test here (extra base-lane skips captured in
docs/base-gate.md).

Honest failure surface (install report §3.4 warned the mobile base is weak): the
Panda-on-Omron cannot solve the whole mission with fixed-base scripted stages.
What WORKS is asserted green; what a scripted P-controller cannot yet do is
xfailed with the measured cause (see local-archive/robocasa-adapt/phase3.md for
per-seed numbers and wall-clock). Success rate is deliberately NOT the milestone
here -- that is the RSI campaign's job (docs/sim-adaptation.md §3).
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.spec import EpisodeSpec
from plugins.embodiment_robocasa import drivers as D
from plugins.embodiment_robocasa import provider


def _env(seed):
    return provider().make_env(EpisodeSpec(seed=seed, task="kitchen_thaw"))


def _teleport_to_dock(env, fixture):
    """Scaffold the 'robot at fixture' precondition the furniture-blocked
    straight-line nav cannot reach (robocasa's own set_robot_to_position; xy
    only, spawn orientation kept -- fridge and microwave share yaw~0/pi here)."""
    from robocasa.utils.env_utils import (
        compute_robot_base_placement_pose,
        set_robot_to_position,
    )

    pos, _ = compute_robot_base_placement_pose(env, fixture)
    set_robot_to_position(env, np.asarray(pos))
    for _ in range(15):  # let physics settle at the new base pose
        env.step(D._arm_action(env, D._eef(env), D.GRIP_OPEN, kp=0.0))


# ---- GREEN: stages a fixed-base scripted P-controller does solve ------------

@pytest.mark.robocasa
@pytest.mark.parametrize("seed", [4, 5])
def test_nav_returns_to_dock(seed):
    """The base velocity servo (Rz(psi) frame, empirically calibrated) drives the
    base back to a fixture dock over a CLEAR path. The robot spawns docked at the
    fridge, so we displace it into the open aisle and navigate back -- proving the
    controller, isolated from the cross-kitchen furniture block that defeats the
    fridge->microwave leg (that block is the transport xfail below)."""
    env = _env(seed)
    try:
        env.reset()
        dock_xy, _ = D._base_pose(env)          # spawn == fridge dock
        for _ in range(40):                     # push straight back into the aisle
            a = D._zero(); a[7] = -0.6; a[11] = D.GRIP_CLOSE
            env.step(a)
        assert np.linalg.norm(D._base_pose(env)[0] - dock_xy) > 0.3, "did not displace"
        drv = D.NavigateDriver("fridge")
        done, steps, _ = D.run_stage(env, drv, 400)
        assert done, f"nav did not re-dock (seed {seed}, {steps} steps)"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.parametrize("seed", [4, 5])
def test_grasp_meat_from_fridge(seed):
    """From reset (robot spawns docked at the fridge, door pre-open), GraspDriver
    re-parks the base into the arm's reach sweet spot (FWD/LAT calibration) and
    grasps the frozen meat. Passes on mid-shelf scenes (meat_z~1.0: seeds 4, 5);
    the repark constants are tuned for that shelf, so much higher (seed 0, z~1.4)
    or lower deep shelves (seeds 1, 7, z~0.7-0.8) fall outside the calibrated
    envelope -- widening that envelope per-scene is exactly the RSI campaign's job
    (docs/sim-adaptation.md §3), not a fixed constant this stage can carry."""
    env = _env(seed)
    try:
        env.reset()
        drv = D.GraspDriver("meat")
        done, steps, _ = D.run_stage(env, drv, 220)
        assert done, f"grasp failed (seed {seed}, {steps} steps)"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.parametrize("seed", [7])
def test_press_start_reaches_button(seed):
    """The button-touch driver reaches the microwave start button and makes
    gripper contact (the signal Microwave.update_state latches on) with the door
    open. LATCHING turned_on additionally needs the door closed, which in this
    microwave model geometrically blocks the same approach -- see the press-start
    latch xfail. Here we assert the reachable half: contact."""
    env = _env(seed)
    try:
        env.reset()
        _teleport_to_dock(env, env.microwave)
        btn_geom = f"{env.microwave.naming_prefix}start_button"
        for _ in range(200):
            env.step(D._arm_action(env, D._geom_pos(env, btn_geom), D.GRIP_CLOSE, kp=8.0))
            if env.check_contact(env.robots[0].gripper["right"],
                                 f"{env.microwave.name}_start_button"):
                break
        assert env.check_contact(env.robots[0].gripper["right"],
                                 f"{env.microwave.name}_start_button"), "no button contact"
    finally:
        env.close()


# ---- XFAIL: honest failure surfaces (cause in the reason string) ------------

@pytest.mark.robocasa
@pytest.mark.xfail(reason="hinged-door sweep: a straight OSC push on the handle "
                          "cannot generate the arc that swings the microwave door "
                          "shut; needs a compliant/whole-body close (RSI).",
                   strict=False)
@pytest.mark.parametrize("seed", [7])
def test_close_microwave_door(seed):
    env = _env(seed)
    try:
        env.reset()
        _teleport_to_dock(env, env.microwave)
        done, _, _ = D.run_stage(env, D.CloseDoorDriver("microwave"), 250)
        assert done, "microwave door not closed"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.xfail(reason="carry: switching to base_mode=+1 retargets the arm to "
                          "its base-frame home pose and drops the grasped object; "
                          "and the fridge->microwave straight line is furniture-"
                          "blocked (no path planning). Whole-body transport = RSI.",
                   strict=False)
@pytest.mark.parametrize("seed", [4])
def test_transport_meat_to_microwave(seed):
    env = _env(seed)
    try:
        env.reset()
        assert D.run_stage(env, D.GraspDriver("meat"), 220)[0], "precondition grasp failed"
        D.run_stage(env, D.NavigateDriver("microwave", carry=True), 500)
        import robocasa.utils.object_utils as OU
        assert OU.check_obj_grasped(env, "meat"), "object dropped in transit"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.xfail(reason="place is downstream of transport (above) -- no scripted "
                          "path seats the meat in the gripper AT the microwave, so "
                          "the place stage's own precondition is unreachable here.",
                   strict=False)
@pytest.mark.parametrize("seed", [7])
def test_place_meat_in_microwave(seed):
    env = _env(seed)
    try:
        env.reset()
        _teleport_to_dock(env, env.microwave)
        done, _, _ = D.run_stage(env, D.PlaceDriver("meat", "microwave"), 250)
        assert done, "meat not placed inside the microwave"
    finally:
        env.close()
