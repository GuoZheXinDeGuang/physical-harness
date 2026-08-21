"""Transitional forwarding shell (L2 rung E). Implementation: :mod:`plugins.policies.demos`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.policies.demos"), attr)
