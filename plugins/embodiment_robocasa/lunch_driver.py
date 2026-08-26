"""Composite ``policy.driver`` for the pack_lunch persistent mission
(PackFoodByTemp): two hot items from the stove area + two cold items from the
(already-open) fridge, each fetched nav -> grasp -> carry -> pack into its
temperature's tupperware on the dining counter. All stages ride ``drivers.py`` /
``stage_extras.py`` primitives; the hot->tupperware0 / cold->tupperware1
assignment is the same deterministic function the mission's sort-temp decide
node records (PackFoodByTemp accepts either consistent assignment).

Item homes are the task's OWN placements (PackFoodByTemp._get_obj_cfgs): hot0 on
the stove (in a pan), hot1 on the stove-side counter (on a plate), cold0/cold1
on fridge racks; tupperware0/1 on the dining counter by the stool.
"""

from __future__ import annotations

from typing import Any

from plugins.embodiment_robocasa import drivers as D
from plugins.embodiment_robocasa import stage_extras as X

#: item -> (home fixture, target tupperware). The registered fixture names are
#: PackFoodByTemp._setup_kitchen_references's own attributes.
ITEM_PLAN: dict[str, tuple[str, str]] = {
    "cold0": ("fridge", "tupperware1"),
    "cold1": ("fridge", "tupperware1"),
    "hot0": ("stove", "tupperware0"),
    "hot1": ("counter", "tupperware0"),
}
ITEMS: tuple[str, ...] = tuple(ITEM_PLAN)


def _stages() -> dict[str, tuple[Any, int]]:
    """spec.task -> (stage factory, step cap); caps mirror kitchen_driver's."""
    table: dict[str, tuple[Any, int]] = {}
    for item, (home, tub) in ITEM_PLAN.items():
        table[f"nav_{item}"] = (
            lambda h=home, o=item: X.NavToObjectDriver(h, o), 250)
        table[f"grasp_{item}"] = (lambda o=item: D.GraspDriver(o), 600)
        # loaded leg docks NEAR THE TARGET TUPPERWARE's run of the long dining
        # counter, not the counter's generic dock (ref_object-addressed).
        table[f"carry_{item}"] = (
            lambda t=tub, o=item: X.NavToObjectDriver(
                "dining_counter", t, carry=True), 450)
        table[f"pack_{item}"] = (
            lambda o=item, t=tub: X.ReceptaclePlaceDriver(o, t), 300)
    return table


_STAGES = _stages()


def provider() -> X.CompositePolicies:
    return X.CompositePolicies(_STAGES, "robocasa_pack_lunch@v1")


if __name__ == "__main__":
    assert set(_STAGES) == {f"{p}_{i}" for i in ITEMS
                            for p in ("nav", "grasp", "carry", "pack")}
    drv = provider().make_driver(object())

    class _S:
        task = "pack_cold1"
    drv.enter_segment(object(), _S())
    assert isinstance(drv._stage, X.ReceptaclePlaceDriver)
    assert drv._stage.receptacle == "tupperware1"

    class _S2:
        task = "carry_hot0"
    drv.enter_segment(object(), _S2())
    assert drv._stage.carry and drv._stage.obj_name == "tupperware0"
    print("plugins/embodiment_robocasa/lunch_driver.py self-check OK")
