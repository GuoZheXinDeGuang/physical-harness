"""S1/S2 end to end: a ``suite`` brief through the REAL scripts/harness_runtime.py
subprocess and the REAL board/storecli.py. No simulator: a tmp benchmark card
(PH_PLUGINS_EXTRA) names two tasks bound to test_mission_e2e's fake embodiment
(one that seals a legal episode, one whose graph is refused -> a death).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_mission_e2e import _CARD, _Runtime, _wait, REPO, SESSION

from board import mcp_server as ms
from board import store as bs

_BENCH = """
[benchmarks.e2e_v0]
tasks = ["suite_ok", "suite_dead"]
arms = ["scripted", "pi05"]
max_replans = 1
"""


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    (runs / "plugins" / "bench").mkdir(parents=True)
    (runs / "plugins" / "bench" / "manifest.toml").write_text(
        _CARD.format(task="suite_ok", planner="planner_provider")
        + _CARD.format(task="suite_dead", planner="unsupported_planner_provider")
        + _BENCH)
    rt = _Runtime(runs)
    yield rt
    rt.stop()


def _submit(runtime, brief):
    before = len(bs.chain_rows(runtime.session))
    name = bs.submit_brief(runtime.runs, json.dumps(brief), session=SESSION)["submitted"]
    where = lambda: [d for d in ("done", "failed", "cancelled")
                     if (runtime.session / d / name).exists()]
    _wait(lambda: where() or runtime.proc.poll() is not None, 120, f"{name} filed")
    return where(), bs.chain_rows(runtime.session)[before:]


@pytest.fixture(scope="module")
def s1(runtime):
    return _submit(runtime, {"kind": "suite", "suite": "e2e_v0", "arm": "scripted",
                             "seeds": [5, 6]})


def test_s1_suite_runs_every_task_seed_through_the_task_path_and_seals(runtime, s1):
    where, rows = s1
    assert where == ["done"], rows
    plans = [r["data"] for r in rows if r["kind"] == "task.plan_complete"]
    assert len(plans) == 4   # 2 tasks x 2 seeds, each an ordinary task episode
    sealed = [r["data"] for r in rows if r["kind"] == "suite.sealed"]
    assert len(sealed) == 1 and sealed[0]["suite"] == "e2e_v0"
    art = bs.suite_result(runtime.session)
    assert art == json.loads((runtime.session / "suites" / f"{sealed[0]['sha']}.json").read_text())
    assert art["suite"] == "e2e_v0" and art["arm"] == "scripted" and art["seeds"] == [5, 6]
    assert art["per_task"] == {
        "suite_ok": {"n": 2, "k": 2, "L_mean": 1.0, "first_death": None},
        "suite_dead": {"n": 2, "k": 0, "L_mean": 0.0, "first_death": 5}}
    assert len(art["prereg_sha"]) == 64 and "checkpoint_sha" not in art
    # prereg sealed BEFORE the first episode, and burned_blocks sees the block
    kinds = [r["kind"] for r in rows]
    assert kinds.index("runtime.suite_preregistered") < kinds.index("task.plan")
    assert (5, 6, "heldout", art["prereg_sha"]) in bs.burned_blocks(runtime.runs)


def test_s1_three_faces_return_the_artifact_byte_equal(runtime, s1):
    direct = bs.suite_result(runtime.session)
    assert direct is not None
    res = subprocess.run(
        [sys.executable, "-m", "board.storecli", "suite_result", SESSION,
         "--runs", str(runtime.runs)],
        cwd=str(REPO), capture_output=True, text=True, check=True)
    assert res.stdout.rstrip("\n") == json.dumps(direct)
    ms.configure(runtime.runs)
    assert json.dumps(ms.suite_result(SESSION)) == json.dumps(direct)
    assert json.dumps(ms.suite_result(SESSION, bs.chain_rows(runtime.session)[-1]["data"]["sha"])) == json.dumps(direct)
    assert bs.suite_result(runtime.session, "../x") is None


def test_s2_overlapping_block_is_refused_before_any_episode(runtime, s1):
    where, rows = _submit(runtime, {"kind": "suite", "suite": "e2e_v0",
                                    "arm": "scripted", "seeds": [6, 9]})
    assert where == ["failed"]
    kinds = [r["kind"] for r in rows]
    assert "task.plan" not in kinds and "runtime.suite_preregistered" not in kinds
    err = [r["data"]["error"] for r in rows if r["kind"] == "runtime.task_error"]
    assert len(err) == 1 and "seed-ledger overlap" in err[0] and "[6,9]" in err[0]
    # the ledger did not grow: still the one heldout block
    assert [b[:3] for b in bs.burned_blocks(runtime.runs)] == [(5, 6, "heldout")]
