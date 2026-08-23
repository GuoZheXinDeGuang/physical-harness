"""R8: the seed-block allocator SUGGESTS the lowest free [lo, lo+N).

Fakes only -- a synthetic ledger of (lo, hi, state) rows, the exact shape
``board.store.parse_ledger`` returns. The acceptance: the suggested block is
disjoint from every burned range and is exactly N wide.
"""

from __future__ import annotations

import pytest

from scripts.alloc_seeds import next_block


def _ledger(*rows):  # (lo, hi, state)
    return [{"lo": lo, "hi": hi, "state": st} for lo, hi, st in rows]


def _overlaps(block, lo, hi):
    blo, bhi = block  # half-open [blo, bhi) covers blo .. bhi-1
    return blo <= hi and lo <= bhi - 1


_LEDGER = _ledger((1000, 1999, "burned"), (2000, 2899, "burned"),
                  (3000, 3100, "reserved"), (5000, 5100, "planned"))


def test_disjoint_from_burned_and_respects_n():
    lo, hi = next_block(200, _LEDGER, floor=1000)
    assert hi - lo == 200                                   # respects N
    for r in _LEDGER:
        if r["state"] == "burned":
            assert not _overlaps((lo, hi), r["lo"], r["hi"]), (lo, hi, r)
    # 1000-2899 burned (fused), only 100 free seeds before reserved 3000-3100,
    # so a 200-block cannot fit the gap and lands after the reserved range.
    assert (lo, hi) == (3101, 3301)


def test_fits_a_gap_when_n_is_small():
    lo, hi = next_block(50, _LEDGER, floor=1000)
    assert (lo, hi) == (2900, 2950)                         # 50 fits the 100-wide gap


def test_lowest_is_below_everything_when_floor_is_zero():
    assert next_block(200, _LEDGER, floor=0) == (0, 200)


def test_avoids_reserved_so_reserve_then_next_advances():
    # the suggest -> reserve -> suggest-next workflow: once a block is reserved it
    # must fall out of the next suggestion (default avoids burned AND reserved).
    lo, _ = next_block(50, _LEDGER, floor=3000)
    assert lo == 3101


def test_include_reserved_only_avoids_burned():
    # the runtime guard enforces burned only; --include-reserved mirrors it.
    assert next_block(50, _LEDGER, floor=3000, avoid=("burned",)) == (3000, 3050)


def test_zero_block_is_loud():
    with pytest.raises(ValueError, match="positive"):
        next_block(0, _LEDGER)
