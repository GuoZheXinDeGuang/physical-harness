"""S3: a suite brief on the REAL robocasa simulator -- kitchen_thaw x 1 seed, arm
scripted, submitted to a tmp session and drained by the REAL
scripts/harness_runtime.py subprocess headless (MUJOCO_GL=egl). A tmp benchmark
card (PH_PLUGINS_EXTRA, pure data) narrows robocasa_v0 to kitchen_thaw so the run
stays under a few minutes. Seals one episode with task.verify rows and one
content-addressed suite artifact. Nothing here touches runs/.

Run: cd <repo> && MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m robocasa tests/test_suite_robocasa_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from board import store as bs

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "scripts" / "harness_runtime.py"
SUITE = "robocasa_thaw"
_BENCH = f"""
[benchmarks.{SUITE}]
tasks = ["kitchen_thaw"]
arms = ["scripted", "pi05"]
max_replans = 1
max_actuations = 24
"""


def drain_suite(tmp_path: Path, arm: str, seed: int, timeout: float):
    """submit one {suite, arm, [seed,seed]} brief; drain it in a real runtime."""
    runs = tmp_path / "runs"
    session = runs / "session-main"
    (runs / "plugins" / "bench").mkdir(parents=True)
    (runs / "plugins" / "bench" / "manifest.toml").write_text(_BENCH)
    name = bs.submit_brief(runs, json.dumps(
        {"kind": "suite", "suite": SUITE, "arm": arm, "seeds": [seed, seed]}))["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), capture_output=True, text=True, timeout=timeout, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO),
             "PH_PLUGINS_EXTRA": str(runs / "plugins")})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    assert not [r for r in rows if r["kind"] == "runtime.task_error"], proc.stderr[-4000:]
    return runs, session, rows


@pytest.mark.robocasa
def test_s3_scripted_suite_on_the_real_kitchen_seals_episode_and_artifact(tmp_path):
    seed = 429002
    runs, session, rows = drain_suite(tmp_path, "scripted", seed, timeout=360)
    kinds = [r["kind"] for r in rows]
    assert kinds.index("runtime.suite_preregistered") < kinds.index("task.plan")
    plans = [r["data"] for r in rows if r["kind"] == "task.plan"]
    assert plans and plans[0]["mission"] == "kitchen_thaw"
    verify = [r["data"] for r in rows if r["kind"] == "task.verify"]
    assert verify and all("driver" not in v for v in verify)      # scripted arm throughout
    assert len([r for r in rows if r["kind"] == "task.plan_complete"]) == 1
    sealed = [r["data"] for r in rows if r["kind"] == "suite.sealed"]
    assert len(sealed) == 1
    art = bs.suite_result(session)
    assert art == json.loads((session / "suites" / f"{sealed[0]['sha']}.json").read_text())
    assert art["suite"] == SUITE and art["arm"] == "scripted" and art["seeds"] == [seed, seed]
    per = art["per_task"]["kitchen_thaw"]
    assert per["n"] == 1 and per["k"] in (0, 1) and per["L_mean"] == len(verify)
    assert per["first_death"] == (None if per["k"] else seed)
    assert "checkpoint_sha" not in art
    assert (seed, seed, "heldout", art["prereg_sha"]) in bs.burned_blocks(runs)
    cli = subprocess.run(
        [sys.executable, "-m", "board.storecli", "suite_result", "session-main",
         "--runs", str(runs)], cwd=str(REPO), capture_output=True, text=True, check=True)
    assert cli.stdout.rstrip("\n") == json.dumps(art)
