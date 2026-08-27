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

Keyframes hang off THIS layer, not the chain: ``on_emit`` lets a downstream
capture layer (scripts/frame_dump) pin a still to an event, and ``arm`` clears
the ``keyframes/`` sibling directory on the same truncate-per-boot lifecycle.
Because the anchor is a feed seq and never a chain row, "frames never enter the
session-log chain" holds by construction -- deleting the whole directory loses
zero evidence, exactly like deleting this file.
"""

from __future__ import annotations

import json
import os
import time

#: Armed destination (str path) or None. Module-level singleton on purpose --
#: the resident runtime is one process, one session; the workload and the
#: rollout reach the feed without threading a writer through every seam.
#: ponytail: single-flight assumption (the runtime processes briefs serially);
#: add a lock + per-task streams if tasks ever run concurrently.
_path: str | None = None
_seq = 0

#: After-emit listeners, ``fn(seq, kind)``. Registration is INVERTED so the
#: kernel keeps importing nothing (test_kernel's AST rule): the capture layer
#: lives in scripts/frame_dump and registers itself here at import. Listeners
#: outlive arm() -- a re-boot re-truncates the feed, not the wiring.
_hooks: list = []


def on_emit(fn) -> None:
    """Register ``fn(seq, kind)``, called once per event that actually landed
    in the feed (never for a dropped or unarmed emit), in registration order.

    A listener inherits emit's discipline: its exception is swallowed and the
    remaining listeners still run -- a capture layer can never fail a task.
    """
    _hooks.append(fn)


def keyframe_dir() -> str | None:
    """The armed session's ``keyframes/`` directory (the feed's sibling), or
    None unarmed. Cleared by arm(); written by an on_emit listener, read by
    board.store.read_runtime_keyframes."""
    if _path is None:
        return None
    return os.path.join(os.path.dirname(_path), "keyframes")


def arm(path) -> None:
    """Truncate ``path``, clear ``keyframes/``, and direct subsequent emits
    there. Runtime-boot only.

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
        return
    # Same horizon as the feed: this boot's stills are the ones a panel can
    # pair with this boot's seqs, so the previous boot's go. Best-effort --
    # a leftover still is a stale thumbnail, never a broken boot.
    d = keyframe_dir()
    if d is None:  # unreachable (_path was just set); never listdir(None), which is cwd
        return
    try:
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
    except OSError:
        pass


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
        return
    for fn in _hooks:
        try:
            fn(_seq, kind)
        except Exception:
            pass
