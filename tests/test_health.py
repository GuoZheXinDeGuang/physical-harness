"""Liveness the pipeline can actually SEE: stalled briefs, poison briefs, health.

Three incidents, one shape -- a piece of the pipeline was dead and every face
still read normal. 2026-08-27: an RSI brief sat 21h in a session whose runtime
had died. 2026-08-28: the web process was reaped, the runtimes lived on, the UI
was gone. 2026-08-29: session-robocasa's runtime was long dead, its leftover
``runtime_status.json`` made every face answer "runtime alive", and a brief held
queue position 1 forever.

What the three have in common is that ``runtime_status.json`` is a FILE and a
file outlives its writer. So the checks here are all the same check from three
angles: liveness must come from /proc (``runtime_liveness``), a brief nobody can
claim must SAY so (``brief_status`` -> ``stalled``), a brief that keeps killing
the runtime must stop coming back (``harness_runtime._requeue``), and one call
must answer "is my system healthy" (``health``).

Nothing here touches runs/ or spawns a runtime: the dead-pid cases use a pid that
provably cannot exist, and the live-pid case uses THIS pytest process, which is
not a harness_runtime -- which is exactly what the /proc identity guard must
notice.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from board import store as bs
from board import storecli
from harness.events import SessionLog
from scripts import harness_runtime as runtime

#: A pid that cannot be live: the kernel's own ceiling plus one.
_DEAD_PID = int(Path("/proc/sys/kernel/pid_max").read_text()) + 1


def _session(tmp_path: Path, name: str = "session-main") -> Path:
    session = tmp_path / name
    for sub in ("inbox", "processing", "done", "failed", "cancelled"):
        (session / sub).mkdir(parents=True)
    SessionLog(session / "session-log").append("runtime.boot", {"mode": "execution"})
    return session


def _status(session: Path, pid: int, **over) -> None:
    """The live-status file a booted runtime leaves behind -- and keeps leaving
    behind after it dies, which is the whole problem."""
    (session / "runtime_status.json").write_text(json.dumps(
        {"pid": pid, "mode": "execution", "boot_ts": 1.0,
         "heartbeat_ts": 1.0, **over}))


# --- runtime_liveness: ask /proc, never the file ----------------------------


def test_a_leftover_status_file_does_not_vouch_for_a_dead_runtime(tmp_path):
    """The 2026-08-29 incident in one assertion: the file says pid N, N is gone,
    and the answer must be "not alive" with the reason named."""
    session = _session(tmp_path)
    _status(session, _DEAD_PID)
    live = bs.runtime_liveness(session)
    assert live["alive"] is False and live["pid"] == _DEAD_PID
    assert "stale runtime_status.json" in live["reason"]
    # the raw read stays what it always was: reports the pid, judges nothing
    assert bs.read_runtime_status(session)["pid"] == _DEAD_PID


def test_a_live_pid_that_is_not_this_sessions_runtime_is_not_alive(tmp_path):
    """pid RECYCLING, the other half. This box runs three runtimes, so a number
    handed to another process -- here, pytest itself -- must not vouch."""
    session = _session(tmp_path)
    _status(session, os.getpid())
    live = bs.runtime_liveness(session)
    assert live["alive"] is False
    assert "is not a harness_runtime serving" in live["reason"]


def test_a_session_that_never_booted_says_so(tmp_path):
    live = bs.runtime_liveness(_session(tmp_path))
    assert live["alive"] is False and live["pid"] is None
    assert "never booted" in live["reason"]


def test_heartbeat_age_is_reported_not_judged(tmp_path):
    """A runtime mid-brief does not beat for hours (harness_runtime.main's own
    ponytail note), so age is DATA here; only health() -- which can also see
    processing/ -- is allowed to turn it into a verdict."""
    session = _session(tmp_path)
    _status(session, _DEAD_PID, heartbeat_ts=1.0)
    assert bs.runtime_liveness(session)["heartbeat_age_s"] > 1e9


# --- brief_status: queued + no runtime = stalled ----------------------------


def test_queued_with_a_dead_runtime_reads_stalled_not_queued(tmp_path):
    """"queued, position 1" is honest and useless when nothing will ever claim
    it. The directory answer survives as stalled_from."""
    session = _session(tmp_path)
    _status(session, _DEAD_PID)
    (session / "inbox" / "b.json").write_text('{"task": "stack"}')

    res = bs.brief_status(session, "b.json")

    assert res["state"] == "stalled" and res["stalled_from"] == "queued"
    assert res["queue_position"] == 1, "the queue facts still ride along"
    assert res["runtime"]["alive"] is False
    assert "stale runtime_status.json" in res["runtime"]["reason"]


def test_a_processing_orphan_reads_stalled_too(tmp_path):
    """A brief claimed by a runtime that then died is the same problem wearing a
    different directory -- one word for both, so neither hides."""
    session = _session(tmp_path)
    _status(session, _DEAD_PID)
    (session / "processing" / "b.json").write_text('{"task": "stack"}')
    res = bs.brief_status(session, "b.json")
    assert res["state"] == "stalled" and res["stalled_from"] == "running"


def test_a_finished_brief_is_never_stalled(tmp_path):
    """Terminal states are facts about work already done; a dead runtime cannot
    retroactively unmake them."""
    session = _session(tmp_path)
    _status(session, _DEAD_PID)
    for sub in ("done", "failed", "cancelled"):
        (session / sub / f"{sub}.json").write_text("{}")
        res = bs.brief_status(session, f"{sub}.json")
        assert res["state"] == sub and "stalled_from" not in res


def test_stalled_never_long_polls(tmp_path):
    """Waiting 30s for a dead runtime to claim something is the non-answer this
    face exists to stop giving."""
    session = _session(tmp_path)
    _status(session, _DEAD_PID)
    (session / "inbox" / "b.json").write_text("{}")
    start = __import__("time").monotonic()
    res = bs.brief_status(session, "b.json", wait_ms=5000)
    assert res["state"] == "stalled"
    assert __import__("time").monotonic() - start < 1.0


def test_every_reply_carries_the_sessions_liveness(tmp_path):
    """So a caller polling one brief can never again report progress about a
    session whose runtime is gone."""
    session = _session(tmp_path)
    (session / "done" / "b.json").write_text("{}")
    assert bs.brief_status(session, "b.json")["runtime"]["alive"] is False
    assert bs.brief_status(session, "nope.json")["runtime"]["alive"] is False


# --- the crash loop: a poison brief must stop coming back -------------------


def test_a_brief_that_keeps_killing_the_runtime_is_filed_not_re_queued(tmp_path):
    """Crash recovery is at-least-once and WAS unbounded: a brief that takes the
    process down (a segfaulting sim, an OOM kill -- nothing _process's try/except
    can catch) came back every boot and killed it again, forever, and the only
    symptom was a runtime that would not stay up."""
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    (rt.processing / "poison.json").write_text('{"kind": "task", "task": "stack"}')

    for attempt in range(runtime._MAX_REQUEUES):
        rt = runtime.boot(session)  # simulate the crash: reboot with it stranded
        assert (rt.inbox / "poison.json").exists(), f"re-queue {attempt + 1}"
        os.replace(rt.inbox / "poison.json", rt.processing / "poison.json")

    rt = runtime.boot(session)
    assert not (rt.inbox / "poison.json").exists(), "the loop must end"
    assert (rt.failed / "poison.json").exists()
    row = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"][-1]
    assert row["data"]["brief"] == "poison.json" and "crash-loop" in row["data"]["error"]
    # and the chain row is what brief_status reports as the outcome
    assert bs.brief_status(session, "poison.json")["outcome"]["chain_kind"] == \
        "runtime.task_error"


def test_the_requeue_counter_forgets_a_brief_that_finished(tmp_path):
    """One crash then a clean run must not leave a strike on the record: the map
    is rebuilt from what is stranded RIGHT NOW, so there is no reaper to forget."""
    session = tmp_path / "session-main"
    rt = runtime.boot(session)
    (rt.processing / "a.json").write_text("{}")
    rt = runtime.boot(session)
    assert json.loads((session / runtime.REQUEUE_FILE).read_text()) == {"a.json": 1}
    os.replace(rt.inbox / "a.json", rt.done / "a.json")  # it finished
    runtime.boot(session)
    assert json.loads((session / runtime.REQUEUE_FILE).read_text()) == {}


# --- health: the one first command ------------------------------------------


def _free_port() -> int:
    """A port nothing is listening on -- bind it, read it, close it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_health_names_the_session_the_briefs_are_rotting_in(tmp_path):
    runs = tmp_path / "runs"
    dead = _session(runs, "session-robocasa")
    _status(dead, _DEAD_PID)
    (dead / "inbox" / "b.json").write_text("{}")

    h = bs.health(runs, _free_port())

    assert h["ok"] is False
    stuck = [p for p in h["problems"] if p.startswith("session-robocasa:")]
    assert len(stuck) == 1 and "NO RUNTIME" in stuck[0]
    assert "1 queued" in stuck[0]
    row = next(s for s in h["sessions"] if s["name"] == "session-robocasa")
    assert row == {"name": "session-robocasa", "mode": "execution", "alive": False,
                   "pid": _DEAD_PID, "heartbeat_age_s": row["heartbeat_age_s"],
                   "queued": 1, "processing": 0, "done": 0, "failed": 0,
                   "state": "stalled", "reason": row["reason"]}


