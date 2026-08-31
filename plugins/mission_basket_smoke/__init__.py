"""Minimal VLM graph task: three same-counter objects -> one basket."""

from __future__ import annotations

from typing import Any

from harness.skill_library import LIBRARY

SKILLS = ("pick", "place_in")
ITEMS = ("item0", "item1", "item2")
TARGET = "basket"

CATALOGUE = LIBRARY.catalogue("robocasa", SKILLS)
SKILL_DOCS = LIBRARY.planner_docs("robocasa", SKILLS)
ORACLES = ("segment_success",)

DEFAULT_INSTRUCTION = (
    "Put every object into the basket. For each object, pick it and then place "
    "it inside the basket. Do not use navigation or transport."
)
PLANNING_CONTEXT = {
    "benchmark": "robocasa",
    "scene": "BasketPackingSmoke",
    "objects": list(ITEMS),
    "receptacles": [TARGET],
    "target_by_object": {item: TARGET for item in ITEMS},
    "required_per_object_order": ["pick", "place_in"],
    "unavailable_skills": ["navigate_to_object", "transport"],
}

EPISODE: dict[str, Any] = {
    "task": "basket_smoke_vlm",
    "percept_noise": 0.012,
    "percept_provider": "plugins.embodiment_robocasa.percept:provider",
    "horizon": 4000,
}

SEGMENT_SPECS = LIBRARY.segment_specs("robocasa", SKILLS)
for _name, _spec in SEGMENT_SPECS.items():
    _spec["allowed_args"] = {"object": ITEMS}
    if "target" in CATALOGUE[_name]:
        _spec["allowed_args"]["target"] = (TARGET,)
        _spec["target_by_object"] = {item: TARGET for item in ITEMS}
