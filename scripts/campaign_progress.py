#!/usr/bin/env python3
"""Live campaign progress for script-path batteries (probe/campaign scripts).

Campaigns run OUTSIDE the resident runtime (two-state discipline), so they have
no runtime_events feed; while one runs, the console sees nothing until the store
seals. This helper gives any battery script a heartbeat the board can read:
``runs/<store>/progress.json``, atomically overwritten per finished episode.

RENDERING IS LIVE STATE, NOT EVIDENCE (same family as runtime_status.json /
frame.jpg): progress.json is never a chain row, never sealed, and the board
reads it with no verify. The rolling statistics (success count, first-death
histogram) are folded HERE, python-side -- the TS panel only displays them
(charter: statistics live in board/scripts, never the fork's TypeScript).

Every write swallows every exception: a broken progress heartbeat must never
kill a battery that has been running for hours.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path


def write_progress(out_dir: str | Path, done: int, total: int, *,
                   label: str | None = None, started_ts: float | None = None,
                   extra: dict | None = None) -> None:
    """Atomically overwrite ``<out_dir>/progress.json`` (temp + os.replace, so a
    board poll never reads a half-written file). ``extra`` carries the rolling
    stats verbatim. Never raises -- progress is best-effort by contract."""
    try:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"done": int(done), "total": int(total),
                   "started_ts": float(started_ts) if started_ts is not None else time.time(),
                   "updated_ts": time.time(), "label": label, **(extra or {})}
        tmp = out_dir / "progress.json.tmp"
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, out_dir / "progress.json")
    except Exception:  # noqa: BLE001 -- a dead heartbeat must never kill the battery
        pass


def tracker(out_dir: str | Path, total: int, label: str | None = None):
    """Per-episode progress callback for a battery's result loop.

    Returns ``tick(row)``: call it with each finished episode row (any dict; a
    probe row's ``thawed``/``success`` counts as a success, ``first_death``
    feeds the rolling histogram) and it folds the stats and rewrites
    progress.json. Row-shape tolerant so future campaign scripts reuse it
    unchanged. tick itself never raises (write_progress swallows)."""
    started = time.time()
    done = 0
    succeeded = 0
    first_death: Counter = Counter()

    def tick(row: dict | None = None) -> None:
        nonlocal done, succeeded
        done += 1
        if isinstance(row, dict):
            if row.get("thawed") or row.get("success"):
                succeeded += 1
            fd = row.get("first_death")
            if isinstance(fd, str) and fd != "none":
                first_death[fd] += 1
        write_progress(out_dir, done, total, label=label, started_ts=started,
                       extra={"succeeded": succeeded,
                              "first_death": dict(first_death)})

    # write the 0/total row up front so the card appears as soon as the battery
    # starts, not after its first (possibly minutes-long) episode
    write_progress(out_dir, 0, total, label=label, started_ts=started,
                   extra={"succeeded": 0, "first_death": {}})
    return tick
