"""Per-stage smoke for the kitchen_thaw scripted drivers (docs/project-documentation.md
§5.3 "驱动", §5.5 phase 3). Each test drives ONE stage from a seeded reset -- using an
upstream driver or a teleport scaffold where a prior stage is furniture- or
carry-blocked -- and asserts THAT stage's own robocasa live-state predicate alone.

Runs only in the robocasa venv (`pytest -m robocasa`, cwd=repo so the sys.path
namespace trap never fires -- install report §1.5). In the harness .venv robocasa
is unimportable and the conftest hook auto-skips every test here (the extra
base-lane skips are captured in docs/project-documentation.md §3).

GREEN = what the fixed fixed-base scripted drivers actually do (re-measured this
venv, NOT trusted from the prior agent's notes). XFAIL(strict=False) = honest
failure surfaces with the MEASURED cause in the reason string -- reach/furniture
limits of a fixed-base OSC push, never faked green. Per-stage numbers, seeds and
wall-clock live in local-archive/robocasa-adapt/phase3.md. Success RATE is not the
milestone here (that is the RSI campaign's job, docs/project-documentation.md §5.4).
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
    """Scaffold the 'robot at fixture dock' precondition the aisle-blocked nav
    cannot reach (robocasa's own set_robot_to_position; xy only, spawn yaw kept)."""
    from robocasa.utils.env_utils import (
        compute_robot_base_placement_pose,
        set_robot_to_position,
    )

    pos, _ = compute_robot_base_placement_pose(env, fixture)
    set_robot_to_position(env, np.asarray(pos))
    for _ in range(20):  # let physics settle at the new base pose
        env.step(D._arm_action(env, D._eef(env), D.GRIP_OPEN, kp=0.0))


# ---- action-vector contract: shape / clip / mode+grip channels --------------

@pytest.mark.robocasa
def test_driver_action_vectors_shape_and_clip():
    """Both action primitives emit a well-formed 12-vector: clipped to the
    controller box [-1, 1], a far goal saturates the driven channels, and the
    base_mode / gripper channels carry the right sign (the axis signs themselves
    are re-verified live in phase3.md, not here)."""
    env = _env(7)
    try:
        env.reset()
        # arm: a far goal must saturate xyz to +-1; mode<0 (arm); grip passes through
        a = D._arm_action(env, D._eef(env) + np.array([9.0, -9.0, 9.0]), D.GRIP_CLOSE)
        assert a.shape == (D.ADIM,)
        assert np.all(a >= -1.0) and np.all(a <= 1.0), "arm action escaped [-1,1]"
        assert np.allclose(np.abs(a[0:3]), 1.0), "a far arm goal must saturate xyz"
        assert a[D.MODE] < 0, "arm action must select arm mode (mode<0)"
        assert a[D.GRIP] == D.GRIP_CLOSE, "gripper channel must pass through"
        # base: a far goal saturates vx/vy; mode>0 (base)
        xy, yaw = D._base_pose(env)
        b = D._base_action(env, xy + np.array([9.0, -9.0]), yaw)
        assert b.shape == (D.ADIM,)
        assert np.all(b >= -1.0) and np.all(b <= 1.0), "base action escaped [-1,1]"
        assert np.allclose(np.abs(b[7:9]), 1.0), "a far base goal must saturate vx/vy"
        assert b[D.MODE] > 0, "base action must select base mode (mode>0)"
    finally:
        env.close()


# ---- GREEN: stages a fixed-base scripted P-controller does solve ------------

@pytest.mark.robocasa
@pytest.mark.parametrize("seed", [4, 5])
def test_nav_returns_to_dock(seed):
    """The base velocity servo (Rz(psi) frame, axis-verified live) re-docks at a
    fixture over a CLEAR path. The robot spawns docked at the fridge, so we shove
    it into the open aisle and navigate back -- proving the controller in
    isolation from the cross-appliance furniture block that defeats the
    fridge->microwave leg (that block is the aisle xfail below)."""
    env = _env(seed)
    try:
        env.reset()
        dock_xy, _ = D._base_pose(env)          # spawn == fridge dock
        for _ in range(40):                     # push straight back into the aisle
            a = D._zero(); a[7] = -0.6; a[D.MODE] = D.GRIP_CLOSE
            env.step(a)
        assert np.linalg.norm(D._base_pose(env)[0] - dock_xy) > 0.3, "did not displace"
        done, steps, _ = D.run_stage(env, D.NavigateDriver("fridge"), 400)
        assert done, f"nav did not re-dock (seed {seed}, {steps} steps)"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.parametrize("seed", [11, 100007])
def test_grasp_meat_from_fridge(seed):
    """From reset (robot spawns docked at the fridge, door pre-open), GraspDriver
    torso-lifts and base-aligns to the shelf, hovers over the meat, aligns the
    finger-opening axis across its bbox MINOR axis, descends and closes in
    place, retrying on a fixed schedule when the lift disproves the enclosure
    -- done() demands a SECURE grasp: check_obj_grasped plus the object risen
    SECURE_DZ off its entry z (carry-probe: the bare latch is a false positive
    that fires with the fingers wide open merely touching the object).
    Measured (capability-r1): seed 11 secures on retry attempt 2 (~480 steps);
    seed 100007 on attempt 0 (~148 steps) off a HIGH z=1.45 shelf the old
    fixed z~1.0 standoff could never reach."""
    env = _env(seed)
    try:
        env.reset()
        done, steps, _ = D.run_stage(env, D.GraspDriver("meat"), 900)
        assert done, f"grasp failed (seed {seed}, {steps} steps)"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.xfail(reason="seeds 4/5: no attempt of the retry schedule (over/"
                          "over_end x yaw styles) achieves a secure enclosure "
                          "on these scenes -- the fingers latch check_obj_"
                          "grasped's false positive or graze without the meat "
                          "ever riding up (measured through capability-r1: "
                          "attempts exhaust with fingers ~0.04 == open or "
                          "~0.0005 == closed empty). Enclosure here needs a "
                          "grasp policy (RSI), not another schedule entry.",
                   strict=False)
@pytest.mark.parametrize("seed", [4, 5])
def test_grasp_secure_on_false_latch_scenes(seed):
    env = _env(seed)
    try:
        env.reset()
        done, steps, _ = D.run_stage(env, D.GraspDriver("meat"), 900)
        assert done, f"no secure grasp (seed {seed}, {steps} steps)"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.parametrize("seed", [100007])
def test_carry_transport_survives(seed):
    """After a SECURE grasp, the loaded staged-stow -> back-out-free arc-drive
    -> counter-sweep -> standoff/stall-arrival transport reaches the microwave
    with the meat still IN HAND, where the old direct base servo stripped the
    cargo within ~10 steps. Held is asserted RELATIONALLY (meat still travels
    with the eef) -- check_obj_grasped is the known false-positive latch.

    One seed is a smoke, not a rate. Re-measured on 30 scratch seeds that reach
    a REAL grasp on --split train (tuning 430001-430040, held-out 431001-431040,
    disjoint), same criterion as the assert below -- leg done AND meat still
    with the eef: 10/30 = 33% before the VCAP / back-out / CARRY_Z rework,
    12/30 = 40% after, with the held-out half unchanged at 6/16. capability-r1's
    52% was measured on an UNDECLARED scene split and does not reproduce on
    train. Most non-arrivals are jams, not timeouts (see NavigateDriver)."""
    env = _env(seed)
    try:
        env.reset()
        assert D.run_stage(env, D.GraspDriver("meat"), 900)[0], "precondition grasp"
        done, steps, _ = D.run_stage(env, D.NavigateDriver("microwave", carry=True), 450)
        assert done, f"loaded transport did not reach the standoff ({steps} steps)"
        gap = float(np.linalg.norm(D._obj_pos(env, "meat") - D._eef(env)))
        assert gap < 0.15, f"cargo dropped in transport (eef-meat gap {gap:.3f} m)"
    finally:
        env.close()


# ---- XFAIL: honest failure surfaces (measured cause in the reason) ----------

@pytest.mark.robocasa
@pytest.mark.xfail(reason="nav fridge->microwave: the open side-by-side fridge "
                          "blocks the aisle at y~-3.99; the straight velocity servo "
                          "(no path planning, docs/project-documentation.md §5.3) stalls "
                          "~0.70 m short of the microwave dock.",
                   strict=False)
@pytest.mark.parametrize("seed", [7])
def test_nav_to_microwave_reaches_dock(seed):
    env = _env(seed)
    try:
        env.reset()
        done, _, _ = D.run_stage(env, D.NavigateDriver("microwave"), 400)
        assert done, "base did not reach the microwave dock"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.xfail(reason="place from the carry standoff (the precondition the "
                          "carry transport makes reachable, seed 100007) "
                          "LOSES the meat on the way to the interior -- the "
                          "arm-only ferry across the last 0.65-0.85 m (the "
                          "loaded standoff/stall-arrival band) + the cavity "
                          "entry is the next frontier (carry-probe.md, "
                          "capability-r1.md).",
                   strict=False)
@pytest.mark.parametrize("seed", [100007])
def test_place_meat_in_microwave(seed):
    import robocasa.utils.object_utils as OU

    env = _env(seed)
    try:
        env.reset()
        assert D.run_stage(env, D.GraspDriver("meat"), 900)[0], "precondition grasp failed"
        assert D.run_stage(env, D.NavigateDriver("microwave", carry=True), 450)[0], \
            "precondition carry failed"
        D.run_stage(env, D.PlaceDriver("meat", "microwave"), 300)
        assert OU.obj_inside_of(env, "meat", env.microwave) and \
            OU.gripper_obj_far(env, obj_name="meat"), "meat not placed inside the microwave"
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.xfail(reason="close microwave door: at reset the door is wide open and "
                          "its handle swings to x~-1.32, BEHIND the forward-facing "
                          "arm at the dock -- a straight OSC push cannot reach the "
                          "handle or arc the hinge shut (needs whole-body/RSI).",
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
@pytest.mark.parametrize("seed", [7])
def test_press_start_reaches_button(seed):
    """The press driver reaches the microwave start button and makes gripper
    contact (door open) -- the signal Microwave.update_state latches on. LATCHING
    turned_on additionally needs the door CLOSED, which close-door cannot achieve
    here (handle unreachable, see that xfail), so the full press-start stage
    predicate (microwave_on) stays out of reach; this asserts the reachable half:
    button contact. Re-measured live: contact IS made from the dock with the door
    open (the prior 'never contacts' note was wrong -- phase3.md)."""
    env = _env(seed)
    try:
        env.reset()
        _teleport_to_dock(env, env.microwave)
        gripper = env.robots[0].gripper["right"]
        contact_geom = f"{env.microwave.name}_start_button"
        btn_geom = f"{env.microwave.naming_prefix}start_button"
        for _ in range(200):
            env.step(D._arm_action(env, D._geom_pos(env, btn_geom), D.GRIP_CLOSE, kp=8.0))
            if env.check_contact(gripper, contact_geom):
                break
        assert env.check_contact(gripper, contact_geom), "no button contact"
    finally:
        env.close()
