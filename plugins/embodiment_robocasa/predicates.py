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
docs/sim-adaptation.md §3): fridge open, food grasped, food inside the microwave,
microwave door closed, microwave turned on, gripper clear of the food (released).
"""

from __future__ import annotations

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
    def pred(env) -> bool:
        return bool(_ou().check_obj_grasped(env, FOOD))
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
