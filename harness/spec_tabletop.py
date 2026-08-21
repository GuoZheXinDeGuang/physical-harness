"""Tabletop-manipulation motor vocabulary.

Shared by the scripted drivers (policies plugin) and the recovery actor (rsi
plugin): both import the kernel and neither may import the other, so the
vocabulary sits kernel-side in its own domain module. Future domains (nav,
door opening) add sibling harness/spec_<domain>.py modules rather than
touching the core. Pure literals, no imports -- the kernel boundary test
covers this file for free.
"""

from __future__ import annotations

#: Per-phase height over the target, in metres. Shared motor vocabulary: the
#: scripted drivers (policies plugin) and the recovery actor (rsi plugin) both
#: speak it, so like NOMINAL_SCHEDULE it lives with the currency rather than
#: in either plugin -- the cross-plugin import neither may make.
PHASE_HEIGHT = {"above": 0.10, "descend": 0.005, "close": 0.005, "lift": 0.25}

NOMINAL_SCHEDULE: tuple[tuple[str, int], ...] = (
    ("above", 25), ("descend", 25), ("close", 12), ("lift", 38),
)
