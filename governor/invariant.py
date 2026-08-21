"""Transitional forwarding shell (L2 rung F). Implementation: :mod:`harness.invariant`."""


def __getattr__(attr):
    import importlib

    return getattr(importlib.import_module("harness.invariant"), attr)
