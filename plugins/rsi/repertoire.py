"""A repertoire of recovery strategies, read from the manifest fold.

Round 16 measured where the remaining ground is: of 54 residual dev failures on
the cloned policy, 42 had the critic fire and the repair run and fail anyway.
Detection was not the bottleneck; one scripted regrasp not covering the failure
was. Round 6 had already searched that program's phase DURATIONS and found the
gain did not survive a clean gate -- because durations are not the axis that
matters when the repair is the wrong shape.

A strategy is declared by the EMBODIMENT CARD whose action/phase vocabulary it
speaks -- ``[recoveries.<name>] ref = "module:attr"`` in the card's
manifest.toml, folded by ``harness.manifest.discover`` exactly like
mounts/campaigns (duplicate names are loud). This module resolves the refs and
isinstance-checks each against ``harness.contracts.RecoveryStrategy``; nothing
embodiment-specific is registered here. A card declaring no ``[recoveries.*]``
has no recovery primitives at all, and ``strategies_for`` answers ``[]`` -- the
answer the RSI chain reports verbatim ("this embodiment has no registered
recovery primitive") rather than substituting a program written for a different
robot.

That declaration is the honest boundary, and it exists because the obvious
cheaper test is WRONG: ``plugins/embodiment_robocasa/kitchen_driver.py`` defines
``retarget`` and ``on_handback`` as documented NO-OPS, so a ``hasattr`` probe
reports that kitchen driver as governable while a fired rule would silently do
nothing. Presence of a method is not presence of a primitive.
"""

from __future__ import annotations

import importlib
from functools import cache

from harness.contracts import RecoveryStrategy
from harness.manifest import discover


@cache
def _registry() -> dict[str, tuple[str, RecoveryStrategy]]:
    """name -> (declaring embodiment card, strategy), resolved from the fold.

    The isinstance gate is the same mount-time discipline ``Kernel.provide``
    applies to capability providers: a wrong-shaped strategy fails here, at
    load, never mid-repair.
    """
    out: dict[str, tuple[str, RecoveryStrategy]] = {}
    for name, (card, ref) in discover().recoveries.items():
        module, _, attr = ref.partition(":")
        strat = getattr(importlib.import_module(module), attr)
        if not isinstance(strat, RecoveryStrategy):
            raise TypeError(
                f"recovery {name!r} ({ref}, card {card!r}) does not satisfy "
                "harness.contracts.RecoveryStrategy")
        if strat.name != name:
            raise ValueError(
                f"recovery declared as {name!r} names itself {strat.name!r} ({ref})")
        out[name] = (card, strat)
    return out


def strategy(name: str) -> RecoveryStrategy:
    reg = _registry()
    if name not in reg:
        raise KeyError(f"unknown recovery strategy {name!r}; known: {sorted(reg)}")
    return reg[name][1]


def names() -> list[str]:
    return list(_registry())


def strategies_for(card: str) -> list[str]:
    """Every repair shape one embodiment card declares, or [] for a card that
    declares none. [] is a finished answer, never filled from another card."""
    return [n for n, (c, _s) in _registry().items() if c == card]
