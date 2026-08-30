"""PREDICATES: robocasa's own oracle, wrapped as card-internal predicate factories.

RoboCasa gives every per-step truth for free -- `robocasa.utils.object_utils`
(the `OU` the task classes use) plus each fixture's state API -- so this layer
re-derives NO truth; it only NAMES the primitives the kitchen_thaw mission
verifies against. Each entry in PREDICATES maps a predicate name to a
"module:factory" ref (the load_provider crossing plugin_doctor Tier A resolves,
symmetric with the base's heterogeneous-mission predicate tables). A factory is
zero-arg and returns `pred(env) -> bool`, evaluated against the LIVE robocasa
env. `robocasa` is imported lazily so this module stays base-importable.

The six here cover exactly kitchen_thaw's per-node verifies (MicrowaveThawingFridge,
docs/project-documentation.md §5.4): fridge open, food grasped, food inside the microwave,
microwave door closed, microwave turned on, gripper clear of the food (released).
"""

from __future__ import annotations

# Same-package import (never a sibling plugin): the secure-grasp margin has ONE
# home, GraspDriver.SECURE_DZ, and this module reads it there.
from plugins.embodiment_robocasa.drivers import GraspDriver

#: The graspable food kitchen_thaw transports. MicrowaveThawingFridge registers it
#: under the name "meat" and its own _check_success reads that name, so the free
#: oracle and this card agree on the target with no second source of truth.
FOOD = "meat"


def _ou():
    import robocasa.utils.object_utils as OU
    return OU


def fridge_is_open():
    def pred(env) -> bool:
        return bool(env.fridge.is_open(env))
    return pred


def obj_grasped():
    """The bare LATCH: robocasa's ``check_obj_grasped`` == gripper-object contact
    AND both finger joints under 0.035. No lift, no force, no displacement -- so
    it cannot tell HOLDING from TOUCHING, and a gripper closed around an object
    still resting on the shelf reads True. Audited, not asserted:
    ``scripts/probe_grasp_predicate.py`` reads it True on 7 of 7 constructible
    synthetic controls where the closed gripper sits at the meat's RESTING pose
    with the meat still on its support -- a 100% false-positive rate on the one
    case a grasp verify exists to reject.

    It stays exported because it is a real component -- a precondition ref and
    the first conjunct of the honest check -- but a VERIFY must use
    :func:`obj_grasped_secure` instead (the mission cards all do).
    """
    def pred(env) -> bool:
        return bool(_ou().check_obj_grasped(env, FOOD))
    return pred


def obj_grasped_secure(name: str = FOOD):
    """HOLDING, not touching: the latch AND the object risen off the resting z
    the caller surveyed.

    The margin is ``GraspDriver.SECURE_DZ``, imported rather than copied: this
    card already owns a stricter definition of "the grasp is real" -- the grasp
    segment's own ``done()`` has been SECURE_DZ-gated since the carry probe
    measured that a latch fired while the fingers closed on air -- and a verify
    that disagreed with the driver's own success criterion would be a second
    ruler for one event. 0.08 m is not a threshold chosen here; it is that one,
    reused (0.04 was measured too low: the meat cleared the latch but not the
    shelf lip).

    ``z0`` is an EVALUATION argument, not a factory param: the resting z belongs
    to the episode (kitchen_thaw's survey node seals it as ``meat_pos``), while
    the factory is resolved at mount time, before any env exists.
    """
    def pred(env, z0: float) -> bool:
        return bool(_ou().check_obj_grasped(env, name)
                    and float(env.sim.data.body_xpos[env.obj_body_id[name]][2])
                    > float(z0) + GraspDriver.SECURE_DZ)
    return pred


def obj_in_microwave():
    def pred(env) -> bool:
        return bool(_ou().obj_inside_of(env, FOOD, env.microwave))
    return pred


def microwave_closed():
    def pred(env) -> bool:
        return bool(env.microwave.is_closed(env))
    return pred


def microwave_on():
    def pred(env) -> bool:
        return bool(env.microwave.get_state()["turned_on"])
    return pred


