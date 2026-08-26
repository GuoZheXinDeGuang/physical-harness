"""The three composite mission cards on the LIVE simulator: env registration +
obs surface + predicate reachability per task, and steam_prep's honestly-declared
driver gap (the faucet stub) as an xfail frontier.

Runs only in the robocasa venv (`pytest -m robocasa`, cwd=repo -- install
report §1.5); auto-skipped in the harness .venv. The full graph E2Es go through
the resident runtime (board submit_brief -> runs/session-robocasa), evidence in
local-archive/robocasa-adapt/missions/ -- these are the cheap per-card smokes.
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.registry import load_provider
from harness.spec import EpisodeSpec
from plugins.embodiment_robocasa import provider

_P = "plugins.embodiment_robocasa.predicates"

#: task -> (obs pose keys that must exist, a live predicate ref+params that must
#: read False on the fresh scene -- proving the wrapper reaches real state).
_SMOKES = {
    "recycle_cans": (
        ("can1_pos", "can2_pos", "can3_pos", "can4_pos"),
        (f"{_P}:obj_grasped_any", {"name": "can1"}),
    ),
    "pack_lunch": (
        ("hot0_pos", "hot1_pos", "cold0_pos", "cold1_pos",
         "tupperware0_pos", "tupperware1_pos"),
        (f"{_P}:obj_in_receptacle", {"name": "hot0", "receptacle": "tupperware0"}),
    ),
    "steam_prep": (
        ("vegetable1_pos", "pot_pos"),
        (f"{_P}:sink_water", {"on": True}),
    ),
}


@pytest.mark.robocasa
@pytest.mark.parametrize("task", sorted(_SMOKES))
def test_env_make_reset_and_predicates(task):
    emb = provider()
    spec = EpisodeSpec(seed=17, task=task)
    env = emb.make_env(spec)
    try:
        obs = env.reset()
        keys, (pref, pargs) = _SMOKES[task]
        for k in keys:
            assert k in obs, f"{task}: obs missing {k}"
            assert np.all(np.isfinite(np.asarray(obs[k])[:3]))
        meta = env.get_ep_meta()
        assert "lang" in meta and meta.get("layout_id") is not None
        # a fresh scene: nothing grasped/packed, water off -- the parametric
        # predicate reaches real live state, not a constant
        assert load_provider(pref, pargs)(env) is False
    finally:
        env.close()


@pytest.mark.robocasa
@pytest.mark.xfail(reason="awaiting sink driver: no scripted stage can actuate "
                          "the faucet handle (hinge-arc torque, the phase-3 "
                          "door finding) -- steam_prep's faucet segments run an "
                          "honest zero-action stub, so water never turns on",
                   strict=True)
def test_steam_prep_faucet_stage_turns_water_on():
    from plugins.embodiment_robocasa import drivers as D
    from plugins.embodiment_robocasa import steam_driver as S

    emb = provider()
    env = emb.make_env(EpisodeSpec(seed=17, task="steam_prep"))
    try:
        obs = env.reset()
        stage, cap = S._STAGES["faucet_on"]
        done, steps, obs = D.run_stage(env, stage(), cap, obs)
        assert done, "faucet_on stage cannot succeed without a sink driver"
    finally:
        env.close()
