"""Every mission card's ``[task_bindings.<task>].skills`` line is the data twin of
its planner's ``SKILL_RECORDS`` (a Python attribute board/ must not import): the
vault's USES / COVERS / EVIDENCED_ON edges read the line, this test pins it."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARDS = sorted(p.parent for p in (ROOT / "plugins").glob("mission_*/manifest.toml"))


@pytest.mark.parametrize("card", CARDS, ids=[c.name for c in CARDS])
def test_manifest_skills_line_equals_planner_skill_records(card):
    for task, binding in tomllib.loads((card / "manifest.toml").read_text())["task_bindings"].items():
        if "records" not in binding:
            assert "skills" not in binding, f"{card.name}.{task}: skills without records"
            continue
        module, attr = binding["records"].split(":", 1)
        records = getattr(importlib.import_module(module), attr)
        assert binding.get("skills") == sorted(records), f"{card.name}.{task}: manifest skills drifted"
