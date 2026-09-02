"""End to end: `bash scripts/cockpit --status` and `--with-model` against a
throwaway repo layout (the real bash script, copied like test_cockpit_stop does;
the real board.store via PYTHONPATH=$REPO).

A session whose runtime is dead and whose inbox is empty is DORMANT: one compact
line, exit 0, never a problem -- runs/session-robocasa-evolution sat as DOWN
with a 10-day-old heartbeat until operators learned to ignore health. The same
session with a queued brief is STALLED: exit 1 and the problem names it.

--with-model routes through board.store.model_server('start') and waits for
healthy. The tmp repo carries a FAKE board/store.py (PYTHONPATH=$REPO makes
cockpit import it) that records the calls -- the real one would load a 27B
model on this box.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_DEAD_PID = int(Path("/proc/sys/kernel/pid_max").read_text()) + 1


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "cockpit", repo / "scripts" / "cockpit")
    # the real board package (and what it imports), found through PYTHONPATH=$REPO
    os.symlink(REPO / "board", repo / "board")
    os.symlink(REPO / "harness", repo / "harness")
    os.symlink(REPO / "scripts" / "brief_drop.py", repo / "scripts" / "brief_drop.py")
    return repo


def _session(repo: Path, name: str) -> Path:
    d = repo / "runs" / name
    for sub in ("inbox", "processing", "done", "failed"):
        (d / sub).mkdir(parents=True)
    (d / "runtime_status.json").write_text(json.dumps(
        {"pid": _DEAD_PID, "mode": "evolution", "boot_ts": 1.0, "heartbeat_ts": 1.0}))
    return d


def _cockpit(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(repo / "scripts" / "cockpit"), *args],
                          capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PH_BOARD_PYTHON": str(REPO / ".venv/bin/python"),
                               "PH_CONSOLE_PORT": "1", **(env or {})})


def _status(repo: Path, port: int):
    """--port=<listening socket> so the console row is not the problem under test."""
    return _cockpit(repo, "--status", f"--port={port}")


def _listening():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def test_status_lists_a_dormant_session_once_and_stays_green(tmp_path):
    repo = _repo(tmp_path)
    _session(repo, "session-robocasa-evolution")
    with _listening() as console:
        res = _status(repo, console.getsockname()[1])
    out = res.stdout
    assert res.returncode == 0, out + res.stderr
    assert "cockpit --status: HEALTHY" in out
    assert "  dormant session-robocasa-evolution" in out
    assert "session-robocasa-evolution" not in "".join(
        line for line in out.splitlines() if line.startswith("  !"))
    assert "DOWN" not in out.replace("console", "")  # the old red row is gone


def test_status_flags_the_same_session_once_a_brief_is_queued(tmp_path):
    repo = _repo(tmp_path)
    (_session(repo, "session-robocasa-evolution") / "inbox" / "b.json").write_text("{}")
    with _listening() as console:
        res = _status(repo, console.getsockname()[1])
    out = res.stdout
    assert res.returncode == 1, out + res.stderr
    problems = [line for line in out.splitlines() if line.startswith("  !")]
    assert len(problems) == 1 and "session-robocasa-evolution: 1 queued" in problems[0]
    assert "  session session-robocasa-evolution STALLED" in out
    assert "dormant" not in out


def test_status_names_the_model_start_command_and_flags_it_only_when_wanted(tmp_path):
    repo = _repo(tmp_path)
    with _listening() as console:
        res = _status(repo, console.getsockname()[1])
        out = res.stdout
        model_row = out[out.index("  model "):]
        if "STOPPED" in model_row:      # true on a box without the server up
            assert res.returncode == 0
            assert "cockpit --with-model" in model_row
            (repo / ".env").write_text("PH_WITH_MODEL=1\n")
            res = _status(repo, console.getsockname()[1])
            assert res.returncode == 1
            assert any(line.startswith("  ! model:") and "cockpit --with-model" in line
                       for line in res.stdout.splitlines()), res.stdout


_FAKE_STORE = '''
import json, sys
from pathlib import Path
CALLS = Path(__file__).with_name("calls.json")
def model_server(action="status", runs_dir="."):
    calls = json.loads(CALLS.read_text()) if CALLS.exists() else []
    calls.append([action, str(runs_dir)])
    CALLS.write_text(json.dumps(calls))
    # start answers "loading", the status poll that follows answers healthy
    return {"running": True, "pid": 4242, "port": 30001, "healthy": action == "status",
            "model": "fake-27b", "vram_mib": None}
'''


def test_with_model_starts_through_model_server_and_waits_for_healthy(tmp_path):
    repo = _repo(tmp_path)
    os.unlink(repo / "board")                      # fake face, same import path
    (repo / "board").mkdir()
    (repo / "board" / "__init__.py").write_text("")
    (repo / "board" / "store.py").write_text(_FAKE_STORE)
    # --no-runtime + no nvm: cockpit exits 1 at the node stage, AFTER the model step
    res = _cockpit(repo, "--with-model", "--no-runtime",
                   env={"NVM_DIR": str(tmp_path / "no-nvm")})
    assert "model server HEALTHY (fake-27b)" in res.stderr, res.stderr
    assert "nvm not found" in res.stderr           # got past the model step
    calls = json.loads((repo / "board" / "calls.json").read_text())
    assert calls[0] == ["start", str(repo / "runs")]
    assert calls[-1][0] == "status" and len(calls) == 2
    # off by default: no call at all
    (repo / "board" / "calls.json").unlink()
    _cockpit(repo, "--no-runtime", env={"NVM_DIR": str(tmp_path / "no-nvm")})
    assert not (repo / "board" / "calls.json").exists()


_FAKE_POLICY_STORE = _FAKE_STORE + '''
def policy_server(action="status", runs_dir=".", checkpoint_dir=None):
    calls = json.loads(CALLS.read_text()) if CALLS.exists() else []
    calls.append(["policy", action, str(runs_dir), checkpoint_dir])
    CALLS.write_text(json.dumps(calls))
    return {"running": True, "pid": 4343, "port": 8000, "serving": action == "status",
            "checkpoint_sha": "ab" * 32}
'''


def test_with_policy_starts_through_policy_server_and_waits_for_the_port(tmp_path):
    repo = _repo(tmp_path)
    os.unlink(repo / "board")
    (repo / "board").mkdir()
    (repo / "board" / "__init__.py").write_text("")
    (repo / "board" / "store.py").write_text(_FAKE_POLICY_STORE)
    (repo / ".env").write_text("PH_WITH_POLICY=1\nPH_POLICY_CHECKPOINT=/ckpt/199\n")
    res = _cockpit(repo, "--no-runtime", env={"NVM_DIR": str(tmp_path / "no-nvm")})
    assert "policy server SERVING (checkpoint_sha=" + "ab" * 32 + ")" in res.stderr, res.stderr
    assert "nvm not found" in res.stderr
    calls = json.loads((repo / "board" / "calls.json").read_text())
    assert calls == [["policy", "start", str(repo / "runs"), "/ckpt/199"],
                     ["policy", "status", str(repo / "runs"), None]]
    # the flag alone, no .env: same path, default checkpoint
    (repo / "board" / "calls.json").unlink()
    (repo / ".env").unlink()
    _cockpit(repo, "--with-policy", "--no-runtime", env={"NVM_DIR": str(tmp_path / "no-nvm")})
    assert json.loads((repo / "board" / "calls.json").read_text())[0] == \
        ["policy", "start", str(repo / "runs"), None]
