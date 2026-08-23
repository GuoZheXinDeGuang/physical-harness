#!/usr/bin/env python3
"""Seed-block allocator: SUGGEST the lowest free ``[lo, lo+N)`` (R8, W2).

    PYTHONPATH=. .venv/bin/python scripts/alloc_seeds.py 200 --floor 48000

Reads the one prose seed-ledger (``STATUS.md`` 区块预算, via
``board.store.parse_ledger`` -- the SAME parser the runtime overlap-guard and the
report render) and prints the lowest ``[lo, lo+N)`` block that steps on no taken
range. This only SUGGESTS: the author reserves the printed block in STATUS.md by
hand, and the runtime overlap-guard in ``scripts/harness_runtime`` stays the
enforcement (a campaign brief whose dev∪heldout hits a BURNED range is rejected
at scheduling). STATUS.md stays human-authored; there is no write-back.

Taken = burned AND reserved. Burned because those seeds are spent; reserved
because the suggest -> reserve -> suggest-next workflow needs a just-reserved
block to fall out of the next suggestion (else the allocator hands back the same
block until it is burned). The runtime guard only enforces burned -- a reserved
block a hand-authored campaign chooses to reuse is the author's call -- so a
suggestion disjoint from burned is disjoint from the enforced set by
construction.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from board.store import parse_ledger

STATUS_MD = REPO_ROOT / "STATUS.md"

#: Ledger states whose ranges the allocator will not suggest over.
_TAKEN = ("burned", "reserved")


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and coalesce inclusive ``[lo, hi]`` intervals (touching ones fuse, so
    no phantom one-seed gap survives). Leaves the greedy below trivially correct
    against any nesting or overlap in the prose ledger."""
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def next_block(n: int, ledger: list[dict], *, floor: int = 0,
               avoid: tuple[str, ...] = _TAKEN) -> tuple[int, int]:
    """Lowest half-open ``[lo, lo+n)`` with ``lo >= floor`` hitting no avoided
    range. Returned as ``(lo, lo+n)``; the block covers seeds ``lo .. lo+n-1``."""
    if n <= 0:
        raise ValueError(f"block size N must be positive, got {n}")
    taken = _merge([(r["lo"], r["hi"]) for r in ledger if r["state"] in avoid])
    lo = floor
    for blo, bhi in taken:
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
    ap.add_argument("--status", type=Path, default=STATUS_MD,
                    help="the prose ledger to read (default STATUS.md)")
    ap.add_argument("--include-reserved", action="store_true",
                    help="only avoid BURNED (let the suggestion reuse reserved "
                         "blocks -- what the runtime guard actually enforces)")
    args = ap.parse_args(argv)
    ledger = parse_ledger(args.status.read_text())
    avoid = ("burned",) if args.include_reserved else _TAKEN
    lo, hi = next_block(args.n, ledger, floor=args.floor, avoid=avoid)
    print(f"{lo}-{hi - 1}  ([{lo}, {hi})  {args.n} seeds; reserve this in "
          f"{args.status.name} 区块预算, the runtime guard enforces burned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
