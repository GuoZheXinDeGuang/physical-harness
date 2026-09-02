"""S4: the suite brief with arm pi05 on the REAL robocasa simulator against the
REAL pi0.5 server (started for the module in its own process group by the
``pi05_server`` fixture of tests/test_pi05_arm_sim_e2e.py, stopped after).
kitchen_thaw runs nav/grasp/carry under the scripted kitchen driver and hands the
``place`` segment to plugins.policy_vla_remote; the chain's task.verify row for
``place`` names that provider and the server's checkpoint_sha, and the suite
artifact carries the same sha. The episode need not succeed (pi0.5 place is
historically 0/10) -- the mechanism must. Nothing here touches runs/.

Run: cd <repo> && MUJOCO_GL=egl <robocasa-venv>/bin/python -m pytest -m "robocasa and vla" tests/test_suite_pi05_e2e.py
"""

from __future__ import annotations

import pytest
from test_pi05_arm_sim_e2e import SEED, VLA, pi05_server  # noqa: F401  (fixture)
from test_suite_robocasa_e2e import SUITE, drain_suite

from board import store as bs


@pytest.mark.robocasa
@pytest.mark.vla
def test_s4_pi05_suite_hands_place_to_policy_vla_remote(tmp_path, pi05_server):
    sha = pi05_server["metadata"]["checkpoint_sha"]
    assert len(sha) == 64
    runs, session, rows = drain_suite(tmp_path, "pi05", SEED, timeout=900)
    verify = {r["data"]["node"]: r["data"] for r in rows if r["kind"] == "task.verify"}
    assert "grasp" in verify and "driver" not in verify["grasp"]     # scripted arm
    assert "place" in verify, sorted(verify)                          # reached the handover
    drv = verify["place"]["driver"]
    assert drv["ref"] == VLA
    assert drv["handshake"]["metadata"]["checkpoint_sha"] == sha
    art = bs.suite_result(session)
    assert art["arm"] == "pi05" and art["checkpoint_sha"] == sha
    assert art["per_task"]["kitchen_thaw"]["n"] == 1
