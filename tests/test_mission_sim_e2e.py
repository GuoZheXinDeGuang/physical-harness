"""G5: the task flow on the REAL robosuite simulator -- {"kind":"task","task":"stack"}
submitted through board.store.submit_brief to a tmp session, drained by the REAL
scripts/harness_runtime.py subprocess headless (MUJOCO_GL=egl), sealing task.plan,
task.verify and an episode end. Nothing here touches runs/."""

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


@pytest.mark.robosuite
def test_g5_stack_on_the_real_simulator_seals_a_verified_episode(tmp_path):
    runs = tmp_path / "runs"
    session = runs / "session-main"
    res = bs.submit_brief(runs, json.dumps({"kind": "task", "task": "stack", "seed": 41000}))
    name = res["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), capture_output=True, text=True, timeout=180, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO)})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    kinds = lambda k: [r["data"] for r in rows if r["kind"] == k]
    plans = kinds("task.plan")
    assert plans and plans[0]["legal"] is True and plans[0]["mission"] == "stack"
    assert "present(cubeA)" in plans[0]["facts"] and plans[0]["visible"] == ["pick", "stack"]
    verify = kinds("task.verify")
    assert verify and all(set(v["results"]) == {"stack_success"} for v in verify)
    assert all(v["results"]["stack_success"] in (True, False) for v in verify)
    end = kinds("task.plan_complete")
    assert len(end) == 1 and end[0]["actuations"] >= 1
    assert [s["name"] for s in end[0]["nodes"]["stack-0"]["stages"]] == ["grasp", "place"]
    assert not kinds("runtime.task_error")