def test_health_does_not_cry_about_a_retired_session(tmp_path):
    """A stopped runtime with an empty inbox needs no hand. Retired sessions
    outnumber live ones on a real box, and a permanently red health face is one
    nobody reads -- which is how all three incidents stayed invisible."""
    runs = tmp_path / "runs"
    _status(_session(runs, "session-old"), _DEAD_PID)
    h = bs.health(runs, _free_port())
    assert [p for p in h["problems"] if p.startswith("session-old")] == []
    row = next(s for s in h["sessions"] if s["name"] == "session-old")
    assert row["alive"] is False and row["state"] == "dormant"


def test_health_flags_a_stopped_model_only_when_the_operator_wants_one(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "_find_model_server", lambda: None)
    monkeypatch.setattr(bs, "_model_health", lambda: (False, None))
    monkeypatch.delenv("PH_WITH_MODEL", raising=False)
    assert not [p for p in bs.health(tmp_path / "runs", _free_port())["problems"]
                if p.startswith("model:")]
    monkeypatch.setenv("PH_WITH_MODEL", "1")
    flagged = [p for p in bs.health(tmp_path / "runs", _free_port())["problems"]
               if p.startswith("model:")]
    assert len(flagged) == 1 and "cockpit --with-model" in flagged[0] \
        and "storecli model_server start" in flagged[0]


