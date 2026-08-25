"""Scripted, frozen, per-stage drivers for the kitchen_thaw mission (PandaOmron).

RoboCasa's mobile manipulator takes a 12-dim action (install report §3.4):
arm OSC delta (0:6), gripper (6), base vx/vy/wyaw (7:10), torso (10), and a
base_mode switch (11): +1 = base drives / arm follows, -1 = arm mode. These
drivers are the robosuite card's phased-scripted pattern ported to that action:
each is a closed-loop P controller reading LIVE privileged state (base pose, eef
site, object bodies, fixture geoms) -- privileged is fine here, these are the
oracle scripted policies the harness governs, not learned policies under test.

Axes are EMPIRICAL, not copied from any demo (the report warns robosuite-master
re-signed the mobile base's forward axis). Measured in this venv (seed 7) with
SMALL, drift-subtracted deltas -- a saturated 5-step probe reads a rotated/curved
frame off the wheeled base and lies; the clean in-scene reading is:
  * base, base_mode=+1: world velocity = Rz(psi) @ (vx, vy) -- at psi=0, +vx->+X,
    +vy->+Y. +wyaw = +yaw (CCW). Plain base frame, no offset.
  * arm, base_mode=-1: the OSC delta frame is the base frame, world_from_osc =
    Rz(psi): at psi=0 +ax->+X, +ay->+Y, +az->+Z.
  * both are therefore commanded from a world error by Rz(-psi) @ err (xy; z is
    shared). gripper: +1 closes, -1 opens.

Each driver exposes act(env, obs) -> (12,) action and done(env) -> bool. A stage
runs until done() or a step budget (run_stage). Everything is deterministic given
the env's seeded scene: no rng in the controllers.
"""

from __future__ import annotations

import numpy as np

# 12-dim action layout (install report §3.4): arm OSC 0:6, gripper 6,
# base vx/vy/wyaw 7:10, torso 10, base_mode 11.
GRIP = 6
MODE = 11
ADIM = 12

GRIP_CLOSE = 1.0
GRIP_OPEN = -1.0

# Navigate success tolerance == NavigateKitchen._check_success (kitchen_navigate.py).
NAV_POS_TOL = 0.20
NAV_ORI_COS = 0.98


# ---- live-state readers (privileged; scripted-oracle side) -------------------

def _base_pose(env):
    """(x, y) world position and yaw of the mobile base body."""
    import robosuite.utils.transform_utils as T

    bid = env.sim.model.body_name2id("mobilebase0_base")
    p = np.asarray(env.sim.data.body_xpos[bid])
    yaw = float(T.mat2euler(np.asarray(env.sim.data.body_xmat[bid]).reshape(3, 3))[2])
    return p[:2].copy(), yaw


def _fixture(env, name):
    """Resolve a fixture: prefer the task's registered ref (env.microwave /
    env.fridge) over env.get_fixture, whose fuzzy name match returns the
    HousingCabinet for 'microwave' instead of the Microwave itself."""
    fx = getattr(env, name, None)
    return fx if fx is not None else env.get_fixture(name)


def _rot_world_to_base(err_xy, psi):
    """Rz(-psi) @ err_xy: a world xy error expressed in the base/OSC frame."""
    c, s = np.cos(psi), np.sin(psi)
    return np.array([c * err_xy[0] + s * err_xy[1],
                     -s * err_xy[0] + c * err_xy[1]])


def _eef(env):
    """World position of the right-hand eef site."""
    return np.asarray(env.sim.data.site_xpos[env.robots[0].eef_site_id["right"]]).copy()


def _obj_pos(env, name):
    return np.asarray(env.sim.data.body_xpos[env.obj_body_id[name]]).copy()


def _geom_pos(env, geom):
    gid = env.sim.model.geom_name2id(geom)
    return np.asarray(env.sim.data.geom_xpos[gid]).copy()


def _zero():
    a = np.zeros(ADIM)
    return a


# ---- shared primitives -------------------------------------------------------

def _arm_action(env, goal_world, grip, kp=10.0):
    """base_mode=-1 arm action driving the eef toward goal_world (P control).

    World error is rotated into the OSC base frame (Rz(-psi)) so the
    command is axis-correct at any base yaw; scaled by kp and clipped to the
    controller's [-1, 1] (== +-0.05 m/step). Rotation channels stay 0 (the foods
    are small/graspable top-down; wrist yaw is never needed for this mission).
    """
    err = np.asarray(goal_world, float) - _eef(env)
    _, psi = _base_pose(env)
    bxy = _rot_world_to_base(err[:2], psi)
    cmd = np.array([bxy[0], bxy[1], err[2]])
    a = _zero()
    a[0:3] = np.clip(cmd * kp, -1.0, 1.0)
    a[GRIP] = grip
    a[MODE] = GRIP_OPEN  # -1 == arm mode
    return a


