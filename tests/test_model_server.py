"""board.store.model_server: the console's local-model switch.

This face EXECUTES, so the contract under test is mostly about what it refuses:
the only thing a caller supplies is an action word, the launcher is a module
constant, and nothing is ever killed by pattern -- only a pid that still proves
its identity through /proc/<pid>/exe at kill time. A pattern kill in this repo's
history matched the killer's own shell.

/proc, the health probe and nvidia-smi are all faked here, so the assertions are
the guards and the state machine, not this box's current processes.
"""

from __future__ import annotations

import json
import re
import signal
import subprocess
import urllib.error
from pathlib import Path

from board import mcp_server as ms
from board import store as bs
from board import storecli as sc

_SERVER_EXE = "/home/op/tools/llama.cpp/llama-b10566/llama-server"
_SERVER_CMD = "llama-server -m qwen38.gguf --host 127.0.0.1 --port 30001"
#: The launcher's own here-doc, written by an editor: its argv mentions both the
#: binary and the port, so an argv-only match would have adopted -- and later
#: KILLED -- a text editor. /proc/<pid>/exe is what makes the check unforgeable.
_IMPOSTOR = ("/bin/bash", "bash -c cat > launch.sh <<EOF ... llama-server --port 30001 ... EOF")

_GPU_CSV = "GPU-aaa, 0, NVIDIA GeForce RTX 4090 D, 21838, 24564\n"
_APPS_CSV = "GPU-aaa, 4242, llama-server, 19980\nGPU-aaa, 428, python, 428\n"


def _fake_proc(monkeypatch, procs: dict[int, tuple[str, str]]):
    """Stand in for /proc: ``{pid: (exe, cmdline)}``. Returned so a test can
    mutate it -- a spawn that reaches its exec is exactly a new entry."""
    real_readlink, real_bytes, real_iterdir = bs.os.readlink, Path.read_bytes, Path.iterdir

    def readlink(path, *a, **kw):
        m = re.fullmatch(r"/proc/(\d+)/exe", str(path))
        if m is None:
            return real_readlink(path, *a, **kw)
        if int(m.group(1)) not in procs:
            raise FileNotFoundError(path)
        return procs[int(m.group(1))][0]

    def read_bytes(self):
        m = re.fullmatch(r"/proc/(\d+)/cmdline", str(self))
        if m is None:
            return real_bytes(self)
        if int(m.group(1)) not in procs:
            raise FileNotFoundError(self)
        return procs[int(m.group(1))][1].replace(" ", "\0").encode()

    def iterdir(self):
        if str(self) != "/proc":
            return real_iterdir(self)
        return iter([Path(f"/proc/{pid}") for pid in sorted(procs)])

    monkeypatch.setattr(bs.os, "readlink", readlink)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    return procs


def _fake_health(monkeypatch, serving: bool):
    """Answer /v1/models like llama.cpp does once loaded, or refuse the
    connection the way it does for the 1-2 minutes it spends loading."""
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"data": [{"id": "qwen38.gguf"}]}).encode()

    def urlopen(url, timeout=None):
        if not serving:
            raise urllib.error.URLError("Connection refused")
        return _Resp()
    monkeypatch.setattr(bs.urllib.request, "urlopen", urlopen)


def _fake_smi(monkeypatch):
    class _P:
        returncode = 0
        def __init__(self, stdout): self.stdout = stdout
    monkeypatch.setattr(bs.subprocess, "run",
                        lambda cmd, **kw: _P(_GPU_CSV if "--query-gpu" in cmd[1] else _APPS_CSV))


def _no_spawn_no_kill(monkeypatch):
    """Trip wires: any status/reject path that spawns or kills has failed."""
    def boom(*a, **kw):
        raise AssertionError("a read-only path must not spawn or kill")
    monkeypatch.setattr(bs.subprocess, "Popen", boom)
    monkeypatch.setattr(bs.os, "kill", boom)


def test_status_separates_stopped_loading_and_serving(tmp_path, monkeypatch):
    """The three states the operator's badge shows. `running and not healthy`
    is the loading window -- the process holds the port for minutes before it
    answers, and without that middle state the badge would flap stopped/on."""
    _fake_smi(monkeypatch)
    _no_spawn_no_kill(monkeypatch)

    _fake_proc(monkeypatch, {})
    _fake_health(monkeypatch, serving=False)
    stopped = bs.model_server("status", tmp_path)
    assert stopped == {"running": False, "pid": None, "port": 30001,
                       "healthy": False, "model": None, "vram_mib": None}

    _fake_proc(monkeypatch, {4242: (_SERVER_EXE, _SERVER_CMD)})
    loading = bs.model_server("status", tmp_path)
    assert (loading["running"], loading["healthy"], loading["pid"]) == (True, False, 4242)

    _fake_health(monkeypatch, serving=True)
    serving = bs.model_server("status", tmp_path)
    assert (serving["running"], serving["healthy"]) == (True, True)
    assert serving["model"] == "qwen38.gguf"
    # VRAM comes from the same per-pid rows host_vitals reports, joined on OUR
    # pid -- the 428 MiB neighbour on the same card is not ours.
    assert serving["vram_mib"] == 19980


def test_a_shell_that_merely_mentions_the_binary_is_not_the_server(tmp_path, monkeypatch):
    """argv is attacker- and accident-controlled; /proc/<pid>/exe is not."""
    _fake_smi(monkeypatch)
    _no_spawn_no_kill(monkeypatch)
    _fake_health(monkeypatch, serving=False)
    _fake_proc(monkeypatch, {77: _IMPOSTOR})

    assert bs.model_server("status", tmp_path)["running"] is False


