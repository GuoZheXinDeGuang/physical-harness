#!/usr/bin/env python3
"""Seed-block allocator: SUGGEST the lowest free ``[lo, lo+N)`` (R8, W2).

    PYTHONPATH=. .venv/bin/python scripts/alloc_seeds.py 200 --floor 48000

Reads the DERIVED seed ledger (``board.store.burned_blocks``: every sealed
preregistration under runs/ -- the SAME source the runtime overlap-guard and
``scripts/rsi_campaign.allocate`` consume) and prints the lowest ``[lo, lo+N)``
block that steps on no burned range. This only SUGGESTS; the runtime guard in
``scripts/harness_runtime`` stays the enforcement. No store at all is an
error, never "nothing burned".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from board.store import burned_blocks


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and coalesce inclusive ``[lo, hi]`` intervals (touching ones fuse, so
    no phantom one-seed gap survives)."""
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def next_block(n: int, taken: list[tuple[int, int]], *, floor: int = 0) -> tuple[int, int]:
    """Lowest half-open ``[lo, lo+n)`` with ``lo >= floor`` hitting no inclusive
    ``[lo, hi]`` in ``taken``. Returned as ``(lo, lo+n)``."""
    if n <= 0:
        raise ValueError(f"block size N must be positive, got {n}")
    lo = floor
    for blo, bhi in _merge(taken):
        if lo + n <= blo:      # block fits entirely before this range -> done
            break
        if lo <= bhi:          # [lo, lo+n) overlaps inclusive [blo, bhi] -> jump past
            lo = bhi + 1
    return lo, lo + n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("n", type=int, help="block size N (number of seeds)")
    ap.add_argument("--floor", type=int, default=0,
                    help="lowest seed to consider (default 0; pass the evidence "
                         "frontier, e.g. 48000, to skip low unrelated gaps)")
    ap.add_argument("--runs", type=Path, default=REPO_ROOT / "runs",
                    help="runs/ tree whose sealed preregistrations burn blocks")
    args = ap.parse_args(argv)
    taken = [(lo, hi) for lo, hi, _role, _sha in burned_blocks(args.runs)]
    lo, hi = next_block(args.n, taken, floor=args.floor)
    print(f"{lo}-{hi - 1}  ([{lo}, {hi})  {args.n} seeds; disjoint from every sealed "
          f"prereg under {args.runs}, the runtime guard enforces the same set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
