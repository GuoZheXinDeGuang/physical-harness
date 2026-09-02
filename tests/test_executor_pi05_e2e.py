"""Per-node executor choice on the REAL robocasa simulator against the REAL pi0.5
server: the kitchen_thaw graph under arm ``auto`` with ``executor: pi05`` on the
place node and ``scripted`` on grasp (a tmp card wrapping the deterministic
kitchen planner, PH_PLUGINS_EXTRA), drained by the real scripts/harness_runtime.py
subprocess. The place row seals executor='pi05' with driver.ref policy_vla_remote
and the served checkpoint_sha; the scripted segments seal executor='scripted'.
The episode need not succeed -- the routing and attribution must. Nothing here
touches runs/; the server is the pi05_server fixture's (stopped after the module).

Run: MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m "robocasa and vla" tests/test_executor_pi05_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from board import store as bs
from harness.skill_library import RECORDS
from test_pi05_arm_sim_e2e import RUNTIME, SEED, VLA, pi05_server  # noqa: F401  (fixture)

PIN = RECORDS["place_meat"].bindings["robocasa"]["policies"]["pi05"]["checkpoint_sha"]
_EXECUTORS = {"place": "pi05", "grasp": "scripted"}


class _Planner:
    """The kitchen planner's fixed graph with an explicit executor per node."""
    identity = "e2e:thaw_exec"

    def plan(self, brief):
        from plugins.mission_kitchen_thaw.planner import KitchenThawPlanner
        plan = json.loads(json.dumps(KitchenThawPlanner().plan({**brief, "task": "kitchen_thaw"})))
        for n in plan["nodes"]:
            if n["id"] in _EXECUTORS:
                n["executor"] = _EXECUTORS[n["id"]]
        return plan


def planner_provider():
    return _Planner()


#: the kitchen_thaw card's binding verbatim, under a test task with the wrapper planner
_CARD = """
[task_bindings.e2e_thaw_exec]
env = "plugins.embodiment_robocasa:provider"
percept = "plugins.embodiment_robocasa.percept:provider"
policy = "plugins.embodiment_robocasa.kitchen_driver:provider"
planner = "test_executor_pi05_e2e:planner_provider"
catalogue = "plugins.mission_kitchen_thaw.planner:CATALOGUE"
records = "plugins.mission_kitchen_thaw.planner:SKILL_RECORDS"
initial_facts = "plugins.mission_kitchen_thaw.planner:INITIAL_FACTS"
oracles = "plugins.mission_kitchen_thaw.planner:ORACLES"
predicates = "plugins.mission_kitchen_thaw.planner:PREDICATES"
episodic = true
episode = "plugins.mission_kitchen_thaw.planner:EPISODE"
segment_specs = "plugins.mission_kitchen_thaw.planner:SEGMENT_SPECS"
"""


@pytest.mark.robocasa
@pytest.mark.vla
def test_auto_arm_routes_place_to_pi05_and_seals_executor_per_node(tmp_path, pi05_server):
    sha = pi05_server["metadata"]["checkpoint_sha"]
    assert sha == PIN
    runs = tmp_path / "runs"
    session = runs / "session-main"
    (runs / "plugins" / "e2e").mkdir(parents=True)
    (runs / "plugins" / "e2e" / "manifest.toml").write_text(_CARD)
    name = bs.submit_brief(runs, json.dumps(
        {"kind": "task", "task": "e2e_thaw_exec", "seed": SEED, "arm": "auto",
         "max_actuations": 24}))["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(RUNTIME.parent.parent), capture_output=True, text=True, timeout=900,
        check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PH_PLUGINS_EXTRA": str(runs / "plugins"),
             "PYTHONPATH": f"{RUNTIME.parent.parent}:{RUNTIME.parent.parent / 'tests'}"})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    assert not [r for r in rows if r["kind"] == "runtime.task_error"], proc.stderr[-4000:]
    plan = [r["data"] for r in rows if r["kind"] == "task.plan"][0]
    assert plan["legal"] is True and plan["arm"] == "auto", plan["problems"]
    assert {n["id"]: n.get("executor") for n in plan["graph"]["nodes"]
            if n["id"] in _EXECUTORS} == _EXECUTORS
    verify = {r["data"]["node"]: r["data"] for r in rows if r["kind"] == "task.verify"}
    assert "place" in verify, sorted(verify)                          # reached the handover
    place = verify["place"]
    assert place["executor"] == "pi05" and place["driver"]["ref"] == VLA
    assert place["driver"]["handshake"]["metadata"]["checkpoint_sha"] == sha
    for seg in ("nav-fridge", "grasp", "nav-micro"):
        assert verify[seg]["executor"] == "scripted" and "driver" not in verify[seg], seg
