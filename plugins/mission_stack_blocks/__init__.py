"""Minimal VLM graph task: pick cubeA, then place it on cubeB."""

from __future__ import annotations

from typing import Any

from harness.skill_library import LIBRARY

SKILLS = ("pick", "place_on")
OBJECT = "cubeA"
TARGET = "cubeB"

CATALOGUE = LIBRARY.catalogue("robosuite", SKILLS)
SKILL_DOCS = LIBRARY.planner_docs("robosuite", SKILLS)
ORACLES = ("segment_success",)

DEFAULT_INSTRUCTION = (
    "Stack cubeA on top of cubeB. First pick up cubeA, then place cubeA "
    "stably on cubeB. Use only the available skills."
)

PLANNING_CONTEXT = {
    "benchmark": "robosuite",
    "scene": "Stack",
    "objects": [OBJECT],
    "supports": [TARGET],
    "target_by_object": {OBJECT: TARGET},
    "required_per_object_order": ["pick", "place_on"],
}

EPISODE: dict[str, Any] = {
    "task": "stack",
    "percept_noise": 0.0,
    "terminal_label": True,
    "horizon": 600,
}

SEGMENT_SPECS = LIBRARY.segment_specs("robosuite", SKILLS)
for _name, _spec in SEGMENT_SPECS.items():
    _spec["allowed_args"] = {"object": (OBJECT,)}
    if "target" in CATALOGUE[_name]:
        _spec["allowed_args"]["target"] = (TARGET,)
        _spec["target_by_object"] = {OBJECT: TARGET}
