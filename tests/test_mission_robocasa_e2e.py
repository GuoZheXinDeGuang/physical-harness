"""Mission lane on the REAL kitchen: {"kind":"mission","mission":"把肉从冰箱拿到微波炉"}
drained by the real scripts/harness_runtime.py subprocess headless (MUJOCO_GL=egl).
The decomposer is planner_vlm over the fake endpoint (one canned reply naming the
known kitchen_thaw binding and a real goal); the plan comes from the card's
deterministic planner; the composed graph (task-labelled nodes, non-empty goal)
runs the persistent robocasa episode to a sealed end. Nothing touches runs/.

Run: cd <repo> && MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m robocasa \\
    tests/test_mission_robocasa_e2e.py
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
MISSION = "把肉从冰箱拿到微波炉"
GOAL = ["inside(meat,microwave)", "reported()"]
REPLY = {"tasks": [{"id": "thaw", "task": "kitchen_thaw", "goal": GOAL}],
         "rationale": "the meat goes from the fridge into the microwave"}


@pytest.mark.robocasa
def test_mission_on_the_real_kitchen_decomposes_and_seals_a_verified_episode(tmp_path):
    runs = tmp_path / "runs"
    session = runs / "session-main"
    canned = tmp_path / "canned.json"
    canned.write_text(json.dumps([REPLY]))
    name = bs.submit_brief(runs, json.dumps(
        {"kind": "mission", "mission": MISSION, "seed": 41, "arm": "scripted",
         "max_replans": 1, "max_actuations": 24}))["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), capture_output=True, text=True, timeout=360, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": f"{REPO}:{REPO / 'tests'}",
             "PH_MODEL_ENDPOINT_FAKE": str(canned),
             "PH_MISSION_DECOMPOSER": "test_mission_e2e:vlm_planner_provider"})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    kinds = lambda k: [r["data"] for r in rows if r["kind"] == k]
    assert not kinds("runtime.task_error"), proc.stderr[-4000:]
    dec = kinds("mission.decomposed")
    assert len(dec) == 1 and dec[0]["mission"] == MISSION and len(dec[0]["prompt_sha"]) == 64
    assert dec[0]["tasks"] == [{"id": "thaw", "task": "kitchen_thaw", "goal": GOAL}]
    assert not kinds("mission.refused")
    plans = kinds("task.plan")
    assert plans and plans[0]["legal"] is True, plans[0]["problems"]
    assert plans[0]["graph"]["tasks"] == [{"id": "thaw", "goal": GOAL}]
    assert plans[0]["planner"]["provider"] == "mission"
    assert plans[0]["planner"]["tasks"]["thaw"] == {
        "provider": "plugins.mission_kitchen_thaw.planner:provider"}
    assert plans[0]["embodiment"] == "plugins.embodiment_robocasa:provider"
    verify = kinds("task.verify")
    assert verify and all(v["node"].startswith("thaw.") for v in verify)
    # the composed ids reach the card's own predicates: survey + decide read the
    # live kitchen, and at least one segment drove it to a sealed stage verdict
    assert verify[0] == {"node": "thaw.survey", "results": {"survey": True}}
    assert verify[1] == {"node": "thaw.plan", "results": {"plan": True}}
    assert any("staged" in v["results"] for v in verify)
    end = kinds("task.plan_complete")
    assert len(end) == 1 and end[0]["actuations"] >= 3
    assert isinstance(end[0]["success"], bool)   # sealed either way: evidence, not a demo
