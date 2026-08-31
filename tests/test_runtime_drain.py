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
import time
from pathlib import Path

from board import store as bs
from harness.events import SessionLog
from plugins.task import workload
from scripts import harness_runtime as runtime


def _ok_rollout(spec, bundle=None):
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


def test_pending_ignores_file_moved_after_glob(tmp_path, monkeypatch):
    """Atomic inbox ownership changes must not kill the resident poll loop."""
    rt = runtime.boot(tmp_path / "session-main")
    vanished = rt.inbox / "vanished.json"
    present = rt.inbox / "present.json"
    vanished.write_text("{}")
    present.write_text("{}")

    real_stat = Path.stat

    def racing_stat(path, *args, **kwargs):
        if path == vanished:
            path.unlink(missing_ok=True)  # claimed after glob, before stat
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert runtime._pending(rt) == [present]


def test_malformed_brief_fails_without_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "junk.json").write_text("{not json")

    rt = runtime.main(session, drain=True)

    assert (rt.failed / "junk.json").exists()
    # only the boot seal is on the chain: a malformed brief has nothing to
    # attribute a note to (no runtime.task_error).
    assert [r["kind"] for r in rt.log.rows()] == ["runtime.boot"]


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


def test_heartbeat_beats_while_a_brief_is_running(tmp_path, monkeypatch):
    """The beat must survive a brief, not pause for it.

    It used to be stamped by the poll loop, which only comes back around BETWEEN
    briefs -- so a runtime an hour into an rsi chain and a runtime that had been
    dead an hour produced the identical stale badge. The beat now runs in a
    daemon thread, so this drives the REAL main() with a _process that just
    takes its time (no sim needed: the point under test is the loop, not the
    task) and asserts the stamp advanced WHILE that call was blocking."""
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    _drop(session / "inbox", "slow.json", {"kind": "task", "task": "stack"})
    monkeypatch.setattr(runtime, "HEARTBEAT_S", 0.05)

    def slow(rt, path):          # stands in for a long brief: blocks the loop
        path.unlink()
        time.sleep(0.6)

    monkeypatch.setattr(runtime, "_process", slow)
    runtime.main(session, drain=True)
    status = bs.read_runtime_status(session)
    assert status["heartbeat_ts"] - status["boot_ts"] >= 0.4, \
        "no beat landed during the brief -- the badge is back to 'busy or dead'"


def test_clean_exit_marks_the_status_stopped(tmp_path):
    """A runtime that returns normally says so, so its leftover status file
    cannot pass for a live one. Belt-and-braces: a kill -9 never gets here,
    which is why alive is decided against /proc rather than trusting this."""
    session = tmp_path / "session-main"
    runtime.main(session, drain=True)     # nothing pending -> returns at once
    status = bs.read_runtime_status(session)
    assert status["stopped_ts"] >= status["boot_ts"]
    assert status["alive"] is False


def test_boot_and_heartbeat_stamp_liveness(tmp_path):
    """runtime_status.json carries heartbeat_ts from boot on, and _heartbeat
    re-stamps ONLY that field (atomic rewrite; every other boot-written field
    rides through verbatim). boot_ts alone cannot tell a live runtime from a
    dead one -- the board face passes heartbeat_ts through as an age."""
    session = tmp_path / "session-main"
    runtime.main(session, drain=True)  # boot writes the status file
    status = bs.read_runtime_status(session)
    assert status["heartbeat_ts"] == status["boot_ts"] > 0
    stale = dict(status, heartbeat_ts=1.0)
    (session / "runtime_status.json").write_text(json.dumps(stale))
    runtime._heartbeat(session)
    after = bs.read_runtime_status(session)  # board read: verbatim passthrough
    assert after["heartbeat_ts"] > 1.0
    assert after["boot_ts"] == status["boot_ts"] and after["pid"] == status["pid"]


def test_heartbeat_without_status_file_is_a_noop(tmp_path):
    runtime._heartbeat(tmp_path)  # no file -> no crash, none conjured
    assert not (tmp_path / "runtime_status.json").exists()


def test_brief_with_injected_provider_ref_is_rejected(tmp_path, monkeypatch):
    """An unknown key (e.g. a smuggled provider ref) fails loudly to failed/ --
    rejection, not silent neutralization (round 94 hardening)."""
    from scripts import harness_runtime as hr
    calls = []
    monkeypatch.setattr(hr, "_run_task", lambda *a, **k: calls.append(a))
    session = tmp_path / "s"
    rt = hr.boot(session)
    evil = {"task": "stack", "seed": 90001, "policy": "evil.module:provider"}
    tmp = tmp_path / "evil.json.tmp"
    tmp.write_text(json.dumps(evil))
    os.replace(tmp, rt.inbox / "evil.json")
    hr.main(session, drain=True)
    assert not calls, "injected-key brief must never reach _run_task"
    assert (rt.failed / "evil.json").exists()
    reloaded = SessionLog.load(session / "session-log")
    errors = [row for row in reloaded.rows() if row["kind"] == "runtime.task_error"]
    assert len(errors) == 1 and "unknown brief keys" in errors[0]["data"]["error"]
    assert reloaded.verify()
