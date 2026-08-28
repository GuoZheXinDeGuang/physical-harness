"""The brief lifecycle: ask where one is, and stop it.

Two holes this covers. (1) A long mission used to come back ``timeout`` from the
blocking run_task while the runtime was finishing it fine -- so ``brief_status``
must report queue position, how long the thing AHEAD has been running, and the
outcome, in one call. (2) There was no way to stop anything -- so ``cancel_brief``
must end a brief as ``cancelled``, its OWN sealed ending, never as an error.

The runtime half runs for real (drain mode with a monkeypatched rollout, the
test_runtime_drain 手法): the cancel checkpoints are in the real ``_process`` and
the real ``workload.run`` node loop, so what is faked is the simulator, never the
lifecycle under test. Nothing here touches runs/.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

from board import store as bs
from harness.events import SessionLog
from plugins.task import workload
from scripts import harness_runtime as runtime


def _ok_rollout(spec, bundle=None):
    return {"success": True, "steps": 10, "stages": [
        {"name": "grasp", "success": True}, {"name": "place", "success": True}]}


def _drop(inbox: Path, name: str, brief: dict) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps(brief))
    os.replace(tmp, inbox / name)


def _session(tmp_path: Path) -> Path:
    """A session tree with the five intake dirs and a booted chain, no runtime."""
    session = tmp_path / "session-main"
    for sub in ("inbox", "processing", "done", "failed", "cancelled"):
        (session / sub).mkdir(parents=True)
    SessionLog(session / "session-log").append("runtime.boot", {"mode": "execution"})
    return session


def _feed(session: Path, *events: dict) -> None:
    """Write the operational feed directly -- opstream's on-disk shape."""
    (session / "runtime_events.jsonl").write_text(
        "".join(json.dumps({"seq": i + 1, "ts": 1000.0, **e}) + "\n"
                for i, e in enumerate(events)))


# --- brief_status: state, queue, outcome ------------------------------------


def test_state_is_the_directory_that_holds_it(tmp_path, live_runtime):
    """...as long as a runtime is actually serving the session. Without one, an
    unfinished brief reads ``stalled`` instead -- see tests/test_health.py."""
    session = _session(tmp_path)
    live_runtime(session)
    for sub, state in (("inbox", "queued"), ("processing", "running"),
                       ("done", "done"), ("failed", "failed"),
                       ("cancelled", "cancelled")):
        (session / sub / f"{sub}.json").write_text('{"task": "stack"}')
        res = bs.brief_status(session, f"{sub}.json")
        assert res["state"] == state and res["task"] == "stack"
        assert res["brief_id"] == f"{sub}.json" and res["session"] == "session-main"
    assert bs.brief_status(session, "never-existed.json")["state"] == "unknown"


def test_traversal_brief_id_is_rejected_by_the_shared_guard(tmp_path):
    session = _session(tmp_path)
    (tmp_path / "secret.json").write_text("{}")
    assert bs.brief_status(session, "../../secret.json")["state"] == "unknown"
    assert bs.cancel_brief(session, "../../secret.json") == {
        "brief_id": "../../secret.json", "session": "session-main",
        "state": "unknown", "requested": False, "error": "unknown brief"}
    assert not (session / "cancel").exists(), "a rejected id writes no marker"


def test_queue_position_follows_the_runtime_claim_order(tmp_path):
    """mtime order, because that is what harness_runtime._pending claims in --
    a position the runtime does not honor would be worse than none."""
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    for i, name in enumerate(("c.json", "a.json", "b.json")):
        _drop(rt.inbox, name, {"task": "stack"})
        os.utime(rt.inbox / name, (1000 + i, 1000 + i))
    # the runtime's own claim order is the definition; the board must mirror it
    order = [p.name for p in runtime._pending(rt)]
    assert order == ["c.json", "a.json", "b.json"]
    assert [bs.brief_status(session, n)["queue_position"] for n in order] == [1, 2, 3]


