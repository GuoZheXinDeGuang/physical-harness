"""cockpit --restart: the console's restart button. Drives the real bash path
against a throwaway repo copy (tests/test_cockpit_stop.py isolation): a fake
board python answers the policy-status question, a fake pnpm stands in for the
ph-station build, no nvm exists, so nothing real is stopped, built or started."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

COCKPIT = Path(__file__).resolve().parent.parent / "scripts" / "cockpit"


def _repo(tmp_path: Path, serving: int, build_rc: int) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(COCKPIT, repo / "scripts" / "cockpit")
    py = tmp_path / "python"           # stands in for board.store.policy_server("status")
    py.write_text(f"#!/bin/sh\ncat >/dev/null\necho {serving}\n")
    py.chmod(0o755)
    (tmp_path / "bin").mkdir()
    pnpm = tmp_path / "bin" / "pnpm"   # records where it ran, then succeeds or fails
    pnpm.write_text(f"#!/bin/sh\npwd > {tmp_path}/pnpm.ran\nexit {build_rc}\n")
    pnpm.chmod(0o755)
    (tmp_path / "station").mkdir()
    env = {**os.environ, "PH_BOARD_PYTHON": str(py), "PATH": f"{tmp_path}/bin:{os.environ['PATH']}",
           "NVM_DIR": str(tmp_path / "nonvm"), "PH_STATION": str(tmp_path / "station")}
    env.pop("PH_COCKPIT_DETACHED", None)
    return repo, env


def _restart(repo: Path, env: dict, *extra: str) -> str:
    """Run --restart, prove it returns at once, then wait for the detached copy's
    terminal line and return the log."""
    t0 = time.monotonic()
    res = subprocess.run(["bash", str(repo / "scripts" / "cockpit"), "--restart", *extra],
                         capture_output=True, text=True, timeout=30, env=env)
    assert res.returncode == 0, res.stderr
    assert time.monotonic() - t0 < 5, "--restart must not wait for the restart"
    assert "restarting in the background" in res.stderr
    log = repo / "runs" / "restart.log"
    for _ in range(200):
        text = log.read_text() if log.exists() else ""
        if "restart done" in text or "ERROR" in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"detached restart never finished; log:\n{text}")


def test_restart_detaches_and_readds_policy_only_when_it_was_serving(tmp_path):
    repo, env = _repo(tmp_path, serving=1, build_rc=0)
    log = _restart(repo, env, "--port", "1")
    assert "policy server was SERVING" in log and "nothing to stop" in log
    assert "cockpit: starting: cockpit --port 1 --with-policy" in log
    assert not (tmp_path / "pnpm.ran").exists()          # no --build, no build
    # the start path cannot run in a bare copy (no nvm) -- a failed start is an ERROR last line
    assert "ERROR" in log.strip().splitlines()[-1]

    repo, env = _repo(tmp_path / "b", serving=0, build_rc=0)
    log = _restart(repo, env)
    assert "cockpit: starting: cockpit \n" in log and "--with-policy" not in log


def test_restart_build_failure_aborts_before_starting_anything(tmp_path):
    repo, env = _repo(tmp_path, serving=1, build_rc=1)
    log = _restart(repo, env, "--build")
    assert (tmp_path / "pnpm.ran").read_text().strip() == str(tmp_path / "station")
    assert "nothing to stop" in log                       # --stop ran first
    assert "starting:" not in log
    assert "ERROR pnpm build failed" in log.strip().splitlines()[-1]


def test_restart_build_success_then_starts(tmp_path):
    repo, env = _repo(tmp_path, serving=0, build_rc=0)
    log = _restart(repo, env, "--build", "--no-runtime")
    assert (tmp_path / "pnpm.ran").exists()
    assert "cockpit: starting: cockpit --no-runtime\n" in log
