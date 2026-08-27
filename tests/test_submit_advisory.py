"""The session x task advisory on submit_brief: a warning, never a gate.

Dropping a robocasa mission into session-main is ACCEPTED (the task string is in
the manifest union, one table across every card) and refused seconds later inside
a different process, in a log the operator is not reading. submit_brief now says
so in the answer -- and still submits, because its zero-validation is deliberate
(the runtime is the sole authority; a producer that could refuse would launder it).

The fixture spawns a REAL process under this interpreter with a harness_runtime
argv, because both halves of the judgement are read from live state: the pid in
runtime_status.json -> /proc/<pid>/cmdline -> the interpreter -> what it can
import. The repo venv is exactly the incompatible half (robocasa cannot share it
-- numpy 2.x ABI, README's dependency table), so "no robocasa here" is a fact of
the venv running this test, not a mock.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from test_read_session import _session

from board import mcp_server as ms


def _live_session(tmp_path: Path, name: str):
    """A real session dir whose runtime_status.json points at a LIVE process
    running ``harness_runtime.py`` under this interpreter."""
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    _session(runs, name)
    fake = tmp_path / "harness_runtime.py"
    fake.write_text("import time; time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, str(fake)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (runs / name / "runtime_status.json").write_text(json.dumps({"pid": proc.pid}))
    return runs, proc


def test_incompatible_task_warns_and_still_submits(tmp_path):
    runs, proc = _live_session(tmp_path, "session-main")
    try:
        ms.configure(runs)
        # kitchen_thaw's binding names env = plugins.embodiment_robocasa:provider;
        # that card's third_party includes robocasa, absent from the repo venv.
        res = ms.submit_brief({"kind": "task", "task": "kitchen_thaw", "seed": 42001})
        assert (runs / "session-main" / "inbox" / res["submitted"]).exists(), \
            "the advisory must not gate the drop"
        assert "robocasa" in res["warning"] and "session-main" in res["warning"]

        # a task riding the session's own base embodiment says nothing at all
        ok = ms.submit_brief({"kind": "task", "task": "stack", "seed": 42002})
        assert "warning" not in ok, ok  # the key is absent, not empty
        assert (runs / "session-main" / "inbox" / ok["submitted"]).exists()
    finally:
        proc.kill(); proc.wait()


def test_no_warning_without_a_live_runtime_to_read(tmp_path):
    """Silence beats a guess: with no runtime_status.json there is no interpreter
    to ask, so the same mismatch is submitted with no warning key."""
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    ms.configure(runs)
    res = ms.submit_brief({"kind": "task", "task": "kitchen_thaw", "seed": 42003})
    assert "warning" not in res and "submitted" in res


def test_recycled_pid_is_not_read_as_an_interpreter(tmp_path):
    """A pid whose cmdline is not a harness runtime reads as unknown -- the guard
    that keeps a recycled pid from sourcing a wrong warning."""
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (runs / "session-main" / "runtime_status.json").write_text(
            json.dumps({"pid": other.pid}))
        assert ms._session_python(runs / "session-main") is None
    finally:
        other.kill(); other.wait()


def test_run_task_carries_the_advisory_onto_its_outcome(tmp_path):
    """run_task blocks on a runtime that will never claim (the spawned process is
    a sleeper), so it times out -- and the reason rides along."""
    runs, proc = _live_session(tmp_path, "session-main")
    try:
        ms.configure(runs)
        start = time.monotonic()
        res = ms.run_task("kitchen_thaw", 42004, timeout_s=0.1)
        assert res["status"] == "timeout" and time.monotonic() - start < 30
        assert "robocasa" in res["warning"]
    finally:
        proc.kill(); proc.wait()
