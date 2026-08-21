"""Transitional forwarding shell (L2 rung A).

The implementation lives in :mod:`plugins.rsi.stats.screen` now; this module
survives only so phase 1 call sites and archived pickles keep resolving during
the migration, and it will be deleted with the governor namespace at the end of
L2. PEP 562 lazy forwarding, so import cycles cannot form through it.
"""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.rsi.stats.screen"), attr)
