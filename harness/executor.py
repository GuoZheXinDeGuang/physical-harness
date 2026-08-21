"""Execution fabric: the local-pool provider for exec.rollouts.

Distribution later means another provider behind the same contract, not a
rewrite of any workload.
"""

from __future__ import annotations

from collections.abc import Sequence
from multiprocessing import Pool
from typing import Any


class LocalPoolExecutor:
    def map(self, fn: Any, items: Sequence, *, workers: int) -> list:
        with Pool(workers) as pool:
            return pool.map(fn, items)


def provider() -> LocalPoolExecutor:
    return LocalPoolExecutor()