def test_health_flags_a_dead_console_even_when_every_runtime_is_fine(tmp_path):
    """The 2026-08-28 incident: the runtimes were all healthy and the one surface
    the operator actually uses was gone."""
    h = bs.health(tmp_path / "runs", _free_port())
    assert h["console"]["serving"] is False
    assert any("console" in p for p in h["problems"])


def test_health_sees_a_listening_console(tmp_path):
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        h = bs.health(tmp_path / "runs", srv.getsockname()[1])
    assert h["console"]["serving"] is True
    assert not [p for p in h["problems"] if "console" in p]


def test_health_flags_processing_orphans_under_a_live_runtime(tmp_path):
    """A runtime claims serially, so a second file in processing/ is a crash
    orphan -- invisible until the next boot re-queues it."""
    runs = tmp_path / "runs"
    session = _session(runs, "session-main")
    _status(session, _DEAD_PID)
    for n in ("a.json", "b.json"):
        (session / "processing" / n).write_text("{}")
    # dead runtime: reported as the waiting-briefs problem, not as orphans
    assert any("2 claimed" in p for p in bs.health(runs, _free_port())["problems"])


def test_health_skips_campaign_stores(tmp_path):
    """A campaign store carries a session-log too but has NO inbox; 20 archived
    stores in this list would bury the four sessions that take briefs."""
    runs = tmp_path / "runs"
    _session(runs, "session-main")
    store = runs / "stack-g1"
    SessionLog(store / "session-log").append("runtime.boot", {"mode": "execution"})
    names = [s["name"] for s in bs.health(runs, _free_port())["sessions"]]
    assert names == ["session-main"]


def test_health_never_raises_on_a_missing_runs_dir(tmp_path):
    """The command an operator reaches for when things are broken must not be
    the next broken thing."""
    h = bs.health(tmp_path / "nope", _free_port())
    assert h["sessions"] == [] and isinstance(h["model"], dict)


def test_cli_face_matches_the_board_call(tmp_path):
    """MCP and CLI are two call faces of ONE function (the charter): the console
    panel and the operator's terminal must never disagree about system health."""
    runs = tmp_path / "runs"
    _status(_session(runs, "session-main"), _DEAD_PID)
    port = _free_port()
    cli = storecli.dispatch("health", str(port), runs, tmp_path / "STATUS.md",
                            tmp_path / "progress.md")
    board = bs.health(runs, port)
    assert cli["console"] == board["console"] == {"port": port, "serving": False}
    assert [s["name"] for s in cli["sessions"]] == [s["name"] for s in board["sessions"]]
    assert cli["ok"] == board["ok"]
