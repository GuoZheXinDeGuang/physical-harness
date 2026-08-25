"""robocasa marker self-proof.

Runs only in the robocasa venv (`pytest -m robocasa`), where robocasa +
robosuite-master live. In the harness .venv robocasa is unimportable, so the
conftest hook auto-skips this -- which is exactly what adds the extra base-lane
skip captured in docs/base-gate.md.
"""
import pytest


@pytest.mark.robocasa
def test_kitchen_env_registry():
    from robocasa.environments import ALL_KITCHEN_ENVIRONMENTS

    assert len(list(ALL_KITCHEN_ENVIRONMENTS)) > 300
