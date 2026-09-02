"""R8: the seed-block allocator SUGGESTS the lowest free [lo, lo+N).

Fakes only -- inclusive (lo, hi) burned intervals, the shape ``next_block``
consumes (``board.store.burned_blocks`` rows with role/sha stripped). The
acceptance: the suggested block is disjoint from every burned range and is
exactly N wide.
"""

from __future__ import annotations

import pytest

from scripts.alloc_seeds import next_block

_TAKEN = [(1000, 1999), (2000, 2899), (3000, 3100)]


def test_disjoint_from_burned_and_respects_n():
    lo, hi = next_block(200, _TAKEN, floor=1000)
    assert hi - lo == 200
    for blo, bhi in _TAKEN:
        assert not (lo <= bhi and blo <= hi - 1), (lo, hi, blo, bhi)
    # 1000-2899 fused, only 100 free seeds before 3000-3100 -> lands after it
    assert (lo, hi) == (3101, 3301)


def test_fits_a_gap_when_n_is_small():
    assert next_block(50, _TAKEN, floor=1000) == (2900, 2950)


def test_lowest_is_below_everything_when_floor_is_zero():
    assert next_block(200, _TAKEN, floor=0) == (0, 200)


def test_zero_block_is_loud():
    with pytest.raises(ValueError, match="positive"):
        next_block(0, _TAKEN)
