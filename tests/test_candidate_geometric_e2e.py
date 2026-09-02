"""The first code candidate end to end: ``plugins/candidates/grasp_geometric_robocasa``
mounted through PH_PLUGINS_EXTRA binds executor key ``geometric`` onto grasp_meat's
robocasa record (bindings.robocasa.policies.geometric = {transport: inproc, ref}) --
and ONLY while mounted (the library file stays untouched). On the REAL kitchen
(robocasa lane) a 1-seed suite under arm ``geometric`` runs to completion in the
real scripts/harness_runtime.py subprocess and the grasp node's task.verify row
seals executor='geometric' with the candidate's ref, so ``skill_evidence`` lists a
by_executor row for it. Success is not required -- the routing and attribution are.

Run: MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m robocasa tests/test_candidate_geometric_e2e.py
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
CANDIDATES = REPO / "plugins" / "candidates"
REF = "plugins.candidates.grasp_geometric_robocasa:provider"
SUITE = "robocasa_thaw_geometric"
_BENCH = f"""
[benchmarks.{SUITE}]
tasks = ["kitchen_thaw"]
arms = ["scripted", "geometric"]
max_replans = 1
max_actuations = 24
"""
_PROBE = """
import json, os
from harness.skill_library import RECORDS, ARMS
b = RECORDS["grasp_meat"].bindings["robocasa"]
print(json.dumps({"policies": b.get("policies"), "geometric_arm": "geometric" in ARMS}))
"""


def _probe(extra: str | None) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    env.pop("PH_PLUGINS_EXTRA", None)
    if extra:
        env["PH_PLUGINS_EXTRA"] = extra
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=str(REPO), env=env,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_candidate_binds_geometric_only_while_mounted():
    assert _probe(None) == {"policies": None, "geometric_arm": False}   # library alone
    mounted = _probe(str(CANDIDATES))
    assert mounted["geometric_arm"] is True
    assert mounted["policies"] == {"scripted": {},
                                   "geometric": {"transport": "inproc", "ref": REF}}
    rec = json.loads((REPO / "skill-library" / "records" / "grasp_meat.json").read_text())
    assert "policies" not in rec["bindings"]["robocasa"]                 # file untouched


@pytest.mark.robocasa
def test_geometric_suite_runs_and_seals_by_executor_evidence(tmp_path):
    seed = 429002
    runs = tmp_path / "runs"
    session = runs / "session-main"
    (runs / "plugins" / "bench").mkdir(parents=True)
    (runs / "plugins" / "bench" / "manifest.toml").write_text(_BENCH)
    name = bs.submit_brief(runs, json.dumps(
        {"kind": "suite", "suite": SUITE, "arm": "geometric", "seeds": [seed, seed]}))["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), capture_output=True, text=True, timeout=900, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO),
             "PH_PLUGINS_EXTRA": f"{runs / 'plugins'}:{CANDIDATES}"})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    assert not [r for r in rows if r["kind"] == "runtime.task_error"], proc.stderr[-4000:]
    assert len([r for r in rows if r["kind"] == "suite.sealed"]) == 1     # ran to completion
    verify = [r["data"] for r in rows if r["kind"] == "task.verify"]
    grasp = [v for v in verify if v.get("executor") == "geometric"]
    assert grasp, [(v["node"], v.get("executor")) for v in verify]      # the handover happened
    assert grasp[0]["driver"]["ref"] == REF
    assert grasp[0]["driver"]["handshake"]["transport"] == "inproc"
    assert all(v.get("executor") in (None, "scripted") for v in verify if v not in grasp)  # non-segment rows seal none
    ev = [e for e in bs.skill_evidence(session) if e["executor"] == "geometric"]
    assert len(ev) == 1 and ev[0]["skill"] == "grasp_meat" and ev[0]["n"] == len(grasp)
    assert 0 <= ev[0]["k"] <= len(grasp)          # success not required; the row is
