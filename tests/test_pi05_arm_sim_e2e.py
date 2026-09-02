"""The pi05 arm on the REAL robocasa simulator against the REAL pi0.5 server:
{"kind":"task","task":"kitchen_thaw","arm":"pi05"} drained by the real
scripts/harness_runtime.py subprocess runs nav/grasp under the scripted kitchen
driver and hands the ``place`` segment to plugins.policy_vla_remote; its
task.verify row names that provider ref and the server's checkpoint_sha. The
episode need not succeed (pi0.5 place is historically 0/10) -- the routing and
the attribution must. Nothing here touches runs/.

Run: MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m "robocasa and vla" tests/test_pi05_arm_sim_e2e.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from board import store as bs

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "scripts" / "harness_runtime.py"
OPENPI = Path("/home/yusenzlabpc/Desktop/Learning_based_model/openpi")
CKPT = OPENPI / "checkpoints/pi05_robocasa_lora/gate2_bs8/199"
VLA = "plugins.policy_vla_remote:provider"
PORT = 8000
#: a scratch seed the scripted nav/grasp/carry prelude is known to complete on
SEED = int(os.environ.get("PI05_E2E_SEED", "429002"))


@pytest.fixture(scope="module")
def pi05_server():
    """The served checkpoint's handshake. Reuses a server already on :8000; else
    starts scripts/serve_vla_openpi.py in its own process group and kills the
    group after the module (JAX workers included)."""
    from harness.manifest import mount_params
    from plugins.policy_vla_remote import RemoteVlaPolicy

    factory = RemoteVlaPolicy(host="127.0.0.1", port=PORT, **mount_params(VLA))
    proc = None
    if not factory.available():
        if not (OPENPI / ".venv/bin/python").exists() or not CKPT.is_dir():
            pytest.skip("openpi venv or the gate2 checkpoint is missing on this box")
        proc = subprocess.Popen(
            [str(OPENPI / ".venv/bin/python"), str(REPO / "scripts/serve_vla_openpi.py"),
             "--checkpoint-dir", str(CKPT), "--port", str(PORT)],
            cwd=str(REPO), start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(REPO)})
        deadline = time.time() + 600
        while not factory.available():
            assert proc.poll() is None, "pi0.5 server died while loading"
            assert time.time() < deadline, "pi0.5 server never listened on :8000"
            time.sleep(2)
    try:
        yield factory.connect()
    finally:
        if proc is not None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


@pytest.mark.robocasa
@pytest.mark.vla
def test_pi05_arm_drives_place_through_policy_vla_remote(tmp_path, pi05_server):
    sha = pi05_server["metadata"]["checkpoint_sha"]
    assert len(sha) == 64
    runs = tmp_path / "runs"
    session = runs / "session-main"
    res = bs.submit_brief(runs, json.dumps(
        {"kind": "task", "task": "kitchen_thaw", "seed": SEED, "arm": "pi05",
         "max_actuations": 24}))
    name = res["submitted"]
    proc = subprocess.run(
        [sys.executable, str(RUNTIME), "--session-dir", str(session), "--drain"],
        cwd=str(REPO), capture_output=True, text=True, timeout=2400, check=False,
        env={**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO)})
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert (session / "done" / name).exists(), proc.stderr[-4000:]
    rows = bs.chain_rows(session)
    verify = {r["data"]["node"]: r["data"] for r in rows if r["kind"] == "task.verify"}
    assert not [r for r in rows if r["kind"] == "runtime.task_error"], proc.stderr[-4000:]
    assert "grasp" in verify and "driver" not in verify["grasp"]     # scripted arm
    assert "place" in verify, sorted(verify)                          # reached the handover
    drv = verify["place"]["driver"]
    assert drv["ref"] == VLA
    assert drv["handshake"]["metadata"]["checkpoint_sha"] == sha
    assert drv["handshake"]["contract"]["unnorm_key"] == "robocasa/lerobot"
