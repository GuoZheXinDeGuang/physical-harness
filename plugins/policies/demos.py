"""Demonstration collection for behaviour cloning.

This module exists because round 44 found `bc_h256.npz` in version control while
the pipeline that produced it lived only in a throwaway script -- an artefact
nobody could reproduce. Rebuilding it also reproduced round 12's negative
result: a clone trained on CLEAN demonstrations closes the loop at 0.0%, no
matter the capacity, because it never sees a state its own drift will put it in.

DART is the fix and also the knob. Noise injected into the demonstrator's
executed actions widens the visited-state distribution, while the label stays
the action the expert would have taken from that state. Larger sigma buys more
robustness, so sigma is what moves a clone's competence -- not hidden width.
"""

from __future__ import annotations

import numpy as np

from governor.env import lifted, make_env, object_key
from harness.spec import EpisodeSpec
from plugins.policies.bc import encode
from plugins.policies.drivers import ScriptedDriver

#: The clone's own clock length, matching `ClonedDriver.HORIZON`.
CLOCK = 100.0


def collect_one(args: tuple) -> tuple[np.ndarray, np.ndarray] | None:
    """One demonstration. Returns None if the demonstrator itself failed.

    `args` is (seed, sigma, task, percept_noise) with an optional trailing
    env_provider ref, kept a flat tuple because it crosses a Pool boundary.
    The demonstrator itself stays the scripted expert on purpose: demos are
    the expert's behaviour, not a mounted policy's.
    """
    seed, sigma, task, percept_noise, *rest = args
    env_provider = rest[0] if rest else None
    spec = EpisodeSpec(seed=seed, task=task, percept_noise=percept_noise,
                       env_provider=env_provider)
    env = make_env(spec)
    obs = env.reset()
    start_z = float(np.asarray(obs[object_key(spec)])[2])
    driver = ScriptedDriver(spec)
    target = driver.observe_once(obs)
    rng = np.random.default_rng(seed)
    xs, ys, ok = [], [], False
    for _ in range(spec.horizon):
        xs.append(encode(obs, target, driver.k / CLOCK))
        action = np.asarray(driver.act(obs), dtype=float)
        # Label with what the expert WOULD do here; execute a perturbed version,
        # so the next state is one the expert never visits on its own.
        ys.append(action.copy())
        executed = action.copy()
        if sigma > 0:
            executed[:3] = np.clip(executed[:3] + rng.normal(0, sigma, 3), -1.0, 1.0)
        obs, _r, done, _i = env.step(executed)
        if lifted(obs, spec, start_z):
            ok = True
            break
        if done or driver.exhausted:
            break
    env.close()
    return (np.asarray(xs), np.asarray(ys)) if ok else None


def collect(n: int, *, sigma: float, first_seed: int = 20000, task: str = "lift",
            percept_noise: float = 0.004, workers: int = 10,
            env_provider: str | None = None) -> tuple[np.ndarray, np.ndarray, int]:
    """Collect `n` DART demonstrations, keeping only the ones that succeed."""
    args = [(first_seed + i, sigma, task, percept_noise, env_provider) for i in range(n)]
    from harness.executor import LocalPoolExecutor
    rows = LocalPoolExecutor().map(collect_one, args, workers=workers)
    good = [r for r in rows if r is not None]
    if not good:
        raise RuntimeError(f"no demonstration succeeded at sigma={sigma}")
    return (np.concatenate([x for x, _ in good]),
            np.concatenate([y for _, y in good]), len(good))


__all__ = ["CLOCK", "collect", "collect_one"]
