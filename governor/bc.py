"""Transitional forwarding shell (L2 rung E). Implementation: :mod:`plugins.policies.bc`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("plugins.policies.bc"), attr)