def test_ahead_running_s_separates_just_started_from_three_hours_in(
        tmp_path, live_runtime):
    session = _session(tmp_path)
    live_runtime(session)
    _drop(session / "inbox", "mine.json", {"task": "stack"})
    (session / "processing" / "ahead.json").write_text('{"task": "recycle_cans"}')
    # the claim ts is the clock, not the file (os.rename keeps the drop mtime)
    (session / "runtime_events.jsonl").write_text(json.dumps(
        {"seq": 1, "ts": time.time() - 10800, "kind": "task_claimed",
         "brief": "ahead.json"}) + "\n")

    res = bs.brief_status(session, "mine.json")

    assert res["state"] == "queued" and res["queue_position"] == 1
    assert res["ahead_running_s"] > 10000, "position 2 behind a 3h run says so"

    # the same position with nothing actually running is a different fact
    os.replace(session / "processing" / "ahead.json", session / "done" / "ahead.json")
    assert bs.brief_status(session, "mine.json")["ahead_running_s"] == 0.0


def test_events_are_sliced_by_the_claim_boundary(tmp_path):
    """task.plan_complete carries no brief id, so a brief's events are the feed
    between ITS task_claimed and its task_done -- the runtime claims serially."""
    session = _session(tmp_path)
    (session / "done" / "mine.json").write_text('{"task": "stack"}')
    _feed(session,
          {"kind": "task_claimed", "brief": "other.json"},
          {"kind": "plan_complete", "success": False},
          {"kind": "task_done", "brief": "other.json"},
          {"kind": "task_claimed", "brief": "mine.json"},
          {"kind": "node_start", "node": "n0"},
          {"kind": "plan_complete", "success": True, "goal": "stacked",
           "replans": 1, "actuations": 2},
          {"kind": "task_done", "brief": "mine.json"},
          {"kind": "task_claimed", "brief": "later.json"},
          {"kind": "node_start", "node": "n9"})

    res = bs.brief_status(session, "mine.json")

    assert [e["kind"] for e in res["events"]] == [
        "task_claimed", "node_start", "plan_complete", "task_done"]
    assert res["outcome"]["success"] is True and res["outcome"]["goal"] == "stacked"
    assert res["outcome"]["replans"] == 1


def test_outcome_prefers_the_chain_row_that_names_the_brief(tmp_path):
    session = _session(tmp_path)
    (session / "failed" / "bad.json").write_text('{"task": "stack"}')
    SessionLog.load(session / "session-log").append(
        "runtime.task_error", {"brief": "bad.json", "task": "stack",
                               "error": "ValueError('unknown brief keys')"})

    res = bs.brief_status(session, "bad.json")

    assert res["state"] == "failed"
    assert res["outcome"]["chain_kind"] == "runtime.task_error"
    assert "unknown brief keys" in res["outcome"]["error"]


def test_wait_ms_returns_the_current_state_not_a_timeout(tmp_path, live_runtime):
    """The whole point: waiting out the cap is an ANSWER ('still running'), the
    failure mode that sent agents digging through runs/ by hand. It only blocks
    while a runtime is alive to change the answer -- a stalled brief answers at
    once (test_health.py)."""
    session = _session(tmp_path)
    live_runtime(session)
    (session / "processing" / "slow.json").write_text('{"task": "recycle_cans"}')

    start = time.monotonic()
    res = bs.brief_status(session, "slow.json", wait_ms=1200)

    assert 1.0 < time.monotonic() - start < 3.0, "it really blocked"
    assert res["state"] == "running" and "error" not in res


def test_wait_ms_never_blocks_on_a_terminal_state(tmp_path):
    session = _session(tmp_path)
    (session / "done" / "fin.json").write_text('{"task": "stack"}')
    start = time.monotonic()
    assert bs.brief_status(session, "fin.json", wait_ms=5000)["state"] == "done"
    assert time.monotonic() - start < 0.5


# --- cancel: queued, running, and the ones you cannot cancel -----------------


def test_cancel_writes_one_marker_and_touches_nothing_else(tmp_path):
    session = _session(tmp_path)
    _drop(session / "inbox", "q.json", {"task": "stack"})

    res = bs.cancel_brief(session, "q.json")

    assert res["requested"] is True and res["state"] == "queued"
    assert json.loads((session / "cancel" / "q.json").read_text())["ts"] > 0
    assert (session / "inbox" / "q.json").exists(), "the board never moves a brief"
    # ...and never writes the chain: the runtime is its one writer
    assert [r["kind"] for r in SessionLog.load(session / "session-log").rows()] \
        == ["runtime.boot"]


