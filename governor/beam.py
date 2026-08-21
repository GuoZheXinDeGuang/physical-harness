"""Transitional forwarding shell (L2 rung E). Implementation: :mod:`plugins.rsi.beam`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.rsi.beam"), attr)