def gripper_food_far():
    def pred(env) -> bool:
        return bool(_ou().gripper_obj_far(env, obj_name=FOOD))
    return pred


# ── parametric primitives (the three composite missions) ─────────────────────
# Same free-oracle wrapping, but the object/fixture is a FACTORY PARAM: a mission
# planner resolves e.g. load_provider(".../predicates:obj_grasped_any",
# {"name": "can1"}) at its own factory time, so a dead ref still reddens at the
# doctor's Tier A. NOT in the PREDICATES table below -- that table is evaluated
# verbatim against a kitchen_thaw env by test_robocasa_card, and these name
# objects that scene does not contain.


def obj_grasped_any(name: str = FOOD):
    def pred(env) -> bool:
        return bool(_ou().check_obj_grasped(env, name))
    return pred


def gripper_far(name: str = FOOD):
    def pred(env) -> bool:
        return bool(_ou().gripper_obj_far(env, obj_name=name))
    return pred


def obj_in_fixture(name: str, fixture: str):
    """Object inside a REGISTERED fixture (env.<fixture>: sink, microwave, ...)."""
    def pred(env) -> bool:
        return bool(_ou().obj_inside_of(env, name, getattr(env, fixture)))
    return pred


def obj_in_receptacle(name: str, receptacle: str):
    """Object inside a receptacle OBJECT (tupperware/pot), not a fixture."""
    def pred(env) -> bool:
        return bool(_ou().check_obj_in_receptacle(env, name, receptacle))
    return pred


def obj_near_fixture(name: str, fixture: str, th: float = 0.25):
    """Object within `th` of a registered fixture's bbox (RecycleSodaCans's own
    stove_threshold reading, exposed per object)."""
    def pred(env) -> bool:
        return bool(_ou().obj_fixture_bbox_min_dist(env, name, getattr(env, fixture)) <= th)
    return pred


def obj_on_counter(name: str):
    def pred(env) -> bool:
        return bool(_ou().check_obj_any_counter_contact(env, name))
    return pred


def base_near_obj(name: str, th: float = 1.5):
    """The mobile base parked within `th` (xy) of the object -- the arrived truth
    for a nav-to-object leg (no fixture dock to compare against)."""
    import numpy as np

    def pred(env) -> bool:
        bid = env.sim.model.body_name2id("mobilebase0_base")
        bxy = np.asarray(env.sim.data.body_xpos[bid])[:2]
        oxy = np.asarray(env.sim.data.body_xpos[env.obj_body_id[name]])[:2]
        return bool(np.linalg.norm(bxy - oxy) <= th)
    return pred


def sink_water(on: bool = True):
    """Sink faucet state (MultistepSteaming's own handle_state reading)."""
    def pred(env) -> bool:
        return bool(env.sink.get_handle_state(env=env)["water_on"]) is on
    return pred


def pot_on_chosen_burner(name: str = "pot"):
    """The pot sits on the episode's chosen burner -- MultistepSteaming's own
    _check_obj_location_on_stove against its sealed knob choice."""
    def pred(env) -> bool:
        return env._check_obj_location_on_stove(name) == env.knob
    return pred


#: name -> "module:factory" ref. plugin_doctor load_provider-resolves every value
#: (a dead ref reddens at 体检, not mid-mission); the kitchen_thaw mission card
#: (phase 4) references this table by ref, never by sibling import.
PREDICATES: dict[str, str] = {
    "fridge_is_open":   "plugins.embodiment_robocasa.predicates:fridge_is_open",
    "obj_grasped":      "plugins.embodiment_robocasa.predicates:obj_grasped",
    "obj_in_microwave": "plugins.embodiment_robocasa.predicates:obj_in_microwave",
    "microwave_closed": "plugins.embodiment_robocasa.predicates:microwave_closed",
    "microwave_on":     "plugins.embodiment_robocasa.predicates:microwave_on",
    "gripper_food_far": "plugins.embodiment_robocasa.predicates:gripper_food_far",
}
