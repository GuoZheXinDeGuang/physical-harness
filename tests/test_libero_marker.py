"""libero marker self-proof.

Runs only in the libero venv (`pytest -m libero`), where the LIBERO namespace
package resolves via its venv-local .pth (see plugins/embodiment_libero/env.py
docstring -- upstream's editable install maps nothing). In the harness .venv
libero is unimportable, so the conftest hook auto-skips this -- the extra
base-lane skip captured in docs/project-documentation.md §3.
"""
import os
import sys

import pytest


@pytest.mark.libero
def test_libero_suite_registry():
    # Importing libero.libero READS (or interactively creates!) a config file;
    # point it at the venv-local one, same default the card's make_env sets.
    os.environ.setdefault("LIBERO_CONFIG_PATH", os.path.join(sys.prefix, ".libero"))
    from libero.libero import benchmark

    suites = benchmark.get_benchmark_dict()
    assert {"libero_spatial", "libero_object", "libero_goal", "libero_10"} <= set(suites)
