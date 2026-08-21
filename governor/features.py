"""Transitional forwarding shell (L2 rung F).

Machinery: :mod:`harness.features`. The robosuite feature DECLARATION lives in
:mod:`plugins.embodiment_robosuite.features`; importing this shell keeps the
legacy no-ref path populated exactly as before.
"""

import plugins.embodiment_robosuite.features  # noqa: F401  legacy-path population


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("harness.features"), attr)
