"""Live campaign progress: the heartbeat writer (scripts/campaign_progress.py),
the board scan (board.store.campaign_progress), and its three call-faces.

The heartbeat is LIVE STATE, not evidence: the writer must never raise into a
battery, the scan must tolerate mid-write garbage, and the statistics (success
count, first-death histogram) must arrive pre-folded from python -- the panel
only displays them. Face equivalence follows the round-95 discipline pinned by
test_storecli/test_mcp_server: byte-identical dicts on every face.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from board import mcp_server as ms
from board import store as bs
from board import storecli
from scripts.campaign_progress import tracker, write_progress


def _read(runs: Path, name: str) -> dict:
    return json.loads((runs / name / "progress.json").read_text())


# --- writer ------------------------------------------------------------------


def test_write_progress_atomic_payload(tmp_path):
    write_progress(tmp_path / "cal", 3, 150, label="kitchen_thaw 52150:52300",
                   extra={"succeeded": 1, "first_death": {"grasp": 2}})
    row = json.loads((tmp_path / "cal" / "progress.json").read_text())
    assert row["done"] == 3 and row["total"] == 150
    assert row["label"] == "kitchen_thaw 52150:52300"
    assert row["succeeded"] == 1 and row["first_death"] == {"grasp": 2}
    assert row["started_ts"] <= row["updated_ts"] <= time.time()
    # atomic: no temp residue beside the replaced file
    assert not (tmp_path / "cal" / "progress.json.tmp").exists()


def test_write_progress_never_raises(tmp_path):
    blocker = tmp_path / "flat"
    blocker.write_text("not a directory")
    write_progress(blocker / "cal", 1, 10)  # mkdir fails; swallowed


def test_tracker_folds_rolling_stats(tmp_path):
    tick = tracker(tmp_path / "cal", 3, label="battery")
    first = json.loads((tmp_path / "cal" / "progress.json").read_text())
    assert first["done"] == 0 and first["total"] == 3  # card appears at start
    tick({"thawed": True, "first_death": "none"})
    tick({"thawed": False, "first_death": "grasp"})
    tick({"thawed": False, "first_death": "grasp"})
    row = json.loads((tmp_path / "cal" / "progress.json").read_text())
    assert row["done"] == 3 and row["succeeded"] == 1
    assert row["first_death"] == {"grasp": 2}
    assert row["started_ts"] == first["started_ts"]  # start survives rewrites


# --- board scan --------------------------------------------------------------


def _fixture(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    write_progress(runs / "live-cal", 40, 150, label="kitchen_thaw",
                   extra={"succeeded": 3, "first_death": {"grasp": 30}})
    write_progress(runs / "done-cal", 150, 150, label="old")
    stale = {"done": 10, "total": 100, "label": "stale",
             "started_ts": time.time() - 9000, "updated_ts": time.time() - 8000}
    (runs / "stale-cal").mkdir()
    (runs / "stale-cal" / "progress.json").write_text(json.dumps(stale))
    (runs / "midwrite").mkdir()
    (runs / "midwrite" / "progress.json").write_text('{"done": 5, "tot')
    return runs


def test_campaign_progress_running_vs_stale_vs_done(tmp_path):
    rows = bs.campaign_progress(_fixture(tmp_path))
    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"live-cal", "done-cal", "stale-cal"}  # midwrite skipped
    assert by_name["live-cal"]["running"] is True
    assert by_name["live-cal"]["succeeded"] == 3
    assert by_name["done-cal"]["running"] is False   # at total
    assert by_name["stale-cal"]["running"] is False  # heartbeat too old
    assert rows[0]["name"] in ("live-cal", "done-cal")  # newest updated first
    assert rows[-1]["name"] == "stale-cal"


def test_campaign_progress_empty_runs(tmp_path):
    (tmp_path / "runs").mkdir()
    assert bs.campaign_progress(tmp_path / "runs") == []


# --- three faces -------------------------------------------------------------


def test_campaign_progress_faces_are_byte_identical(tmp_path, capsys):
    runs = _fixture(tmp_path)
    want = json.dumps(bs.campaign_progress(runs), sort_keys=True)
    code = storecli.main(["campaign_progress", "--runs", str(runs)])
    assert code == 0
    got_cli = json.loads(capsys.readouterr().out)
    assert json.dumps(got_cli, sort_keys=True) == want
    ms.configure(runs)
    assert json.dumps(ms.campaign_progress(), sort_keys=True) == want
