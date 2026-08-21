"""Transitional forwarding shell (L2 rung F). Implementation: :mod:`harness.percept`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("harness.percept"), attr)
