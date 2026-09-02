"""The media recorder on the REAL kitchen, on a driver that never had its own
``frame()``: recycle_cans seed 4243, arm scripted, ``media: true``, drained by a
tmp runtime headless (MUJOCO_GL=egl, no --render). The embodiment-level frame
source (obs camera image) means the nav / grasp / carry segments that verify true
leave <1 MB clips, and every segment seals ``diagnostics.media`` (kept file or the
honest reason) -- the overnight finding was three media runs with no media/ at all.

Run: cd <repo> && MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m robocasa \\
    tests/test_media_robocasa_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from board import store as bs
from harness import media

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "scripts" / "harness_runtime.py"
TASK, SEED = "recycle_cans", 4243


@pytest.mark.robocasa
def test_recycle_cans_media_keeps_verified_segments_under_1mb(tmp_path):
    runs = tmp_path / "runs"
    session = runs / "session-main"
    name = bs.submit_brief(runs, json.dumps(
        {"kind": "task", "task": TASK, "seed": SEED, "arm": "scripted", "media": True,
         "max_replans": 1, "max_actuations": 24}))["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), capture_output=True, text=True, timeout=2400, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO)})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    assert not [r for r in rows if r["kind"] == "runtime.task_error"], proc.stderr[-4000:]
    end = [r["data"] for r in rows if r["kind"] == "task.plan_complete"]
    assert len(end) == 1
    nodes = end[0]["nodes"]
    segs = {n: d for n, d in nodes.items() if "media" in (d.get("diagnostics") or {})}
    assert segs, sorted(nodes)   # every segment seals kept/reason, never silence
    kept = media.index_of(session / "media", TASK, SEED)
    dropped = media.dropped_of(session / "media", TASK, SEED)
    for node, d in segs.items():
        m = d["diagnostics"]["media"]
        if d["success"]:
            assert m["kept"] is True and node in kept, (node, m, dropped)
        else:
            assert m == {"kept": False, "reason": "verify_failed"} or m["kept"] is True, m
    for node in ("nav-can1", "grasp-can1", "carry-can1"):
        assert segs[node]["success"] and node in kept, (node, segs.get(node), dropped)
        f = session / "media" / TASK / str(SEED) / kept[node]["file"]
        assert f.is_file() and 0 < f.stat().st_size <= media.MAX_BYTES, (node, f)
    assert not set(kept) & set(dropped)
