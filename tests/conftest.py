"""Base/plugin test split (R3, W6 双层测试).

The `robosuite` marker gates every test that drives the embodiment_robosuite
card's mujoco rollout. When robosuite is unimportable (the extra is not
installed), those items auto-skip -- so `pytest -m "not robosuite"` is the base
fast lane on a card-absent machine, and `pytest -m robosuite` self-skips there
instead of erroring. With the card present the marker is inert and every test
runs, so full-suite parity is untouched.
"""
from importlib.util import find_spec

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "robosuite: needs the embodiment_robosuite card (robosuite+mujoco); "
        "auto-skipped when it is unimportable",
    )
    config.addinivalue_line(
        "markers",
        "robocasa: needs the robocasa venv (robocasa+robosuite-master); "
        "auto-skipped when robocasa is unimportable (harness .venv)",
    )


def _auto_skip(items, pkg, marker, reason):
    if find_spec(pkg) is not None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if marker in item.keywords:
            item.add_marker(skip)


def pytest_collection_modifyitems(config, items):
    _auto_skip(
        items, "robosuite", "robosuite",
        "robosuite unimportable (embodiment_robosuite extra not installed)",
    )
    _auto_skip(
        items, "robocasa", "robocasa",
        "robocasa unimportable (robocasa venv only)",
    )
