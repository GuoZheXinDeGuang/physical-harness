"""Transitional forwarding shell (L2 rung E). Implementation: :mod:`plugins.rsi.parallel`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.rsi.parallel"), attr)