def test_cancel_of_a_finished_brief_does_nothing(tmp_path):
    session = _session(tmp_path)
    for sub in ("done", "failed", "cancelled"):
        (session / sub / f"{sub}.json").write_text("{}")
        res = bs.cancel_brief(session, f"{sub}.json")
        assert res["requested"] is False
        assert res["error"] == f"already {sub}; nothing to cancel"
    assert not (session / "cancel").exists()


def test_queued_brief_is_cancelled_at_the_claim_not_run(tmp_path, monkeypatch):
    """The marker is read AFTER the atomic claim, so cancelling a queued brief
    races nothing: whoever wins the rename is whoever reads the marker."""
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    _drop(rt.inbox, "q.json", {"kind": "task", "task": "stack", "seed": 90000})
    _drop(rt.inbox, "keep.json", {"kind": "task", "task": "stack", "seed": 90001})
    assert bs.cancel_brief(session, "q.json")["requested"] is True

    runtime.main(session, drain=True)

    assert (session / "cancelled" / "q.json").exists()
    assert (rt.done / "keep.json").exists(), "the loop kept going"
    rows = SessionLog.load(session / "session-log").rows()
    cancels = [r for r in rows if r["kind"] == "runtime.task_cancelled"]
    assert len(cancels) == 1
    assert cancels[0]["data"] == {"brief": "q.json", "task": "stack",
                                  "stage": "queued"}
    # a cancel is NOT an error and NOT a completed task
    assert not [r for r in rows if r["kind"] == "runtime.task_error"]
    assert len([r for r in rows if r["kind"] == "task.plan_complete"]) == 1
    assert not (session / "cancel" / "q.json").exists(), "the marker was consumed"
    assert SessionLog.load(session / "session-log").verify()

    # and the board reads it back as its own state, with the sealed outcome
    res = bs.brief_status(session, "q.json")
    assert res["state"] == "cancelled"
    assert res["outcome"]["chain_kind"] == "runtime.task_cancelled"


def test_running_mission_stops_at_a_node_boundary(tmp_path, monkeypatch):
    """The check sits BEFORE dispatch, so the node in flight finishes and a
    persistent episode is never torn mid-segment. clear_table has two nodes: the
    cancel fires between them, so exactly one rollout runs."""
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    _drop(rt.inbox, "live.json", {"kind": "task", "task": "clear_table",
                                  "seed": 90002})
    dispatched = []

    def cancel_after_first(node, ctx):
        dispatched.append(node["id"])
        bs.cancel_brief(session, "live.json")     # the operator, mid-mission
        return workload._manipulate(node, ctx)
    monkeypatch.setitem(workload._KIND_HANDLERS, "manipulate", cancel_after_first)

    runtime.main(session, drain=True)

    assert len(dispatched) == 1, "the cancel landed at the next node boundary"
    assert (session / "cancelled" / "live.json").exists()
    rows = SessionLog.load(session / "session-log").rows()
    assert [r["data"]["stage"] for r in rows
            if r["kind"] == "runtime.task_cancelled"] == ["running"]
    # the partial is sealed and says, in its own faults, that a human stopped it
    done = next(r for r in rows if r["kind"] == "task.plan_complete")
    assert done["data"]["success"] is False
    assert [f["kind"] for f in done["data"]["faults"]] == ["cancelled"]
    assert SessionLog.load(session / "session-log").verify()


def test_a_cancel_that_arrives_too_late_cancels_nothing(tmp_path, monkeypatch):
    """stack is a ONE-node mission: the stop lands after the only node ran, so
    the work IS done. Filing a finished mission under cancelled/ would be the
    same lie as filing a stopped one under done/."""
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    _drop(rt.inbox, "late.json", {"kind": "task", "task": "stack", "seed": 90004})

    def cancel_during(node, ctx):
        bs.cancel_brief(session, "late.json")
        return workload._manipulate(node, ctx)
    monkeypatch.setitem(workload._KIND_HANDLERS, "manipulate", cancel_during)

    runtime.main(session, drain=True)

    assert (rt.done / "late.json").exists()
    rows = SessionLog.load(session / "session-log").rows()
    assert not [r for r in rows if r["kind"] == "runtime.task_cancelled"]
    assert next(r for r in rows
                if r["kind"] == "task.plan_complete")["data"]["success"] is True
    assert not (session / "cancel" / "late.json").exists(), \
        "a marker that cancelled nothing must not outlive its brief"


