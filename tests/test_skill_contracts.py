"""The §2b interface pass: Skill / SkillLibrary describe EXISTING shapes.

One pin: the skills_root store (plugins/graphs.InMemorySkillGraph -- the
graph.skill mount) satisfies SkillLibrary (protocol inheritance over
SkillGraph must keep runtime isinstance working), and the Skill protocol's
attribute triple matches what the card tables actually carry -- a stand-in
built from a real CATALOGUE row + SKILL_SPECS row conforms.
"""

from __future__ import annotations

from types import SimpleNamespace

from harness import contracts
from plugins.graphs import InMemorySkillGraph
from plugins.task.planner_stack import CATALOGUE
from plugins.task.workload import SKILL_SPECS


def test_skill_library_and_skill_describe_the_existing_shapes():
    lib = InMemorySkillGraph()
    assert isinstance(lib, contracts.SkillLibrary)
    assert isinstance(lib, contracts.SkillGraph)  # same mount contract

    # the two halves the Skill protocol names, joined from the real tables
    skill = SimpleNamespace(name="stack", args=CATALOGUE["stack"],
                            binding=SKILL_SPECS["stack"])
    assert isinstance(skill, contracts.Skill)
