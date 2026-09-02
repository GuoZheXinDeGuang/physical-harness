"""The lightweight evolve loop on the REAL robocasa kitchen: kitchen_thaw x 2 seeds,
arm scripted, ONE round, submitted to a tmp evolution-mode runtime drained headless
(MUJOCO_GL=egl) -- the same shape as tests/test_suite_robocasa_e2e.py. Asserts the
campaign lands (rsi_series one row, one rsi_step row) and that a seed that
succeeded left a <1 MB media clip. Nothing here touches runs/.

Run: cd <repo> && MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m robocasa tests/test_evolve_robocasa_e2e.py
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
TASK = "kitchen_thaw"


@pytest.mark.robocasa
def test_one_evolve_round_on_the_real_kitchen(tmp_path):
    runs = tmp_path / "runs"
    session = runs / "session-main"
    seeds = [429002, 429003]
    name = bs.submit_brief(runs, json.dumps(
        {"kind": "evolve", "task": TASK, "seeds": seeds, "rounds": 1, "arm": "scripted",
         "max_actuations": 24, "max_replans": 1}))["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain",
         "--mode", "evolution"],
        cwd=str(REPO), capture_output=True, text=True, timeout=1800, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO)})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    assert not [r for r in rows if r["kind"] == "runtime.task_error"], proc.stderr[-4000:]
    series = bs.rsi_series(session, TASK)
    assert len(series) == 1 and series[0]["round"] == 1
    assert 0 <= series[0]["before"] <= 2 and series[0]["best"] >= series[0]["before"]
    steps = [r["data"] for r in rows if r["kind"] == "rsi_step"]
    assert [s["round"] for s in steps] == [1] and steps[0]["task"] == TASK
    run = bs.rsi_run(session, TASK)
    assert run["status"] == "done" and run["cursor"] == 1 and run["latest"]["round"] == 1
    assert run["latest"]["tried"]["kind"] in ("executor", "tunables", "none")
    # media: every kept clip is a verified segment, under 1 MB, listed by rsi_frames
    frames = bs.rsi_frames(session, TASK, 1)
    assert frames == run["latest"]["media"]
    for rel in frames:
        f = session / rel
        assert f.is_file() and 0 < f.stat().st_size <= media.MAX_BYTES, rel
    kept = {s: media.index_of(session / "media", TASK, s) for s in seeds}
    if run["latest"]["best"] > 0:   # a successful seed verified every segment: clips exist
        assert any(kept.values()), kept
    assert all(f"media/{TASK}/{s}/{v['file']}" in frames
               for s, idx in kept.items() for v in idx.values())
