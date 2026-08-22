"""Unit tests for the RSI report builder (board/report.py) and its wiring.

The builder is pure -- it takes a runs/ dir and reuses board.store's parse layer -- so
it is exercised against hand-built fake stores (same on-disk shape CampaignStore
writes). The CLI (--report) and the /api/report endpoint are smoked without the
simulator: an ephemeral stdlib server on port 0.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from board import report as br
from board import store as bs
from scripts import rsi_board


def _digest(payload: dict, seq: int) -> str:
    return hashlib.sha256((json.dumps(payload, sort_keys=True) + str(seq)).encode()).hexdigest()


def _mkstore(root: Path, artifacts: list[tuple[str, dict]]) -> Path:
    (root / "artifacts").mkdir(parents=True)
    lines = []
    for seq, (kind, payload) in enumerate(artifacts):
        digest = _digest(payload, seq)
        (root / "artifacts" / f"{digest}.json").write_text(json.dumps(payload))
        lines.append(json.dumps({"seq": seq, "kind": kind, "sha": digest, "time": seq}))
    (root / "index.jsonl").write_text("\n".join(lines) + "\n")
    return root


def _paired(base, gov, fixed, broken, p, n=200, fires=0, priv=0) -> dict:
    return {"n": n, "base_rate": base, "governed_rate": gov, "fixed": fixed,
            "broken": broken, "p_value": p, "fires": fires, "declared_privilege": priv}


def _fake_runs(runs: Path) -> None:
    """A campaign + a rescore-with-stage-attribution + a round88 diagnostic store."""
    prereg = {"dev": list(range(1000, 1196)), "heldout": list(range(42000, 42200)),
              "task": "stack", "policy": "scripted", "stages": [{"name": "grasp"}],
              "terminal_label": True, "alpha": 0.05, "min_fixed": 3, "percept_noise": 0.012}
    gen1 = {"generation": 1, "promoted": True, "reason": "promoted",
            "rule": {"rule_id": "g1", "trigger": {"feature": "observable.finger_gap", "op": "lt",
                     "threshold": 0.001, "dwell": 1, "arm_after": 58, "reducer": "value"},
                     "recovery": {"name": "regrasp"}},
            "dev_gate": _paired(0.60, 0.66, 12, 0, 0.0005, n=196, fires=58),
            "blind_gate": _paired(0.39, 0.66, 62, 9, 7e-11, n=196)}
    gen2 = {"generation": 2, "promoted": False, "reason": "rejected (fixed=16 broken=12 p=0.57)",
            "rule": {"rule_id": "g2", "trigger": {"feature": "observable.finger_gap", "op": "lt",
                     "threshold": 0.0386, "dwell": 1, "arm_after": 62, "reducer": "value"},
                     "recovery": {"name": "regrasp"}},
            "dev_gate": _paired(0.63, 0.64, 16, 12, 0.57, n=385),
            "blind_gate": _paired(0.52, 0.64, 66, 20, 6e-7, n=385)}
    result = {"generations": 2, "promoted": 1, "rules": ["g1"],
              "heldout": _paired(0.585, 0.65, 13, 0, 0.00024, fires=54),
              "heldout_vs_blind": _paired(0.295, 0.65, 79, 8, 8e-16, fires=54),
              "ablation": [[0.0, _paired(0.585, 0.765, 36, 0, 1e-11, priv=1)],
                           [0.02, _paired(0.585, 0.65, 13, 0, 2e-4)]]}
    _mkstore(runs / "stack-g1", [("preregistration", prereg), ("generation", gen1),
                                 ("generation", gen2), ("campaign_result", result)])

    rescore = {"block": 42400, "n": 200, "judgement": True,
               "paired": _paired(0.485, 0.595, 22, 0, 4.7e-7, fires=65),
               "vs_blind": _paired(0.315, 0.595, 67, 11, 6e-11, fires=65),
               "stage_attribution": {"n": 200, "successes": 119, "stage_order": ["grasp", "place"],
                   "per_stage": {"grasp": {"reached": 200, "success": 164},
                                 "place": {"reached": 200, "success": 113}},
                   "first_failure": {"grasp": 34, "place": 47, "(terminal)": 0}}}
    _mkstore(runs / "stack-g1-rescore-42400", [("heldout_rescore", rescore)])

    fix = {"grade": "diagnostic", "dev_rate": 0.5, "top_score": 1.0,
           "anchors": {"naive_arm95_fixed": 63, "old_search_arm43_fixed": 54},
           "heldout": {"fixed_pick": _paired(0.4, 0.715, 63, 0, 2e-19, fires=119),
                       "search_arm43_anchor": _paired(0.4, 0.67, 54, 0, 1e-16, fires=119)}}
    _mkstore(runs / "round88-fix", [("round88_fix", fix)])


def test_report_has_sections_and_numbers(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _fake_runs(runs)
    now = max(s["mtime"] for s in bs.list_stores(runs)) + 5
    html = br.build_report(
        runs, head_sha="deadbeefcafe", now=now, live_threshold_s=1e9,
        status_text="**区块预算(round 78):** stack held-out 已烧 42000-42199。\n",
        progress_text="## Round 88 - 2026-08-22 - objective repair\nbody eight-eight\n")

    # section markers, in document order
    for marker in ("Executive summary", "Campaigns", "Diagnostics", "Live campaigns",
                   "Seed ledger", "Rounds", "Traceability"):
        assert marker in html, marker
    assert html.index("Executive summary") < html.index("Campaigns") < html.index("Diagnostics")

    # executive headline numbers (own block +6.5pp fixed 13, rescore +11.0pp fixed 22 -> 35 fixed n=400)
    assert "Held-out headline" in html
    assert "+6.5pp" in html and "+11.0pp" in html
    assert "35 fixed, 0 broken" in html and "n=400" in html
    # ceiling: place primitive attribution
    assert "place" in html and "113/200" in html and "164/200" in html
    # objective-repair story
    assert "54" in html and "63" in html and "self-selects the peak arm" in html
    # per-campaign + diagnostics + ledger + rounds + footer
    assert "stack-g1" in html
    assert "Stage attribution" in html
    assert "Objective-repair validation" in html
    assert "42000" in html  # ledger range
    assert "Round 88" in html and "objective repair" in html
    assert "deadbeefcafe" in html  # HEAD sha in footer
    assert html.lstrip().startswith("<!doctype html")


def test_live_flag_respects_threshold(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _fake_runs(runs)
    # far in the future -> nothing is fresh
    html = br.build_report(runs, now=2e9, live_threshold_s=60)
    assert "nothing in progress" in html


def test_cli_report_writes_file(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _fake_runs(runs)
    out = tmp_path / "report.html"
    rc = rsi_board.main(["--runs", str(runs), "--report", str(out), "--no-browser"])
    assert rc == 0
    text = out.read_text()
    assert text.lstrip().startswith("<!doctype html")
    assert "RSI Report" in text and "stack-g1" in text


def test_report_endpoint_returns_html(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _fake_runs(runs)
    rsi_board.Handler.runs_dir = runs.resolve()
    rsi_board.Handler.status_path = tmp_path / "STATUS.md"      # absent -> empty ledger, still 200
    rsi_board.Handler.progress_path = tmp_path / "progress.md"
    rsi_board.Handler.live_threshold = 3600.0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), rsi_board.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/report", timeout=10) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            disp = resp.headers.get("Content-Disposition", "")
            assert "attachment" in disp and "rsi-report-" in disp and disp.endswith('.html"')
            body = resp.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert body.lstrip().startswith("<!doctype html")
    assert "Executive summary" in body
