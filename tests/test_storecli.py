"""The CLI face (board/storecli.py) is a byte-thin passthrough.

Round-95 discipline: prove all three call-faces are the SAME function. Each fn's
stdout must equal ``json.dumps(board.store.<fn>(...))`` exactly (so the panel
renders the byte-identical dict the LLM gets from board/mcp_server.py, which
test_mcp_server.py separately pins to board.store), and the shared
board.store.safe_child guard must reject a ``../`` name.

Fixtures reuse the sibling test modules' builders (real CampaignStore + a real
SessionLog chain) so the passthrough is exercised against production on-disk
shapes, not a hand-mocked one.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_read_session import _session
from test_store import _campaign, _mkstore, _paired

from board import store as bs
from board import storecli


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    runs = tmp_path / "runs"
    runs.mkdir()
    _campaign(runs / "stack-g1")
    rescore = {"block": 42200, "n": 200, "judgement": True,
               "paired": _paired(0.60, 0.70, 19, 0, 1e-8, n=200),
               "vs_blind": _paired(0.70, 0.78, 40, 5, 1e-6), "stage_attribution": None}
    _mkstore(runs / "stack-g1-rescore-42200", [("heldout_rescore", rescore)])
    _session(runs, "session-main")
    status = tmp_path / "STATUS.md"
    status.write_text(
        "**PHASE 3 区块预算:** stack-g1 dev 41000-41580;\n"
        "**已烧:** held-out 42000-42199。\n")
    progress = tmp_path / "progress.md"
    progress.write_text("## Round 95 - 2026-08-23 - the cockpit\nbody one\n")
    return runs, status, progress


def _run(capsys, *argv) -> tuple[int, str]:
    code = storecli.main(list(argv))
    return code, capsys.readouterr().out.rstrip("\n")


def test_every_fn_is_byte_identical_to_board_store(tmp_path, capsys):
    runs, status, progress = _fixture(tmp_path)
    base = ["--runs", str(runs), "--status", str(status), "--progress", str(progress)]
    cases = [
        (["list_stores"], bs.list_stores(runs)),
        (["store", "stack-g1"], bs.store_detail(runs / "stack-g1")),
        (["heldout", "stack-g1"], bs.heldout_blocks(runs, "stack-g1")),
        (["sessions"], bs.discover_sessions(runs)),
        (["session", "session-main"], bs.read_session(runs / "session-main")),
        # runtime_status: fixture session has no runtime_status.json, so this
        # case is both the face-equivalence proof AND null-when-absent (both
        # sides serialize to "null").
        (["runtime_status", "session-main"], bs.read_runtime_status(runs / "session-main")),
        (["ledger"], bs.burned_blocks(runs)),
        (["plan_index", "session-main"], bs.plan_index(runs / "session-main")),
        (["rounds"], bs.parse_rounds(progress.read_text())),
    ]
    for argv, expected in cases:
        code, out = _run(capsys, *argv, *base)
        assert code == 0, argv
        assert out == json.dumps(expected), argv
    # non-trivial fixtures, so identity is not identity-of-empty
    assert bs.list_stores(runs) and bs.heldout_blocks(runs, "stack-g1")["blocks"]
    assert bs.burned_blocks(runs) and bs.parse_rounds(progress.read_text())


def test_submit_brief_two_faces_share_one_drop(tmp_path, capsys):
    """The ONE write fn: the CLI face is a passthrough into board.store.
    submit_brief (the same function the MCP tool delegates to), so both faces
    drop the raw brief bytes VERBATIM into the same inbox and answer the same
    {"submitted", "inbox"} shape -- zero validation on this side (the resident
    runtime's _BRIEF_KEYS on claim stays the sole authority)."""
    runs, status, progress = _fixture(tmp_path)
    base = ["--runs", str(runs), "--status", str(status), "--progress", str(progress)]
    raw = '{"kind":"rsi","task":"kitchen_thaw"}'
    code, out = _run(capsys, "submit_brief", "--brief", raw, "--session", "session-main", *base)
    cli = json.loads(out)
    direct = bs.submit_brief(runs, raw, "session-main")
    inbox = runs / "session-main" / "inbox"
    assert code == 0
    assert cli["inbox"] == direct["inbox"] == str(inbox)
    for res in (cli, direct):  # both faces landed the SAME bytes, unparsed
        assert (inbox / res["submitted"]).read_text() == raw


def test_submit_brief_unknown_session_drops_nothing(tmp_path, capsys):
    runs, status, progress = _fixture(tmp_path)
    code, out = _run(capsys, "submit_brief", "--brief", "{}", "--session", "../oops",
                     "--runs", str(runs), "--status", str(status), "--progress", str(progress))
    assert code == 0 and json.loads(out) == {"error": "unknown session '../oops'"}
    assert not list(runs.rglob("brief-*.json"))


def test_traversal_name_rejected_by_shared_guard(tmp_path, capsys):
    runs, status, progress = _fixture(tmp_path)
    base = ["--runs", str(runs), "--status", str(status), "--progress", str(progress)]
    for name in ("../etc", "..", "../STATUS.md"):
        code, out = _run(capsys, "store", name, *base)
        assert code == 3 and json.loads(out) == {"error": "unknown store"}
    code, out = _run(capsys, "session", "../session-main", *base)
    assert code == 3 and json.loads(out) == {"error": "unknown session"}
    code, out = _run(capsys, "runtime_status", "../session-main", *base)
    assert code == 3 and json.loads(out) == {"error": "unknown session"}


def test_unknown_fn_is_a_nonzero_error(tmp_path, capsys):
    runs, status, progress = _fixture(tmp_path)
    code, out = _run(capsys, "nope", "--runs", str(runs))
    assert code == 2 and json.loads(out)["error"].startswith("unknown fn")
