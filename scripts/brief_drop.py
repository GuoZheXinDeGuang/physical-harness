"""The shared atomic brief-drop: temp write + os.replace, nothing else.

An external writer must land a brief in the runtime inbox WHOLE -- the resident
runtime claims by ``os.rename`` and must never read a half-written file. That is
the entire contract this helper enforces: write a ``.tmp`` sibling, then
``os.replace`` it into place (atomic within one filesystem).

Deliberately NO validation. A brief is selector+budgets, and the resident
runtime re-validates ``_BRIEF_KEYS`` server-side on claim
(scripts/harness_runtime.py) and stays the SOLE authority -- an injected key
must ride through unchanged and hard-fail there, not be silently dropped by a
producer. Every producer (soak's fault injector, the MCP submit_brief tool, the
zos sim_test seam) shares this one function so the atomicity is written once.
"""

from __future__ import annotations

import os
from pathlib import Path


def drop(inbox: str | Path, name: str, raw: str) -> Path:
    """Atomically drop ``raw`` as ``inbox/name``. Returns the final path."""
    inbox = Path(inbox)
    dest = inbox / name
    tmp = inbox / (name + ".tmp")
    tmp.write_text(raw)
    os.replace(tmp, dest)
    return dest
