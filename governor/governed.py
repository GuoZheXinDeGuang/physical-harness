"""Transitional forwarding shell (L2 rung I). Implementation: :mod:`plugins.rsi.governed`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.rsi.governed"), attr)
