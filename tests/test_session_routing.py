"""Phase-5 routing: the ``session`` parameter across the three board faces.

Multi-runtime means submit_brief/run_task must ROUTE a brief to the right
session's inbox (not just the one hardwired session-main), and the session reads
must address any resident runtime. The three faces (board.store fn / storecli /
mcp_server) share ONE guard: a session name is resolved through
board.store.safe_child(is_session) -- traversal rejected, only a real booted
session accepted -- and defaults to session-main so single-runtime behavior is
byte-identical.

The fixture reuses test_read_session._session (a real verifiable SessionLog
chain), so routing is exercised against the exact on-disk session shape the
runtime produces, not a hand-mocked one.
"""

from __future__ import annotations

from pathlib import Path

from test_read_session import _session

from board import mcp_server as ms
from board import store as bs
from board import storecli


def _runs(tmp_path: Path, *names: str) -> Path:
    """A runs/ dir carrying one real session per name."""
    runs = tmp_path / "runs"
    runs.mkdir()
    for name in names:
        _session(runs, name)
    return runs


# --- face 1: board.store.session_inbox (the shared write-routing guard) ------


def test_session_inbox_resolves_real_session(tmp_path):
    runs = _runs(tmp_path, "session-main", "session-robocasa")
    assert bs.session_inbox(runs, "session-robocasa") == runs / "session-robocasa" / "inbox"
    assert bs.session_inbox(runs, "session-main") == runs / "session-main" / "inbox"


def test_session_inbox_rejects_traversal_and_non_session(tmp_path):
    runs = _runs(tmp_path, "session-main")
    # a campaign store (index.jsonl, no session-log) is NOT a routable session
    (runs / "stack-g1" / "artifacts").mkdir(parents=True)
    (runs / "stack-g1" / "index.jsonl").write_text('{"seq":0,"kind":"x","sha":"y"}\n')
    for bad in ("../session-main", "..", "../../etc", "nope", "stack-g1", ""):
        assert bs.session_inbox(runs, bad) is None, bad


# --- face 2: storecli CLI (session reads default to main, traversal rejected) -


def test_storecli_session_read_defaults_to_main(tmp_path):
    runs = _runs(tmp_path, "session-main", "session-robocasa")
    dummy = tmp_path / "STATUS.md"
    # no name -> defaults to session-main
    assert storecli.dispatch("session", None, runs, dummy, dummy)["name"] == "session-main"
    # explicit name routes to that session
    assert storecli.dispatch("session", "session-robocasa", runs, dummy, dummy)["name"] == "session-robocasa"


def test_storecli_session_read_rejects_traversal(tmp_path):
    runs = _runs(tmp_path, "session-main")
    dummy = tmp_path / "STATUS.md"
    for fn in ("session", "runtime_status", "runtime_events", "session_progress"):
        try:
            storecli.dispatch(fn, "../session-main", runs, dummy, dummy)
        except ValueError as exc:
            assert "unknown session" in str(exc)
        else:
            raise AssertionError(f"{fn} did not reject traversal name")


# --- face 3: mcp_server tools (submit_brief/run_task route; reads default) ----


def test_mcp_submit_default_routes_to_main(tmp_path):
    runs = _runs(tmp_path, "session-main")
    ms.configure(runs)
    res = ms.submit_brief({"kind": "task", "task": "stack", "seed": 1})
    assert (runs / "session-main" / "inbox" / res["submitted"]).exists()
    assert res["inbox"] == str(runs / "session-main" / "inbox")


def test_mcp_submit_routes_to_named_session(tmp_path):
    runs = _runs(tmp_path, "session-main", "session-robocasa")
    ms.configure(runs)
    res = ms.submit_brief({"kind": "task", "task": "kitchen_thaw", "seed": 1},
                          session="session-robocasa")
    assert (runs / "session-robocasa" / "inbox" / res["submitted"]).exists()
    # nothing leaked into session-main's inbox
    main_inbox = runs / "session-main" / "inbox"
    assert not (main_inbox.exists() and list(main_inbox.glob("*.json")))


def test_mcp_submit_rejects_unknown_and_traversal_session(tmp_path):
    runs = _runs(tmp_path, "session-main")
    ms.configure(runs)
    for bad in ("../session-main", "session-robocasa", ".."):
        res = ms.submit_brief({"kind": "task", "task": "stack", "seed": 1}, session=bad)
        assert res == {"error": f"unknown session {bad!r}"}, bad
    # a rejected route drops NOTHING anywhere under runs/
    assert not list(runs.rglob("brief-*.json"))


def test_mcp_run_task_rejects_unknown_session(tmp_path):
    runs = _runs(tmp_path, "session-main")
    ms.configure(runs)
    res = ms.run_task("stack", 1, session="../session-main")
    assert res["status"] == "error" and "unknown session" in res["error"]


def test_mcp_session_reads_default_to_main_and_reject_traversal(tmp_path):
    runs = _runs(tmp_path, "session-main", "session-robocasa")
    ms.configure(runs)
    assert ms.session()["name"] == "session-main"                # default
    assert ms.session("session-robocasa")["name"] == "session-robocasa"
    assert ms.session("../session-main") == {"error": "unknown session"}
    assert ms.session_progress()["name"] == "session-main"       # default
    assert ms.runtime_status("../session-main") == {"error": "unknown session"}
