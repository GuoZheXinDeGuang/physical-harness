"""Execution fabric: the local-pool provider for exec.rollouts.

Distribution later means another provider behind the same contract, not a
rewrite of any workload.
"""

from __future__ import annotations

from collections.abc import Sequence
from multiprocessing import Pool
from typing import Any


class LocalPoolExecutor:
    def map(self, fn: Any, items: Sequence, *, workers: int,
            on_result: Any = None) -> list:
        """Parallel map. ``on_result(result)`` (optional) fires in the parent as
        each item FINISHES (completion order, not input order) -- the progress
        heartbeat hook for long batteries. With it, the returned list is in
        completion order too; callers that need a canonical order sort (the
        probes already sort by seed)."""
        with Pool(workers) as pool:
            if on_result is None:
                return pool.map(fn, items)
            out = []
            for result in pool.imap_unordered(fn, items):
                out.append(result)
                on_result(result)
            return out


def provider() -> LocalPoolExecutor:
    return LocalPoolExecutor()
