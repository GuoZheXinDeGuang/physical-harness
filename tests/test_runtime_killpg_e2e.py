"""A campaign group never outlives the runtime that spawned it.

A campaign/rsi subprocess runs in its OWN session (start_new_session), so a
SIGTERM to the runtime (cockpit --stop) or a runtime crash used to leave it
running and still writing campaigns/<stem>, and the next boot's re-queue
started a second copy into the same store (2026-08-28). This drives the REAL
scripts/harness_runtime.py as a subprocess on a throwaway session with a long
``sleep`` standing in for the campaign (PH_CAMPAIGN_ARGV seam, no sim):
  1. SIGTERM the runtime -> the group is gone, the brief is filed cancelled/.
  2. A stranded processing/ brief + live orphan group -> boot kills the group
     by pgid (logged) and re-queues the brief exactly once.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "scripts" / "harness_runtime.py"


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _wait(pred, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _runtime(session: Path, *extra: str, campaign=("sleep", "300")) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(RUNTIME), "--session-dir", str(session),
         "--mode", "evolution", "--poll-interval", "0.1", *extra],
        cwd=str(REPO), env={**os.environ, "PH_CAMPAIGN_ARGV": json.dumps(campaign)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def _rows(session: Path, kind: str) -> list[dict]:
    rows = (session / "session-log" / "rows.jsonl").read_text().splitlines()
    return [r for r in map(json.loads, rows) if r["kind"] == kind]


def _where(session: Path, brief: str) -> list[str]:
    return [d for d in ("inbox", "processing", "done", "failed", "cancelled")
            if (session / d / brief).exists()]


def test_sigterm_kills_the_campaign_group_and_files_the_brief(tmp_path):
    session = tmp_path / "runs" / "session-e2e"
    (session / "inbox").mkdir(parents=True)
    (session / "inbox" / "b.json").write_text('{"kind": "campaign", "campaign": "stack"}')
    marker = session / "processing" / "b.json.pgid"
    rt = _runtime(session)
    try:
        _wait(marker.exists, 30, "the campaign group to start")
        pgid = int(marker.read_text())
        assert _group_alive(pgid)
        rt.send_signal(signal.SIGTERM)
        t0 = time.monotonic()
        rt.wait(timeout=5)
        _wait(lambda: not _group_alive(pgid), 5 - (time.monotonic() - t0),
              "the campaign group to die")
        assert _where(session, "b.json") == ["cancelled"]
        assert not marker.exists()
        assert _rows(session, "runtime.task_cancelled")[0]["data"]["stage"] == "runtime_stopped"
        assert (session / "campaigns" / "b" / "CANCELLED").exists()
        assert "stopped_ts" in json.loads((session / "runtime_status.json").read_text())
    finally:
        if rt.poll() is None:
            rt.kill()
        rt.wait()


def test_boot_kills_an_orphaned_group_before_requeueing_once(tmp_path):
    session = tmp_path / "runs" / "session-e2e"
    (session / "processing").mkdir(parents=True)
    (session / "processing" / "b.json").write_text('{"kind": "campaign", "campaign": "stack"}')
    orphan = subprocess.Popen(["sleep", "300"], start_new_session=True)
    (session / "processing" / "b.json.pgid").write_text(str(orphan.pid))
    # the re-run itself is instant: this case is about the orphan, not the run
    rt = _runtime(session, "--drain", campaign=("true",))
    try:
        _, err = rt.communicate(timeout=60)
        assert rt.returncode == 0, err
        orphan.wait(timeout=5)
        assert not _group_alive(orphan.pid)
        killed = _rows(session, "runtime.orphan_killed")
        assert [r["data"] for r in killed] == [{"brief": "b.json", "pgid": orphan.pid}]
        # re-queued exactly once: one brief file, one requeue count, one run
        assert len(_where(session, "b.json")) == 1
        assert json.loads((session / "requeue.json").read_text()) == {"b.json": 1}
        assert len(_rows(session, "runtime.task_claimed") or [0]) <= 1
    finally:
        if orphan.poll() is None:
            orphan.kill()
        orphan.wait()
        if rt.poll() is None:
            rt.kill()
        rt.wait()
