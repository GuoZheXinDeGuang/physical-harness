"""VLM-planned pack-all mission over the shared static skill library.

The graph uses embodiment-neutral names.  ``SEGMENT_SPECS`` is the only place
those names become RoboCasa's private ``nav_hot0`` / ``grasp_hot0`` / ... stage
names, so changing benchmark controllers does not change the planner contract.
"""

from __future__ import annotations

from typing import Any

from harness.skill_library import RECORDS, catalogue_of, planner_docs, segment_specs, select

SKILLS = ("navigate", "grasp", "carry", "place")
ITEMS = ("hot1", "hot0", "cold0", "cold1")
TARGET_BY_OBJECT = {
    "hot0": "tupperware0",
    "hot1": "tupperware0",
    "cold0": "tupperware1",
    "cold1": "tupperware1",
}

_RECORDS = select(RECORDS, "robocasa", SKILLS)
CATALOGUE = catalogue_of(_RECORDS)
SKILL_DOCS = planner_docs(_RECORDS)
ORACLES = ("segment_success",)

DEFAULT_INSTRUCTION = (
    "Pack every food item into its assigned tupperware. Do one item at a time: "
    "navigate to it, grasp it, carry it to its assigned target, then place it "
    "inside."
)

# Static task-scene grounding. A future perception node can replace the object
# list, but the VLM still receives the same schema and the dispatch guard still
# checks every argument before actuation.
PLANNING_CONTEXT = {
    "benchmark": "robocasa",
    "scene": "PackFoodByTemp",
    "objects": list(ITEMS),
    "receptacles": ["tupperware0", "tupperware1"],
    "target_by_object": dict(TARGET_BY_OBJECT),
    "required_per_object_order": [
        "navigate", "grasp", "carry", "place"
    ],
}

EPISODE: dict[str, Any] = {
    "task": "pack_all_robocasa",
    "percept_noise": 0.012,
    "percept_provider": "plugins.embodiment_robocasa.percept:provider",
    "horizon": 8000,
}

SEGMENT_SPECS = segment_specs(_RECORDS, "robocasa")
for _name, _spec in SEGMENT_SPECS.items():
    _spec["allowed_args"] = {"object": ITEMS}
    if "target" in CATALOGUE[_name]:
        _spec["allowed_args"]["target"] = tuple(sorted(set(TARGET_BY_OBJECT.values())))
        _spec["target_by_object"] = TARGET_BY_OBJECT
