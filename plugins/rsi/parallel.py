"""Process-pool rollout. On this machine 10 workers reach ~212 episodes/min,
which makes the rollout budget a non-issue for gating; see docs/verified-environment.md."""
from __future__ import annotations

from collections.abc import Sequence

from governor.env import EpisodeSpec
from governor.governed import governed_rollout

DEFAULT_WORKERS = 10


def default_executor():
    """The exec.rollouts provider used when no executor is injected.

    L1 rung 2: every rollout fan-out in governor goes through an executor with
    the harness RolloutExecutor contract. Call sites take `executor=None` and
    fall back to this local pool, which maps exactly like the Pool.map blocks
    it replaced; the kernel-mounted path injects the resolved provider instead.
    Executors run in the PARENT process (they create the pools), so unlike
    provider refs this needs no spawn story.
    """
    from harness.executor import LocalPoolExecutor

    return LocalPoolExecutor()


def _one(spec: EpisodeSpec) -> dict:
    return governed_rollout(spec, None)


def rollout_many(specs: Sequence[EpisodeSpec], workers: int = DEFAULT_WORKERS,
                 executor=None) -> list[dict]:
    """Run every spec; results come back in submission order."""
    if workers <= 1:
        return [_one(s) for s in specs]
    return (executor or default_executor()).map(_one, list(specs), workers=workers)
