"""RUNG 3: the MCP submit_brief producer -> resident runtime, end to end.

submit_brief is the SECOND producer onto the same inbox soak's fault injector
writes to; both share brief_drop.drop (temp write + os.replace). What is under
test is that a brief composed by the MCP seam and dropped by the new producer is
claimed and adjudicated by the REAL runtime exactly as any other -- proving the
seam adds no authority. The rollout is monkeypatched (test_runtime_drain's fake)
so the well-formed brief runs its real plan/mount loop with no MuJoCo; the
injected-key brief is rejected by _BRIEF_KEYS BEFORE any rollout, so it needs no
fake at all -- that IS the point: the runtime, not the producer, is the guard.
"""

from __future__ import annotations

from test_runtime_drain import _ok_rollout

from board import mcp_server as ms
from harness.events import SessionLog
from plugins.task import workload
from scripts import harness_runtime as runtime


def test_submit_brief_wellformed_drains_to_done(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    ms.configure(tmp_path, inbox=session / "inbox")

    res = ms.submit_brief({"kind": "task", "task": "stack", "seed": 90000})
    assert (session / "inbox" / res["submitted"]).exists(), "brief landed in the inbox"

    rt = runtime.main(session, drain=True)

    assert (rt.done / res["submitted"]).exists(), "well-formed brief filed under done/"
    assert not list(rt.failed.glob("*.json"))
    completes = [r for r in rt.log.rows() if r["kind"] == "task.plan_complete"]
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(completes) == 1 and not errors
    assert SessionLog.load(session / "session-log").verify()


def test_submit_brief_injected_key_fails_through_the_new_producer(tmp_path):
    """No rollout fake: an injected provider ref hits _BRIEF_KEYS and hard-fails
    to failed/ with one runtime.task_error note -- the server-side guard fires
    through the MCP producer just as it does through soak's."""
    session = tmp_path / "session-main"
    ms.configure(tmp_path, inbox=session / "inbox")

    res = ms.submit_brief(
        {"kind": "task", "task": "stack", "seed": 90001, "policy": "evil.module:provider"})

    rt = runtime.main(session, drain=True)

    assert (rt.failed / res["submitted"]).exists(), "injected-key brief filed under failed/"
    assert not list(rt.done.glob("*.json"))
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1 and errors[0]["data"]["brief"] == res["submitted"]
    assert "unknown brief keys" in errors[0]["data"]["error"]
    assert SessionLog.load(session / "session-log").verify()
