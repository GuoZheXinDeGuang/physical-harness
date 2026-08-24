"""The mission-progress aggregate (board.store.session_progress) is the SAME
function on all three call-faces.

Same Round-95 discipline as test_cards / test_storecli: the CLI subcommand's
stdout, the MCP tool result, and board.store.session_progress() must be one
byte-identical dict -- the cockpit's progress panel renders exactly what the LLM
gets, no second statistics layer. And the shared safe_child guard must reject a
``../`` name. Runs against a real SessionLog chain (production on-disk shapes),
not a hand-mocked one.
"""

from __future__ import annotations

import json

from test_read_session import _session

from board import mcp_server as ms
from board import store as bs
from board import storecli


def _run(capsys, *argv) -> tuple[int, str]:
    code = storecli.main(list(argv))
    return code, capsys.readouterr().out.rstrip("\n")


def test_three_faces_are_byte_identical(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    expected = bs.session_progress(runs / "session-main")

    ms.configure(runs)
    code, out = _run(capsys, "session_progress", "session-main", "--runs", str(runs))
    assert code == 0
    assert out == json.dumps(expected)                              # CLI face
    assert json.dumps(ms.session_progress("session-main")) == json.dumps(expected)  # MCP face


def test_folds_plan_complete_rows_into_counts(tmp_path):
    # _session seeds two plan_complete rows (one success with a single passed
    # stage, one failure with 0 replans->3 and one fault, empty nodes) + one
    # runtime.task_error. The fold is exercised against that real chain.
    runs = tmp_path / "runs"
    runs.mkdir()
    p = bs.session_progress(_session(runs, "session-main"))
    assert (p["tasks"], p["succeeded"], p["failed"]) == (2, 1, 1)
    assert p["replans"] == 3 and p["faults"] == 1 and p["task_errors"] == 1
    assert (p["stages"], p["stages_passed"], p["stage_pass_rate"]) == (1, 1, 1.0)
    assert p["latest"]["goal"] == "clear table" and p["latest"]["success"] is False


def test_empty_session_has_no_rate_and_no_latest(tmp_path):
    # A booted session with no sealed task yet: honest "no rate", not 0/0.
    runs = tmp_path / "runs"
    runs.mkdir()
    from harness.events import SessionLog
    SessionLog(runs / "session-main" / "session-log").append("runtime.boot", {"mode": "execution"})
    p = bs.session_progress(runs / "session-main")
    assert p["tasks"] == 0 and p["stage_pass_rate"] is None and p["latest"] is None


def test_traversal_name_rejected_by_shared_guard(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    code, out = _run(capsys, "session_progress", "../session-main", "--runs", str(runs))
    assert code == 3 and json.loads(out) == {"error": "unknown session"}