def test_only_the_three_actions_are_accepted(tmp_path, monkeypatch):
    """The action word is the whole caller-supplied surface. Anything outside
    the whitelist answers with an error beside a real status and touches
    nothing -- the trip wires above fail the test if it spawns or kills."""
    _fake_smi(monkeypatch)
    _no_spawn_no_kill(monkeypatch)
    _fake_health(monkeypatch, serving=True)
    _fake_proc(monkeypatch, {4242: (_SERVER_EXE, _SERVER_CMD)})

    for action in ("restart", "STATUS", "", "status; rm -rf /", "/home/op/evil.sh"):
        out = bs.model_server(action, tmp_path)
        assert out["error"] == f"unknown action: {action}"
        assert out["running"] is True          # still a truthful status, never a raise


def test_start_adopts_a_live_server_instead_of_spawning_a_second(tmp_path, monkeypatch):
    _fake_smi(monkeypatch)
    _no_spawn_no_kill(monkeypatch)               # Popen here would be the second server
    _fake_health(monkeypatch, serving=True)
    _fake_proc(monkeypatch, {4242: (_SERVER_EXE, _SERVER_CMD)})

    assert bs.model_server("start", tmp_path)["pid"] == 4242
    assert not (tmp_path / "model-server.pid").exists()   # we did not start it, we do not own it


def test_start_spawns_the_constant_launcher_detached(tmp_path, monkeypatch):
    """The command line is a module constant, and the child gets its own
    session: a runtime spawned inside the launching terminal's process group
    died to a group-wide teardown mid-campaign once already."""
    _fake_smi(monkeypatch)
    _fake_health(monkeypatch, serving=False)     # freshly spawned: loading, not serving
    procs = _fake_proc(monkeypatch, {})
    monkeypatch.setattr(bs, "_MODEL_SCRIPT", tmp_path / "launch_llamacpp.sh")
    (tmp_path / "launch_llamacpp.sh").write_text("#!/bin/sh\nexec llama-server\n")
    seen = {}

    class _Child:
        pid = 4242

    def popen(argv, **kw):
        seen.update(argv=argv, kw=kw)
        procs[4242] = (_SERVER_EXE, _SERVER_CMD)   # the launcher reaches its exec
        return _Child()
    monkeypatch.setattr(bs.subprocess, "Popen", popen)

    out = bs.model_server("start", tmp_path)

    assert seen["argv"] == [str(tmp_path / "launch_llamacpp.sh")]   # no caller string reaches argv
    assert seen["kw"]["start_new_session"] is True
    assert seen["kw"]["stdin"] is subprocess.DEVNULL
    assert (out["running"], out["healthy"], out["pid"]) == (True, False, 4242)
    assert (tmp_path / "model-server.pid").read_text() == "4242"


def test_stop_kills_the_recorded_pid_and_clears_the_record(tmp_path, monkeypatch):
    _fake_smi(monkeypatch)
    _fake_health(monkeypatch, serving=True)
    _fake_proc(monkeypatch, {4242: (_SERVER_EXE, _SERVER_CMD)})
    (tmp_path / "model-server.pid").write_text("4242")
    killed = []
    monkeypatch.setattr(bs.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    bs.model_server("stop", tmp_path)

    assert killed == [(4242, signal.SIGTERM)]
    assert not (tmp_path / "model-server.pid").exists()


def test_stop_refuses_a_recycled_pid(tmp_path, monkeypatch):
    """The pidfile's number outlives the process it named. Re-checking identity
    at kill time is the difference between stopping the model server and
    SIGTERMing whatever inherited 4242 -- here, an operator's shell."""
    _fake_smi(monkeypatch)
    _fake_health(monkeypatch, serving=False)
    _fake_proc(monkeypatch, {4242: ("/bin/bash", "bash -l")})     # 4242 was recycled
    (tmp_path / "model-server.pid").write_text("4242")
    monkeypatch.setattr(bs.os, "kill", lambda *a: (_ for _ in ()).throw(
        AssertionError("killed a pid that is no longer the model server")))

    out = bs.model_server("stop", tmp_path)

    assert out["error"] == "not running" and out["running"] is False
    # A garbage or absent pidfile takes the same path, never a scan-and-guess.
    (tmp_path / "model-server.pid").write_text("not a pid")
    assert bs.model_server("stop", tmp_path)["error"] == "not running"


def test_three_faces_are_byte_identical(tmp_path, monkeypatch):
    """store / storecli / mcp_server return the SAME dict, and the CLI's omitted
    argument reads rather than writes (an action slot that defaulted to a write
    would be the worst possible default)."""
    runs = (tmp_path / "runs")
    runs.mkdir()
    runs = runs.resolve()
    _fake_smi(monkeypatch)
    _no_spawn_no_kill(monkeypatch)
    _fake_health(monkeypatch, serving=True)
    _fake_proc(monkeypatch, {4242: (_SERVER_EXE, _SERVER_CMD)})
    ms.configure(runs, tmp_path / "STATUS.md", tmp_path / "progress.md")

    wire = json.dumps(bs.model_server("status", runs), sort_keys=True)
    cli = sc.dispatch("model_server", "status", runs, tmp_path / "STATUS.md", tmp_path / "progress.md")
    bare = sc.dispatch("model_server", None, runs, tmp_path / "STATUS.md", tmp_path / "progress.md")

    assert json.dumps(cli, sort_keys=True) == wire
    assert json.dumps(bare, sort_keys=True) == wire
    assert json.dumps(ms.model_server("status"), sort_keys=True) == wire
    assert json.loads(wire)["pid"] == 4242            # not identity-of-empty