def test_a_cancelled_run_is_not_a_failed_one(tmp_path):
    """The two-state extension: session_progress must never let an operator's
    stop read later as a capability the harness lacks."""
    session = _session(tmp_path)
    log = SessionLog.load(session / "session-log")
    log.append("task.plan_complete", {"success": False, "faults": [], "nodes": {}})
    log.append("task.plan_complete", {
        "success": False, "nodes": {"n0": {"success": True, "stages": [
            {"name": "grasp", "success": True}]}},
        "faults": [{"kind": "cancelled", "node": "n1"}]})

    prog = bs.session_progress(session)

    assert prog["tasks"] == 1 and prog["failed"] == 1 and prog["cancelled"] == 1
    assert prog["stages"] == 1 and prog["stages_passed"] == 1, \
        "the stages that ran are still evidence"
    assert prog["latest"]["faults"][0]["kind"] == "cancelled"


# --- campaign/rsi subprocess: kill the GROUP --------------------------------


def test_cancelled_subprocess_dies_by_group_with_its_children(tmp_path):
    """A campaign is a worker POOL. Signalling only the parent leaves orphans
    burning GPU, so the stop is os.killpg over a group the child OWNS
    (start_new_session) -- which also keeps the runtime out of the blast radius.
    """
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    out = tmp_path / "store"
    # a "pool": the parent spawns a child, and a lone parent kill would orphan it
    childfile = tmp_path / "child.pid"
    script = (f"import subprocess,time,sys;"
              f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
              f"open({str(childfile)!r},'w').write(str(p.pid));"
              f"time.sleep(60)")
    (rt.inbox.parent / "cancel" / "camp.json").write_text("{}")  # already asked

    try:
        runtime._run_watched([sys.executable, "-c", script], dict(os.environ),
                             rt, "camp.json", out, "campaign")
        raise AssertionError("a cancelled subprocess must raise")
    except RuntimeError as exc:
        assert "cancelled by the operator" in str(exc)

    child_pid = int(childfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        os.kill(child_pid, signal.SIGKILL)
        raise AssertionError("the pool child survived: only the parent was killed")

    # the half-written store is marked, so no reader mistakes it for a result
    assert json.loads((out / "CANCELLED").read_text())["brief"] == "camp.json"


def test_watched_subprocess_returns_output_when_not_cancelled(tmp_path):
    """The non-cancel path is still plain subprocess.run: exit code + stderr,
    drained (a chatty campaign must not deadlock on a full pipe)."""
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    code, err = runtime._run_watched(
        [sys.executable, "-c",
         "import sys; sys.stderr.write('x' * 200000); sys.exit(3)"],
        dict(os.environ), rt, "nope.json", tmp_path / "out", "campaign")
    assert code == 3 and len(err) == 200000


# --- the CLI face -----------------------------------------------------------


def test_cli_face_matches_the_board_call(tmp_path, capsys):
    from board import storecli
    runs = tmp_path / "runs"
    runs.mkdir()
    session = _session(runs)
    _drop(session / "inbox", "q.json", {"task": "stack"})
    base = ["--runs", str(runs), "--session", "session-main"]

    assert storecli.main(["brief_status", "q.json", *base]) == 0
    assert json.loads(capsys.readouterr().out) == bs.brief_status(session, "q.json")
    assert storecli.main(["cancel_brief", "q.json", *base]) == 0
    assert json.loads(capsys.readouterr().out)["requested"] is True
    assert (session / "cancel" / "q.json").exists()

    assert storecli.main(["brief_status", "q.json", "--runs", str(runs),
                          "--session", "../oops"]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "unknown session"}
    assert storecli.main(["brief_status", *base]) == 3   # no brief id
