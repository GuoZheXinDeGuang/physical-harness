"""Small same-counter RoboCasa scene for the first VLM skill-graph smoke.

Three compact objects and one basket spawn on the robot's initial counter.  The
scene deliberately removes navigation and appliance interaction so the run
isolates the abstract ``pick -> place_in`` composition being tested.
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
            "work_counter", {"id": FixtureType.COUNTER})
        self.init_robot_base_ref = self.work_counter

    def get_ep_meta(self):
        meta = super().get_ep_meta()
        meta["lang"] = "Put item0, item1, and item2 into the basket."
        return meta

    def _get_obj_cfgs(self):
        # Four non-overlapping patches on one counter, all at the initial dock.
        return [
            {"name": "item0", "obj_groups": "apple", "graspable": True,
             "placement": {"fixture": self.work_counter, "size": (0.14, 0.14),
                           "pos": (-0.42, -1.0)}},
            {"name": "item1", "obj_groups": "lemon", "graspable": True,
             "placement": {"fixture": self.work_counter, "size": (0.14, 0.14),
                           "pos": (-0.14, -1.0)}},
            {"name": "item2", "obj_groups": "tomato", "graspable": True,
             "placement": {"fixture": self.work_counter, "size": (0.14, 0.14),
                           "pos": (0.14, -1.0)}},
            {"name": "basket", "obj_groups": "basket",
             "placement": {"fixture": self.work_counter, "size": (0.24, 0.24),
                           "pos": (0.42, -1.0)}},
        ]

    def _check_success(self):
        return bool(
            all(OU.check_obj_in_receptacle(self, item, "basket") for item in ITEMS)
            and all(OU.gripper_obj_far(self, obj_name=item) for item in ITEMS)
        )
