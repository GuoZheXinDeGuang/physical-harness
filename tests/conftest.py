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


def pytest_collection_modifyitems(config, items):
    if find_spec("robosuite") is not None:
        return
    skip = pytest.mark.skip(
        reason="robosuite unimportable (embodiment_robosuite extra not installed)"
    )
    for item in items:
        if "robosuite" in item.keywords:
            item.add_marker(skip)
