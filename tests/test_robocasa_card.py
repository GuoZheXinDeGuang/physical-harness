"""RoboCasa embodiment card: env lifecycle, determinism, predicate smoke.

Runs only in the robocasa venv (`pytest -m robocasa`, cwd=repo so the sys.path
namespace-package trap never fires -- install report §1.5). In the harness .venv
robocasa is unimportable, so the conftest hook auto-skips every test here; the
extra base-lane skips are captured in docs/project-documentation.md §3.
"""

from __future__ import annotations

import numpy as np
import pytest

from harness.registry import load_provider
from harness.spec import EpisodeSpec
from plugins.embodiment_robocasa import provider
from plugins.embodiment_robocasa.predicates import PREDICATES


def _obs_sig(obs) -> np.ndarray:
    """Flatten the non-image obs into one vector for an elementwise compare."""
    return np.concatenate(
        [np.asarray(v).ravel() for k, v in sorted(obs.items()) if "image" not in k]
    )


@pytest.mark.robocasa
def test_env_make_reset_close():
    emb = provider()
    assert emb.tasks() == ("basket_smoke_vlm", "kitchen_thaw", "pack_all_robocasa",
                           "pack_lunch", "recycle_cans", "steam_prep")
    spec = EpisodeSpec(seed=7, task="kitchen_thaw")
    assert emb.object_key(spec) == "meat_pos"

    env = emb.make_env(spec)
    try:
        obs = env.reset()
        assert isinstance(obs, dict) and obs, "reset gave no obs"
        assert "meat_pos" in obs, "target object pose key missing"
        # ep_meta is the scene fingerprint the runtime archives (§3.6).
        meta = env.get_ep_meta()
        assert "lang" in meta and meta.get("layout_id") is not None
    finally:
        env.close()


@pytest.mark.robocasa
def test_same_seed_determinism():
    """Two independent envs built from the same seed + the same action sequence
    give elementwise-identical non-image obs (install report §3.6)."""
    emb = provider()
    spec = EpisodeSpec(seed=123, task="kitchen_thaw")

    def rollout():
        env = emb.make_env(spec)
        obs = env.reset()
        lo, hi = env.action_spec
        rng = np.random.default_rng(1)
        for _ in range(5):
            obs, *_ = env.step(rng.uniform(lo, hi))
        sig = _obs_sig(obs)
        lang = env.get_ep_meta().get("lang")
        env.close()
        return sig, lang

    s1, l1 = rollout()
    s2, l2 = rollout()
    assert np.allclose(s1, s2), "same-seed obs diverged -- non-deterministic"
    assert l1 == l2, "same-seed language instruction diverged"


@pytest.mark.robocasa
def test_basket_smoke_objects_share_one_reachable_counter_region():
    """Regression for the multi-section counter split seen at seed 424246."""
    emb = provider()
    for seed in (0, 424243, 424246):
        env = emb.make_env(EpisodeSpec(seed=seed, task="basket_smoke_vlm"))
        try:
            env.reset()
            names = ("item0", "item1", "item2", "basket")
            xy = {
                name: np.asarray(env.sim.data.body_xpos[env.obj_body_id[name]])[:2]
                for name in names
            }
            diameter = max(
                float(np.linalg.norm(xy[a] - xy[b]))
                for i, a in enumerate(names) for b in names[i + 1:]
            )
            assert diameter <= 0.35, (
                f"seed {seed}: basket smoke cluster spans {diameter:.3f}m; "
                "place_in is arm-only")
        finally:
            env.close()


@pytest.mark.robocasa
def test_percept_is_deterministic_in_seed_draw():
    """The determinism plugin_doctor Tier B requires, exercised directly: same
    (obs, seed, draw) -> identical estimate; a positive sd perturbs xy, not z."""
    emb = provider()
    spec = EpisodeSpec(seed=7, task="kitchen_thaw")
    env = emb.make_env(spec)
    try:
        obs = env.reset()
    finally:
        env.close()
    perc = load_provider("plugins.embodiment_robocasa.percept:provider")
    a = perc.object_estimate(obs, spec, 0.02, 0)
    b = perc.object_estimate(obs, spec, 0.02, 0)
    assert np.array_equal(a, b), "percept not deterministic in (seed, draw)"
    truth = np.asarray(obs["meat_pos"])
    assert a[2] == truth[2], "z must stay exact"
    assert not np.allclose(a[:2], truth[:2]), "xy must be perturbed at sd>0"


@pytest.mark.robocasa
def test_predicate_smoke():
    """Every PREDICATES ref load_provider-resolves to a callable that reads the
    LIVE robocasa env and returns a bool. Two truths are pinned by
    MicrowaveThawingFridge._setup_scene (opens both fridge and microwave), so the
    wrapper is proven to reach real fixture state, not just return a constant."""
    emb = provider()
    spec = EpisodeSpec(seed=7, task="kitchen_thaw")
    env = emb.make_env(spec)
    try:
        env.reset()
        results = {}
        for name, ref in PREDICATES.items():
            pred = load_provider(ref)
            val = pred(env)
            assert isinstance(val, bool), f"{name} did not return a bool"
            results[name] = val
        # _setup_scene opens the fridge and the microwave door.
        assert results["fridge_is_open"] is True
        assert results["microwave_closed"] is False
    finally:
        env.close()
