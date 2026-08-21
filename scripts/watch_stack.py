#!/usr/bin/env python
"""Watch the Stack policy -- and the round-79 promoted rule -- in a live window.

Faithfulness over convenience: this does NOT reimplement the rollout. It runs
the real `governed_rollout` through a viewer env provider whose only deviation
from the production embodiment is `has_renderer=True` plus a render call per
step. What you watch IS the code path the campaign measured.

  --scan          headless: find seeds where the plain policy fails but the
                  promoted rule saves the episode (the show-worthy ones)
  --seed N        watch one episode (window on $DISPLAY)
  --governed      arm the round-79 rule (finger_gap < 0.001 @ t>=58 -> regrasp)
  --delay S       extra per-step sleep for watchability (default 0.02)
"""
from __future__ import annotations

import argparse
import time

from harness.spec import EpisodeSpec

VIEW_REF = "scripts.watch_stack:viewer_provider"
POLICY_REF = "plugins.policies:stack_scripted_provider"
ENV_REF = "plugins.embodiment_robosuite:provider"

_DELAY = 0.02  # module default; --delay overrides via _RenderEnv


class _RenderEnv:
    """Delegating wrapper: same env, plus a render after every transition."""

    def __init__(self, env, delay: float):
        self._env = env
        self._delay = delay

    def reset(self):
        obs = self._env.reset()
        self._env.render()
        return obs

    def step(self, action):
        out = self._env.step(action)
        self._env.render()
        if self._delay:
            time.sleep(self._delay)
        return out

    def __getattr__(self, name):  # _check_success, close, sim, ...
        return getattr(self._env, name)


class ViewerEmbodiment:
    """The production embodiment with the renderer turned on."""

    def __init__(self, delay: float | None = None):
        from plugins.embodiment_robosuite import provider

        self._base = provider()
        # Read the module global at CALL time, not def time -- the def-time
        # default is exactly the binding trap STATUS warns about.
        self._delay = _DELAY if delay is None else delay

    def make_env(self, spec):
        import robosuite as suite

        from plugins.embodiment_robosuite.env import CONTROL_FREQ, task_config

        cfg = task_config(spec)
        env = suite.make(
            cfg["env"],
            robots=spec.robot,
            has_renderer=True,
            has_offscreen_renderer=False,
            use_camera_obs=False,
            control_freq=CONTROL_FREQ,
            horizon=spec.horizon,
            initialization_noise={"magnitude": spec.arm_noise, "type": "gaussian"},
            seed=spec.seed,
            **cfg["kwargs"],
        )
        return _RenderEnv(env, self._delay)

    def __getattr__(self, name):  # tasks/object_key/success/terminal_success/...
        return getattr(self._base, name)


def viewer_provider():
    return ViewerEmbodiment()


def promoted_bundle():
    """The round-79 promoted rule, verbatim from runs/stack-g1's skill record."""
    from plugins.rsi.governed import Bundle, RecoverySpec, Rule
    from plugins.rsi.stats.search import Trigger

    rule = Rule(
        "stack-g1-gen1",
        Trigger("observable.finger_gap", "lt", 0.001, 1, 58, "value"),
        RecoverySpec(name="regrasp", sensor_sd=0.020, max_invocations=1),
    )
    return Bundle(rules=(rule,), critic_budget=0, action_budget=0)


def _spec(seed: int, env_ref: str) -> EpisodeSpec:
    return EpisodeSpec(seed=seed, task="stack", percept_noise=0.012,
                       terminal_label=True,
                       env_provider=env_ref, policy_provider=POLICY_REF)


def main() -> int:
    global _DELAY
    p = argparse.ArgumentParser()
    p.add_argument("--scan", action="store_true")
    p.add_argument("--first-seed", type=int, default=90000)
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--seed", type=int)
    p.add_argument("--governed", action="store_true")
    p.add_argument("--delay", type=float, default=_DELAY)
    args = p.parse_args()

    from plugins.rsi.governed import governed_rollout

    if args.scan:
        bundle = promoted_bundle()
        for seed in range(args.first_seed, args.first_seed + args.n):
            plain = governed_rollout(_spec(seed, ENV_REF), None)
            if plain["success"]:
                continue
            gov = governed_rollout(_spec(seed, ENV_REF), bundle)
            tag = "SAVED" if gov["success"] else ("fired" if gov["fires"] else "quiet")
            print(f"seed {seed}: plain FAIL -> governed "
                  f"{'ok' if gov['success'] else 'fail'} ({tag})")
        return 0

    if args.seed is None:
        p.error("--seed required unless --scan")
    _DELAY = args.delay
    bundle = promoted_bundle() if args.governed else None
    spec = _spec(args.seed, VIEW_REF)
    label = "governed (round-79 rule armed)" if args.governed else "plain policy"
    print(f"seed {args.seed}  {label}  -- window on $DISPLAY, close when done")
    r = governed_rollout(spec, bundle)
    fired = f", critic fired at step {r['fired_at']}" if r.get("fires") else ""
    print(f"-> success={r['success']}  steps={r['steps']}{fired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
