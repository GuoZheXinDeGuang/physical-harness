"""Static skill library: one ``SkillRecordV0`` JSON per skill under
``skill-library/records/<name>.json`` (harness/protocol.py is the schema).

The symbolic contract (args / requires / ensures / clobbers) is embodiment-
neutral; ``bindings[embodiment]`` carries the execution half: ``task_template``
+ ``backend`` for a persistent segment, ``episode`` for a one-rollout node
(the EpisodeSpec kwargs ``plugins.task.workload`` dispatches). A binding with
``implemented: false`` is declared but not planner-visible.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from harness.protocol import TYPES, SkillRecordV0

ROOT = Path(__file__).resolve().parent.parent / "skill-library" / "records"


def load_records(root: Path = ROOT) -> dict[str, SkillRecordV0]:
    out: dict[str, SkillRecordV0] = {}
    for path in sorted(Path(root).glob("*.json")):
        rec = SkillRecordV0.from_dict(json.loads(path.read_text()))
        if rec.name in out:
            raise ValueError(f"duplicate skill record {rec.name!r}")
        bad = {k: t for k, t in rec.args.items() if t not in TYPES}
        if bad:
            raise ValueError(f"skill {rec.name!r} args have unknown types {bad}")
        out[rec.name] = rec
    if not out:
        raise ValueError(f"no skill records under {root}")
    return out


def _binding(rec: SkillRecordV0, embodiment: str) -> dict[str, Any] | None:
    b = rec.bindings.get(embodiment)
    return b if b and b.get("implemented", True) else None


def select(records: Mapping[str, SkillRecordV0], embodiment: str,
           names: Iterable[str]) -> dict[str, SkillRecordV0]:
    """The planner-visible subset: every name must carry an implemented binding."""
    out = {}
    for name in names:
        if name not in records:
            raise KeyError(f"unknown skill record {name!r}")
        if _binding(records[name], embodiment) is None:
            raise ValueError(f"skill {name!r} has no implemented {embodiment!r} binding")
        out[name] = records[name]
    return out


def catalogue_of(records: Mapping[str, SkillRecordV0]) -> dict[str, dict[str, type]]:
    """The ``{skill: {arg: python type}}`` shape validate_plan / planner briefs use."""
    return {name: {k: TYPES[t] for k, t in rec.args.items()}
            for name, rec in records.items()}


def planner_docs(records: Mapping[str, SkillRecordV0]) -> dict[str, dict[str, Any]]:
    return {name: {"description": rec.description, "kind": rec.kind,
                   "arguments": dict(rec.args), "requires": list(rec.requires),
                   "ensures": list(rec.ensures), "clobbers": list(rec.clobbers)}
            for name, rec in records.items()}


def segment_specs(records: Mapping[str, SkillRecordV0], embodiment: str
                  ) -> dict[str, dict[str, Any]]:
    """``{skill: {task_template}}`` for the persistent-segment bindings (fresh dicts:
    mission cards add their ``allowed_args`` grounding on top)."""
    out = {}
    for name, rec in records.items():
        b = _binding(rec, embodiment)
        if b and "task_template" in b:
            out[name] = {"task_template": b["task_template"]}
    return out


def skill_specs(records: Mapping[str, SkillRecordV0], embodiment: str
                ) -> dict[str, dict[str, Any]]:
    """``{skill: EpisodeSpec kwargs}`` for the one-rollout bindings."""
    return {name: dict(b["episode"]) for name, rec in records.items()
            if (b := _binding(rec, embodiment)) and "episode" in b}


RECORDS = load_records()
