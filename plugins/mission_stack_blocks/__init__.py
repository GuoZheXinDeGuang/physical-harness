"""Minimal VLM graph task: pick cubeA, then place it on cubeB."""

from __future__ import annotations

from typing import Any

from harness.skill_library import RECORDS, catalogue_of, planner_docs, segment_specs, select

SKILLS = ("grasp", "place_on")
OBJECT = "cubeA"
TARGET = "cubeB"

_RECORDS = select(RECORDS, "robosuite", SKILLS)
CATALOGUE = catalogue_of(_RECORDS)
SKILL_DOCS = planner_docs(_RECORDS)
ORACLES = ("segment_success",)

DEFAULT_INSTRUCTION = (
    "Stack cubeA on top of cubeB. First grasp cubeA, then place cubeA "
    "stably on cubeB. Use only the available skills."
)

PLANNING_CONTEXT = {
    "benchmark": "robosuite",
    "scene": "Stack",
    "objects": [OBJECT],
    "supports": [TARGET],
    "target_by_object": {OBJECT: TARGET},
    "required_per_object_order": ["grasp", "place_on"],
}

EPISODE: dict[str, Any] = {
    "task": "stack",
    "percept_noise": 0.0,
    "terminal_label": True,
    "horizon": 600,
}

SEGMENT_SPECS = segment_specs(_RECORDS, "robosuite")
for _name, _spec in SEGMENT_SPECS.items():
    _spec["allowed_args"] = {"object": (OBJECT,)}
    if "target" in CATALOGUE[_name]:
        _spec["allowed_args"]["target"] = (TARGET,)
        _spec["target_by_object"] = {OBJECT: TARGET}
