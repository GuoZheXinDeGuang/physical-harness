"""Transitional forwarding shell (L2 rung B).

The implementation lives in :mod:`plugins.rsi.campaign` now; this module survives
only so phase 1 call sites keep resolving during the migration, and it is
deleted with the governor namespace at the end of L2. PEP 562 lazy forwarding,
so import cycles cannot form through it.
"""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.rsi.campaign"), attr)
