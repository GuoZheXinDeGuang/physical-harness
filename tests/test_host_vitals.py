"""board.store.host_vitals: the operator's live view of the machine's headroom.

This is a LIVE-state read (the read_runtime_status family), so the contract under
test is as much about what it must NOT do -- raise, or take the poll down when a
probe fails -- as about the numbers. A host with no NVIDIA driver is a normal
deployment, and a VRAM ceiling is exactly what the panel exists to show before it
kills a resident runtime.

nvidia-smi and /proc/meminfo are the two host reads, both monkeypatched here so
the assertions are the parse and the join, not this box's current GPU.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from board import mcp_server as ms
from board import store as bs
from board import storecli as sc

_GPU_CSV = (
    "GPU-aaa, 0, NVIDIA GeForce RTX 4090 D, 21838, 24564\n"
    "GPU-bbb, 1, NVIDIA GeForce RTX 4090 D, 12, 24564\n"
)
_APPS_CSV = (
    "GPU-aaa, 428, python, 428\n"
    "GPU-aaa, 1125316, sglang::scheduler, 20728\n"
    "GPU-ccc, 99, stranger, 500\n"      # a card that vanished between the two reads
)
_MEMINFO = "MemTotal:       65614352 kB\nMemFree: 1 kB\nMemAvailable:   45900000 kB\n"


class _Proc:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.returncode = stdout, returncode


def _fake_smi(monkeypatch, gpu=_GPU_CSV, apps=_APPS_CSV, boom=None):
    """Answer both nvidia-smi queries from canned CSV, or raise ``boom``."""
    def run(cmd, **kwargs):
        if boom is not None:
            raise boom
        return _Proc(gpu if "--query-gpu" in cmd[1] else apps)
    monkeypatch.setattr(bs.subprocess, "run", run)


def _fake_meminfo(monkeypatch, text=_MEMINFO):
    real = Path.read_text

    def read_text(self, *a, **kw):
        return text if str(self) == "/proc/meminfo" else real(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", read_text)


def test_reports_gpus_ram_and_disk(tmp_path, monkeypatch):
    _fake_smi(monkeypatch)
    _fake_meminfo(monkeypatch)

    v = bs.host_vitals(tmp_path)

    assert [g["index"] for g in v["gpu"]] == [0, 1]
    assert v["gpu"][0]["name"] == "NVIDIA GeForce RTX 4090 D"
    assert (v["gpu"][0]["used_mib"], v["gpu"][0]["total_mib"]) == (21838, 24564)
    # procs join on uuid (compute-apps has no index column) and land biggest-first,
    # so the panel names the top consumer without sorting anything itself.
    assert v["gpu"][0]["procs"] == [{"pid": 1125316, "name": "sglang::scheduler", "used_mib": 20728},
                                    {"pid": 428, "name": "python", "used_mib": 428}]
    assert v["gpu"][1]["procs"] == []          # the GPU-ccc row belongs to no listed card
    # used = MemTotal - MemAvailable: reclaimable cache is free, not used.
    assert v["ram"] == {"used_gb": 18.8, "total_gb": 62.6}
    assert v["disk"]["path"] == str(tmp_path) and v["disk"]["total_gb"] > 0
    assert v["disk"]["free_gb"] <= v["disk"]["total_gb"]
    assert isinstance(v["ts"], float)


def test_no_nvidia_smi_degrades_to_empty_gpu_list(tmp_path, monkeypatch):
    _fake_meminfo(monkeypatch)
    for boom in (FileNotFoundError("nvidia-smi"), subprocess.TimeoutExpired("nvidia-smi", 2.0)):
        _fake_smi(monkeypatch, boom=boom)
        v = bs.host_vitals(tmp_path)
        assert v["gpu"] == []                  # never an exception, never a fake number
        assert v["ram"]["total_gb"] == 62.6    # the other probes still answer
    # a driver that is present but unhappy (nonzero exit) reads the same way
    _fake_smi(monkeypatch, boom=None)
    monkeypatch.setattr(bs.subprocess, "run", lambda cmd, **kw: _Proc("", returncode=9))
    assert bs.host_vitals(tmp_path)["gpu"] == []


def test_unreadable_proc_and_missing_path_never_raise(tmp_path, monkeypatch):
    _fake_smi(monkeypatch, boom=FileNotFoundError("nvidia-smi"))
    _fake_meminfo(monkeypatch, text="garbage without the fields")

    v = bs.host_vitals(tmp_path / "does-not-exist")

    assert v["ram"] == {"used_gb": 0.0, "total_gb": 0.0}
    assert v["disk"]["free_gb"] == 0.0 and v["disk"]["total_gb"] == 0.0
    assert v["disk"]["path"].endswith("does-not-exist")


def test_three_faces_are_byte_identical(tmp_path, monkeypatch):
    """store / storecli / mcp_server return the SAME dict (the charter's one
    function, three call faces). ``ts`` is pinned so the comparison is the
    payload, not the clock."""
    runs = tmp_path / "runs"
    runs.mkdir()
    runs = runs.resolve()   # mcp_server.configure resolves; disk.path must match
    _fake_smi(monkeypatch)
    _fake_meminfo(monkeypatch)
    monkeypatch.setattr(bs.time, "time", lambda: 1787864763.0)
    ms.configure(runs, tmp_path / "STATUS.md", tmp_path / "progress.md")

    store_face = bs.host_vitals(runs)
    cli_face = sc.dispatch("host_vitals", None, runs, tmp_path / "STATUS.md", tmp_path / "progress.md")
    mcp_face = ms.host_vitals()

    wire = json.dumps(store_face, sort_keys=True)
    assert json.dumps(cli_face, sort_keys=True) == wire
    assert json.dumps(mcp_face, sort_keys=True) == wire
    assert store_face["gpu"] and store_face["ts"] == 1787864763.0  # not identity-of-empty
