"""The report's Runtime sessions section (rung 2) renders the hash-chain
integrity badge in THREE distinct states -- verified, broken (tamper), and not
verifiable (mid-write) -- so invariant #5 survives the board's retirement (rung
4). Also covers the `python -m board.report --out` headless entry cron will call.

Fixtures reuse the session builder from the sibling test module so the section is
exercised against a real SessionLog chain on the exact on-disk shape the runtime
writes, not a hand-mocked dict.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_read_session import _session

from board import report as br


def _build(runs: Path) -> str:
    # now is pinned so nothing but the session mtime is time-dependent
    return br.build_report(runs, status_text="", progress_text="", now=1_000_000_000.0)


def test_verified_session_renders_chain_verified_badge(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    doc = _build(runs)
    assert 'id="sessions"' in doc
    assert "session-main" in doc
    assert "chain verified" in doc
    assert "chain broken" not in doc and "not verifiable" not in doc
    # the task timeline view is preserved (a goal from the fixture chain)
    assert "cubeA on cubeB" in doc


def test_tampered_session_renders_chain_broken_distinctly(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs, "session-main")
    rows_path = sd / "session-log" / "rows.jsonl"
    lines = rows_path.read_text().splitlines()
    row = json.loads(lines[1])          # a task.plan_complete row
    row["data"]["success"] = False      # flip a value; leave stale sha/chain
    lines[1] = json.dumps(row, sort_keys=True, default=str)
    rows_path.write_text("\n".join(lines) + "\n")
    doc = _build(runs)
    assert "chain broken" in doc
    assert "chain verified" not in doc
    assert "not verifiable" not in doc  # a tamper is DISTINCT from mid-write


def test_midwrite_session_renders_not_verifiable_distinctly(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    sd = _session(runs, "session-main")
    rows_path = sd / "session-log" / "rows.jsonl"
    with rows_path.open("a") as fh:
        fh.write('{"seq": 9, "kind": "task.plan_complete", "data": {"succ')  # mid-write
    doc = _build(runs)
    assert "not verifiable" in doc
    assert "chain broken" not in doc    # DISTINCT from a real tamper
    assert "chain verified" not in doc


def test_headless_out_entry_writes_report(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _session(runs, "session-main")
    out = tmp_path / "report.html"
    rc = br.main(["--runs", str(runs), "--out", str(out)])
    assert rc == 0
    doc = out.read_text()
    assert doc.startswith("<!doctype html>")
    assert 'id="sessions"' in doc and "chain verified" in doc
