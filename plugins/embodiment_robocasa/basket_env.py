"""Small same-counter RoboCasa scene for the first VLM skill-graph smoke.

Three compact objects and one basket spawn on the robot's initial counter.  The
scene deliberately removes navigation and appliance interaction so the run
isolates the abstract ``grasp -> place`` composition being tested.
"""

from __future__ import annotations

import robocasa.utils.object_utils as OU
from robocasa.environments.kitchen.kitchen import FixtureType, Kitchen

ITEMS = ("item0", "item1", "item2")


class BasketPackingSmoke(Kitchen):
    """Put all three same-counter objects into ``basket``."""

    def _setup_kitchen_references(self):
        super()._setup_kitchen_references()
        self.work_counter = self.register_fixture_ref(
            "work_counter", {
                "id": FixtureType.COUNTER,
                # Four arm-only manipulation targets need one continuous
                # surface; short joined counter sections are not sufficient.
                "size": (0.70, 0.35),
            })
        self.init_robot_base_ref = self.work_counter

    def get_ep_meta(self):
        meta = super().get_ep_meta()
        meta["lang"] = "Put item0, item1, and item2 into the basket."
        return meta

    def _get_obj_cfgs(self):
        # Keep every object in ONE physical reset region. A Kitchen counter ref
        # can span several joined counter sections; asking each object to sample
        # from that fixture independently let sample_reset_region choose
        # different sections (measured seed 424246: item0 -> basket was 1.326m).
        # ``reuse_region_from`` pins all four samplers to the basket's chosen
        # section, while metre-valued ``offset`` gives a compact ~0.30m cluster
        # independent of that section's length. Each sampling patch follows the
        # selected object's actual footprint instead of assuming every random
        # basket / fruit instance fits a hard-coded patch size. ``pos`` remains
        # at the front edge so the cluster is reachable from the anchored base.
        shared = {"fixture": self.work_counter, "pos": (0, -1.0)}
        return [
            {"name": "basket", "obj_groups": "basket",
             "placement": {**shared, "size": ("obj", "obj"),
                           "offset": (0.14, 0.0)}},
            {"name": "item0", "obj_groups": "apple", "graspable": True,
             "placement": {**shared, "size": ("obj", "obj"),
                           "offset": (-0.11, 0.0),
                           "reuse_region_from": "basket"}},
            {"name": "item1", "obj_groups": "lemon", "graspable": True,
             "init_robot_here": True,
             "placement": {**shared, "size": ("obj", "obj"),
                           "offset": (-0.11, 0.10),
                           "reuse_region_from": "basket"}},
            {"name": "item2", "obj_groups": "tomato", "graspable": True,
             "placement": {**shared, "size": ("obj", "obj"),
                           "offset": (-0.11, 0.20),
                           "reuse_region_from": "basket"}},
        ]

    def _check_success(self):
        return bool(
            all(OU.check_obj_in_receptacle(self, item, "basket") for item in ITEMS)
            and all(OU.gripper_obj_far(self, obj_name=item) for item in ITEMS)
        )
