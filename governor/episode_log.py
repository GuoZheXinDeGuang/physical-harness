"""Transitional forwarding shell (L2 rung C).

The implementation lives in :mod:`harness.episode_log` now; deleted with the governor
namespace at the end of L2. PEP 562 lazy forwarding.
"""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("harness.episode_log"), attr)
