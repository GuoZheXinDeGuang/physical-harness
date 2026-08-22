"""RUNG 1: the resident runtime's intake -- drain, escape-hatch, restart-resume.

No simulation here: ``workload._governed_rollout`` is monkeypatched (the
test_task_seam fake手法), so the well-formed briefs run their real
plan/validate/mount loop on a real base_profile kernel but the rollout returns a
canned success. What is under test is the RUNTIME layer: two valid briefs land
in done/, a planner-raise brief escapes to failed/ with exactly one
``runtime.task_error`` note while the loop keeps going, and the one shared
SessionLog reloads from disk and verifies -- including across a reboot that
re-queues a crashed brief from processing/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from harness.events import SessionLog
from plugins.task import workload
from scripts import harness_runtime as runtime


def _ok_rollout(spec):
    """governed_rollout's stages-on shape, always succeeding (no env built)."""
    return {"success": True, "steps": 10, "stages": [
        {"name": "grasp", "success": True}, {"name": "place", "success": True}]}


def _drop(inbox: Path, name: str, brief: dict) -> None:
    """Drop a brief the way an external writer must: whole-file via os.replace."""
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps(brief))
    os.replace(tmp, inbox / name)


def test_runtime_drain(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "a-stack.json", {"kind": "task", "task": "stack", "seed": 90000})
    _drop(inbox, "b-clear.json", {"kind": "task", "task": "clear_table", "seed": 90001})
    _drop(inbox, "c-nonsense.json", {"kind": "task", "task": "nonsense", "seed": 90002})

    rt = runtime.main(session, drain=True)

    assert sorted(p.name for p in rt.done.glob("*.json")) == ["a-stack.json", "b-clear.json"]
    assert sorted(p.name for p in rt.failed.glob("*.json")) == ["c-nonsense.json"]
    assert not list(rt.inbox.glob("*.json")), "drain leaves the inbox empty"

    # exactly one escape note, for the planner-raise; the two that ran wrote none
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1
    assert errors[0]["data"]["brief"] == "c-nonsense.json"
    assert errors[0]["data"]["task"] == "nonsense"
    completes = [r for r in rt.log.rows() if r["kind"] == "task.plan_complete"]
    assert len(completes) == 2, "each well-formed task closed with its own note"

    # the shared chain reloads from disk and verifies -- one continuous ledger
    reloaded = SessionLog.load(session / "session-log")
    assert reloaded.verify()
    assert [r["kind"] for r in reloaded.rows()] == [r["kind"] for r in rt.log.rows()]


def test_malformed_brief_fails_without_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "junk.json").write_text("{not json")

    rt = runtime.main(session, drain=True)

    assert (rt.failed / "junk.json").exists()
    assert rt.log.rows() == (), "a malformed brief has nothing to attribute a note to"


def test_boot_requeues_processing_and_continues_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "first.json", {"kind": "task", "task": "stack", "seed": 90000})
    rt = runtime.main(session, drain=True)
    rows_after_first = len(rt.log.rows())
    assert rows_after_first > 0

    # a crash leaves a claimed-but-unfinished brief stranded in processing/
    _drop(rt.processing, "stuck.json", {"kind": "task", "task": "stack", "seed": 90003})

    # reboot: the stranded brief is re-queued and drained, and the chain GROWS
    # from disk rather than restarting at genesis
    rt2 = runtime.main(session, drain=True)
    assert not list(rt2.processing.glob("*.json"))
    assert (rt2.done / "stuck.json").exists(), "the requeued brief ran to completion"
    assert len(rt2.log.rows()) > rows_after_first, "the chain grew, it did not restart"
    assert SessionLog.load(session / "session-log").verify()
