"""robocasa lane: the reach-stall watchdog on a real kitchen. A ClusterDropDriver
aimed at an unreachable drop point (3 m past the counter) must fail its stage
with failure_mode "reach_stall" well inside the 300-step drop cap instead of
burning it, and the recovery actor built for that stage must run to completion."""

from __future__ import annotations

import numpy as np
import pytest

from harness.spec import EpisodeSpec
from plugins.embodiment_robocasa import drivers as D
from plugins.embodiment_robocasa import provider
from plugins.embodiment_robocasa.recovery import RobocasaRecoveryActor, run_recovery
from plugins.embodiment_robocasa.recycle_driver import ClusterDropDriver
from plugins.rsi import repertoire


@pytest.mark.robocasa
def test_unreachable_drop_stalls_early_and_recovers():
    env = provider().make_env(EpisodeSpec(seed=4243, task="recycle_cans"))
    try:
        env.reset()
        drv = ClusterDropDriver("can1", 0)
        real = drv._drop_point(env)
        far = real + np.array([3.0, 0.0, 0.0])
        drv._point = far  # bypass the lazy stove/counter lookup with an unreachable aim
        done, steps, obs = D.run_stage(env, drv, 300)
        assert not done and drv.failure_mode == "reach_stall"
        assert D.tunables()["stall_k"] <= steps < 300, steps
        assert drv.diagnostics(env)["failure_mode"] == "reach_stall"
        # the reach repair built for this stage aims at its drop point and runs out
        drv._point = real
        d0 = float(np.linalg.norm(D._eef(env) - real))
        act = RobocasaRecoveryActor.for_stage(env, drv, repertoire.strategy("reapproach"))
        n, obs = run_recovery(env, act, obs)
        assert act.done and n == repertoire.strategy("reapproach").length
        # it moved toward the LIVE target (arrival is not asked: the spawn is out of
        # arm reach of the stove-side counter; that is base_nudge/the carry leg's job)
        assert float(np.linalg.norm(D._eef(env) - real)) < d0 - 0.05, (d0, steps)
    finally:
        env.close()
