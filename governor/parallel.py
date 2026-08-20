"""Process-pool rollout. On this machine 10 workers reach ~212 episodes/min,
which makes the rollout budget a non-issue for gating; see docs/verified-environment.md."""
from __future__ import annotations

from multiprocessing import Pool
from typing import Sequence

from governor.env import EpisodeSpec
from governor.governed import governed_rollout

DEFAULT_WORKERS = 10


def _one(spec: EpisodeSpec) -> dict:
    return governed_rollout(spec, None)


def rollout_many(specs: Sequence[EpisodeSpec], workers: int = DEFAULT_WORKERS) -> list[dict]:
    """Run every spec; results come back in submission order."""
    if workers <= 1:
        return [_one(s) for s in specs]
    with Pool(workers) as pool:
        return pool.map(_one, list(specs))
