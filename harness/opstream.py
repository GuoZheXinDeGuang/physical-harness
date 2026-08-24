"""Operational event stream: ``runs/<session>/runtime_events.jsonl``.

The live-progress feed the console's execution-graph panel animates from --
the runtime_status.json pattern (GOAL v4.2): a plain file the runtime
overwrites per boot and appends to while a task runs, read-only downstream,
NEVER a chain row. The sealed chain (session-log/rows.jsonl) stays the
evidence of record; losing this whole file loses zero evidence.

Why a second file instead of chain notes: the chain is hash-sealed evidence
with exactly one writer discipline, and mid-flight progress (node started,
stage crossed, replanning...) is operational state that changes at animation
speed. Folding it into the chain would bloat the sealed ledger with
non-evidence and make every UI poll a chain verify.

Contract (all three lines of it):

- ``arm(path)`` truncates the file and resets ``seq`` -- once per boot, by the
  runtime only. Truncate-per-boot (not rotate): the panel renders the CURRENT
  boot; history lives in the sealed chain. A reader detects the truncation by
  ``last_seq`` moving backwards past its cursor and re-reads from 0.
- ``emit(kind, **detail)`` appends one JSON line ``{ts, seq, kind, ...detail}``.
  Unarmed (any process that is not the resident runtime: campaign subprocesses,
  tests, library users) it is a no-op.
- Emitting can NEVER fail a task: every filesystem touch is swallowed. A lost
  event is a skipped animation frame; a raised one would be a broken rollout.

Hot-path cost when armed: one json.dumps + open-append-close per event, and
events fire at node/stage/replan cadence (tens per task), never per sim step.
"""

from __future__ import annotations

import json
import time

#: Armed destination (str path) or None. Module-level singleton on purpose --
#: the resident runtime is one process, one session; the workload and the
#: rollout reach the feed without threading a writer through every seam.
#: ponytail: single-flight assumption (the runtime processes briefs serially);
#: add a lock + per-task streams if tasks ever run concurrently.
_path: str | None = None
_seq = 0


def arm(path) -> None:
    """Truncate ``path`` and direct subsequent emits there. Runtime-boot only.

    Any failure (unwritable dir, read-only fs) leaves the stream unarmed and
    the runtime unharmed -- the panel just shows no live feed.
    """
    global _path, _seq
    try:
        with open(path, "w"):
            pass
        _path = str(path)
        _seq = 0
    except Exception:
        _path = None


def emit(kind: str, **detail) -> None:
    """Append one event line; silently no-op unarmed, silently drop on error."""
    global _seq
    if _path is None:
        return
    try:
        _seq += 1
        line = json.dumps({"ts": round(time.time(), 3), "seq": _seq,
                           "kind": kind, **detail})
        with open(_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
