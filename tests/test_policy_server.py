"""board.store.policy_server / health()["policy"]: the pi0.5 server on :8000,
managed the way model_server manages llama.cpp. Nothing here starts the real
server (tests/test_mission_sim_e2e-style vla tests do); the spawn path runs a
fake interpreter whose argv still carries the identity the scan looks for."""

from __future__ import annotations

import os
import signal
import time

import board.store as bs

_ROW = {"running", "pid", "port", "serving", "checkpoint_sha"}


def _stopped(monkeypatch):
    monkeypatch.setattr(bs, "_find_policy_server", lambda: None)
    monkeypatch.setattr(bs, "_policy_probe", lambda: (False, None))
    monkeypatch.setattr(bs, "_find_model_server", lambda: None)
    monkeypatch.setattr(bs, "_model_health", lambda: (False, None))


def test_health_policy_row_shape_and_flag_only_when_wanted(tmp_path, monkeypatch):
    _stopped(monkeypatch)
    monkeypatch.delenv("PH_WITH_POLICY", raising=False)
    h = bs.health(tmp_path / "runs", 1)
    assert set(h["policy"]) == _ROW and h["policy"]["port"] == 8000
    assert not [p for p in h["problems"] if p.startswith("policy:")]
    monkeypatch.setenv("PH_WITH_POLICY", "1")
    flagged = [p for p in bs.health(tmp_path / "runs", 1)["problems"] if p.startswith("policy:")]
    assert len(flagged) == 1 and "cockpit --with-policy" in flagged[0] and ":8000" in flagged[0]


def test_status_and_unknown_action_never_raise(tmp_path, monkeypatch):
    _stopped(monkeypatch)
    assert set(bs.policy_server("status", tmp_path)) == _ROW
    assert bs.policy_server("bogus", tmp_path)["error"] == "unknown action: bogus"
    assert bs.policy_server("stop", tmp_path)["error"] == "not running"


def test_start_spawns_the_serve_script_detached_and_stop_reaps_the_pidfile(tmp_path, monkeypatch):
    _stopped(monkeypatch)
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setattr(bs, "_POLICY_PYTHON", fake)
    monkeypatch.setattr(bs, "_OPENPI", tmp_path)
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    out = bs.policy_server("start", tmp_path, ckpt)
    pid = int((tmp_path / "policy-server.pid").read_text())
    try:
        assert not out.get("error"), out
        assert bs._policy_identity(pid)            # argv names serve_vla_openpi.py + --port 8000
        assert os.getpgid(pid) == pid              # setsid: outlives the caller's group
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        assert b"--checkpoint-dir" in cmd and str(ckpt).encode() in cmd
        assert bs.policy_server("stop", tmp_path).get("error") is None
        for _ in range(50):
            if not bs._policy_identity(pid):
                break
            time.sleep(0.05)
        assert not bs._policy_identity(pid)
        assert not (tmp_path / "policy-server.pid").exists()
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
