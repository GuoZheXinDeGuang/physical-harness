"""Phase-5 routing: cockpit --stop reaps per-session runtimes by EXACT pid.

The multi-runtime change gives each session its own ``runs/<session>/cockpit.pids``;
--stop must reap each recorded runtime by its exact pid, guarded by a cmdline
re-check (never pattern-kill, never touch a pid whose cmdline no longer matches),
and leave an ADOPTED runtime alone. This drives the real bash --stop path against
a throwaway repo layout with real sleep processes standing in for runtimes -- so
the reaping discipline is exercised, not mocked. --stop returns before any
nvm/node work, so no build is needed.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

COCKPIT = Path(__file__).resolve().parent.parent / "scripts" / "cockpit"


def _reaped(proc: subprocess.Popen, timeout: float = 3.0) -> bool:
    """True iff proc died within timeout. Uses Popen.wait so the child is reaped
    (a SIGTERM'd child otherwise lingers as a zombie that os.kill(pid,0) still
    reports as alive)."""
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _cleanup(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.kill(proc.pid, signal.SIGKILL)
    proc.wait()


def _spawn(argv0: str) -> subprocess.Popen:
    """A live process whose /proc/pid/cmdline starts with argv0 (via exec -a),
    so kill_recorded's cmdline guard sees the name it expects. bash is REPLACED
    by sleep, so the returned pid IS the renamed sleep."""
    return subprocess.Popen(["bash", "-c", f'exec -a "{argv0}" sleep 30'])


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(COCKPIT, repo / "scripts" / "cockpit")
    return repo


def _pidfile(repo: Path, session: str, **kv) -> None:
    d = repo / "runs" / session
    d.mkdir(parents=True, exist_ok=True)
    (d / "cockpit.pids").write_text("".join(f"{k}={v}\n" for k, v in kv.items()))


def _stop(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(repo / "scripts" / "cockpit"), "--stop"],
                          capture_output=True, text=True, timeout=30)


def test_stop_reaps_spawned_extra_session_by_exact_pid(tmp_path):
    repo = _repo(tmp_path)
    proc = _spawn("harness_runtime.py --session-dir runs/session-robocasa")
    try:
        _pidfile(repo, "session-robocasa", runtime_pid=proc.pid, runtime_adopted=0)
        res = _stop(repo)
        assert res.returncode == 0, res.stderr
        assert _reaped(proc), "spawned extra-session runtime was not reaped"
        assert not (repo / "runs" / "session-robocasa" / "cockpit.pids").exists()
    finally:
        _cleanup(proc)


def test_stop_leaves_adopted_runtime_running(tmp_path):
    repo = _repo(tmp_path)
    proc = _spawn("harness_runtime.py --session-dir runs/session-robocasa")
    try:
        _pidfile(repo, "session-robocasa", runtime_pid=proc.pid, runtime_adopted=1)
        res = _stop(repo)
        assert res.returncode == 0
        assert proc.poll() is None, "an ADOPTED runtime must be left running"
        assert "ADOPTED" in res.stderr
    finally:
        _cleanup(proc)


def test_stop_skips_pid_whose_cmdline_no_longer_matches(tmp_path):
    """PID reuse guard: a recorded pid now running something ELSE is NOT killed."""
    repo = _repo(tmp_path)
    proc = _spawn("some-unrelated-command")   # cmdline lacks harness_runtime.py
    try:
        _pidfile(repo, "session-robocasa", runtime_pid=proc.pid, runtime_adopted=0)
        res = _stop(repo)
        assert res.returncode == 0
        assert proc.poll() is None, "a pid whose cmdline changed must NOT be killed"
        assert "NOT killing" in res.stderr
    finally:
        _cleanup(proc)


def test_stop_reaps_main_and_extra_together(tmp_path):
    repo = _repo(tmp_path)
    main = _spawn("harness_runtime.py --session-dir runs/session-main")
    robo = _spawn("harness_runtime.py --session-dir runs/session-robocasa")
    try:
        # main pidfile also carries a (dead) web pid -- a stale web pid must not
        # abort the runtime reaping.
        _pidfile(repo, "session-main", web_pid=2, port=3080,
                 runtime_pid=main.pid, runtime_adopted=0)
        _pidfile(repo, "session-robocasa", runtime_pid=robo.pid, runtime_adopted=0)
        res = _stop(repo)
        assert res.returncode == 0, res.stderr
        assert _reaped(main) and _reaped(robo)
    finally:
        _cleanup(main)
        _cleanup(robo)


def test_stop_with_no_pidfiles_is_clean(tmp_path):
    repo = _repo(tmp_path)
    (repo / "runs").mkdir()
    res = _stop(repo)
    assert res.returncode == 0
    assert "nothing to stop" in res.stderr