def _base_action(env, goal_xy, goal_yaw, grip=GRIP_OPEN, kp=2.5, kyaw=4.0):
    """base_mode=+1 velocity action driving the base toward (goal_xy, goal_yaw).

    World xy error is rotated into the base frame (Rz(-psi)) to command (vx, vy);
    wyaw closes the yaw error. Yaw is held tightly (kyaw > kp) so the velocity
    frame stays fixed while translating -- a wandering yaw curves the path. In
    base mode the arm follows the base, so a carried object stays put ONLY if the
    gripper is commanded closed -- pass grip=GRIP_CLOSE when navigating loaded.
    """
    xy, psi = _base_pose(env)
    vxy = _rot_world_to_base(np.asarray(goal_xy, float) - xy, psi)
    dyaw = (float(goal_yaw) - psi + np.pi) % (2 * np.pi) - np.pi  # wrap to [-pi,pi]
    a = _zero()
    a[7] = np.clip(vxy[0] * kp, -1.0, 1.0)
    a[8] = np.clip(vxy[1] * kp, -1.0, 1.0)
    a[9] = np.clip(dyaw * kyaw, -1.0, 1.0)
    a[GRIP] = grip
    a[MODE] = GRIP_CLOSE  # +1 == base mode
    return a


# ---- stage drivers -----------------------------------------------------------

class NavigateDriver:
    """Drive the base to a fixture's docking pose (compute_robot_base_placement_pose).

    Success mirrors NavigateKitchen: base within NAV_POS_TOL (0.20 m) of the dock
    xy AND facing it (cos(dyaw) >= 0.98). No path planning -- straight velocity
    servo; if furniture blocks the line, that is the honest failure surface.
    """

    def __init__(self, fixture_name, carry=False):
        self.fixture_name = fixture_name
        self.carry = carry          # hold the gripper closed to keep a grasped object
        self._goal = None

    def _target(self, env):
        if self._goal is None:
            from robocasa.utils.env_utils import compute_robot_base_placement_pose

            fx = _fixture(env, self.fixture_name)
            pos, ori = compute_robot_base_placement_pose(env, fx)
            self._goal = (np.asarray(pos[:2], float), float(ori[2]))
        return self._goal

    def act(self, env, obs):
        xy, yaw = self._target(env)
        return _base_action(env, xy, yaw, grip=GRIP_CLOSE if self.carry else GRIP_OPEN)

    def done(self, env):
        (gxy, gyaw), (xy, psi) = self._target(env), _base_pose(env)
        return bool(np.linalg.norm(gxy - xy) <= NAV_POS_TOL
                    and np.cos(gyaw - psi) >= NAV_ORI_COS)


class GraspDriver:
    """Base-align to the arm's reach sweet spot, then a direct diagonal approach
    onto the object -> close -> lift. Done == OU.check_obj_grasped.

    Two things the naive "hover-above then descend" driver got wrong, both forced
    by the mobile manipulator's real workspace (measured this venv, seed 7):

    * The navigate dock is placed for ARRIVAL, not grasping. The PandaOmron's right
      arm is mounted ~0.15 m off base centre, so an object is only in the arm's
      envelope when the base sits at obj_xy - Rz(psi)@[FWD, LAT]; from the fridge
      dock the meat is 0.63 m OUT of reach until the base shifts ~0.14 m laterally
      (workspace scan: reach 0.63 m -> 0.005 m). So grasp first re-parks the base.
      FWD/LAT are robot reach constants (calibration knobs), not scene values --
      tune here if the arm/base geometry changes, not per task.
    * At that extension the arm can only touch the far-forward object on a RISING
      diagonal, so a low hover is itself unreachable and the descent gate never
      opens. Driving straight at the grasp point traces the reachable path.
    """

    FWD = 0.65        # base stands this far in front of the object (arm fwd reach)
    LAT = -0.15       # ... and this far to the arm side (right-arm mount offset)
    ALIGN_TOL = 0.04  # base-park tolerance (P-tail floors ~0.03 on this base)
    GRASP_TOL = 0.045 # eef-to-grasp-point distance that triggers the close
    CLOSE_TICKS = 12  # steps to hold the close before lifting
    LIFT_DZ = 0.20    # how far to raise after closing

    def __init__(self, obj_name):
        self.obj_name = obj_name
        self.phase = "align"
        self._psi = None       # approach yaw, locked at entry (the dock yaw)
        self._ticks = 0
        self._lift_z = None

    def _base_target(self, env):
        m = _obj_pos(env, self.obj_name)
        if self._psi is None:
            self._psi = _base_pose(env)[1]
        c, s = np.cos(self._psi), np.sin(self._psi)
        return m[:2] - np.array([c * self.FWD - s * self.LAT,
                                 s * self.FWD + c * self.LAT])

    def act(self, env, obs):
        m = _obj_pos(env, self.obj_name)
        eef = _eef(env)
        if self.phase == "align":
            tgt = self._base_target(env)
            if np.linalg.norm(_base_pose(env)[0] - tgt) < self.ALIGN_TOL:
                self.phase = "reach"
            else:
                return _base_action(env, tgt, self._psi, grip=GRIP_OPEN, kp=6.0)
        gp = np.array([m[0], m[1], m[2]])   # grasp point == object body
        if self.phase == "reach":
            if np.linalg.norm(gp - eef) < self.GRASP_TOL:
                self.phase = "close"
            return _arm_action(env, gp, GRIP_OPEN)
        if self.phase == "close":
            self._ticks += 1
            if self._ticks > self.CLOSE_TICKS:
                self.phase = "lift"
                self._lift_z = _eef(env)[2] + self.LIFT_DZ
            return _arm_action(env, gp, GRIP_CLOSE)
        # lift
        return _arm_action(env, np.array([eef[0], eef[1], self._lift_z]), GRIP_CLOSE)

    def done(self, env):
        import robocasa.utils.object_utils as OU

        return bool(OU.check_obj_grasped(env, self.obj_name))


class PlaceDriver:
    """Carry the held object over a fixture's interior and release it inside.

    Target is the fixture's interior-site centroid (get_int_sites) so the drop
    lands in the cavity, not on the door. Phases: over -> lower -> release ->
    retreat. Done == OU.obj_inside_of AND gripper released (OU.gripper_obj_far).
    """

    OVER_DZ = 0.10
    RELEASE_TICKS = 6

    def __init__(self, obj_name, fixture_name):
        self.obj_name = obj_name
        self.fixture_name = fixture_name
        self.phase = "over"
        self._ticks = 0
        self._target = None

    def _interior(self, env):
        if self._target is None:
            fx = _fixture(env, self.fixture_name)
            regions = fx.get_int_sites(relative=False)
            pts = np.array([p0 for (p0, px, py, pz) in regions.values()])
            self._target = pts.mean(0)
        return self._target

    def act(self, env, obs):
        c = self._interior(env)
        eef = _eef(env)
        if self.phase == "over":
            goal = np.array([c[0], c[1], c[2] + self.OVER_DZ])
            if np.linalg.norm((eef - goal)[:2]) < 0.03:
                self.phase = "lower"
            return _arm_action(env, goal, GRIP_CLOSE)
        if self.phase == "lower":
            goal = np.array([c[0], c[1], c[2]])
            if eef[2] - c[2] < 0.03:
                self.phase = "release"
            return _arm_action(env, goal, GRIP_CLOSE)
        if self.phase == "release":
            self._ticks += 1
            if self._ticks > self.RELEASE_TICKS:
                self.phase = "retreat"
            return _arm_action(env, np.array([c[0], c[1], c[2] + 0.02]), GRIP_OPEN)
        # retreat: back the eef up and out, gripper open
        goal = np.array([eef[0], eef[1], c[2] + 0.25])
        return _arm_action(env, goal, GRIP_OPEN)

    def done(self, env):
        import robocasa.utils.object_utils as OU

        return bool(OU.obj_inside_of(env, self.obj_name, _fixture(env, self.fixture_name))
                    and OU.gripper_obj_far(env, obj_name=self.obj_name))


class CloseDoorDriver:
    """Push a hinged fixture door shut by pressing on its handle from outside.

    Drives the (open) gripper to the door handle geom and keeps pushing toward
    the closed-door hinge side. Done == fixture.is_closed. Best-effort scripted
    push; if the OSC cannot generate the needed lateral force it is honest failure.
    """

    def __init__(self, fixture_name):
        self.fixture_name = fixture_name

    def act(self, env, obs):
        fx = _fixture(env, self.fixture_name)
        handle = _geom_pos(env, fx.handle_name)   # {prefix}door_handle_main
        body = np.asarray(fx.pos, float)          # fixture centre (hinge closes inward)
        push = handle + 0.15 * (body - handle) / (np.linalg.norm(body - handle) + 1e-9)
        return _arm_action(env, push, GRIP_OPEN, kp=6.0)

    def done(self, env):
        return bool(_fixture(env, self.fixture_name).is_closed(env))


class PressStartDriver:
    """Touch the microwave start-button geom with the gripper to turn it on.

    Microwave.update_state turns _turned_on True while the gripper contacts
    {prefix}start_button and the door is closed. So this holds the (closed)
    gripper against the button geom. Done == microwave turned_on. Requires the
    door already closed (CloseDoorDriver first) -- an open door forces state off.
    """

    def __init__(self, fixture_name="microwave"):
        self.fixture_name = fixture_name

    def act(self, env, obs):
        fx = _fixture(env, self.fixture_name)
        btn = _geom_pos(env, f"{fx.naming_prefix}start_button")
        return _arm_action(env, btn, GRIP_CLOSE, kp=8.0)

    def done(self, env):
        return bool(_fixture(env, self.fixture_name).get_state()["turned_on"])


def run_stage(env, driver, budget, obs=None):
    """Step `driver` until driver.done(env) or `budget` control steps elapse.

    Returns (done, steps, obs). The stage reads live env state, so obs is passed
    through only for drivers that want it; success is judged by done(env).
    """
    if obs is None:
        obs = env._get_observations() if hasattr(env, "_get_observations") else None
    for i in range(budget):
        if driver.done(env):
            return True, i, obs
        action = driver.act(env, obs)
        obs, _, _, _ = env.step(action)
    return driver.done(env), budget, obs
