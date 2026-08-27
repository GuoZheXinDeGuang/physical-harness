"""The parent-side hard watchdog on harness.executor's watched path.

The failure this exists for: on 2026-08-28 eight calibration workers spun at
100% CPU inside MuJoCo for three hours. The in-worker SIGALRM cap never fired
(a Python signal handler runs between bytecodes, and there were none), the pool
never drained, and 140 finished episodes were lost. A safety mechanism that
cannot fire is worse than none -- so the cap moved to the parent, where SIGKILL
does not need the child's cooperation.

The stand-in for "stuck in C" is SIGSTOP: a stopped process runs no bytecode,
so its own alarm cannot save it either, and only an outside SIGKILL ends it.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import time

from harness.executor import LocalPoolExecutor


def _lost(item, reason):
    return {"item": item, "lost": reason}


def _hangs_uninterruptibly(item):
    """Item 1 parks where no signal handler can run -- with its OWN alarm armed,
    which is the point: the in-worker cap is proved useless before the parent's
    is proved to work."""
    if item == 1:
        signal.signal(signal.SIGALRM, lambda *a: None)
        signal.alarm(1)
        os.kill(os.getpid(), signal.SIGSTOP)
        return {"item": item, "lost": "the alarm saved it"}   # never reached
    return {"item": item, "lost": None}


def test_one_wedged_worker_is_killed_and_the_batch_still_completes():
    seen = []
    t0 = time.monotonic()
    out = LocalPoolExecutor().map(
        _hangs_uninterruptibly, [0, 1, 2, 3], workers=2,
        on_result=seen.append, timeout=2.0, on_lost=_lost)
    elapsed = time.monotonic() - t0

    by_item = {r["item"]: r["lost"] for r in out}
    assert by_item == {0: None, 1: "wall_timeout", 2: None, 3: None}
    assert len(seen) == 4, "the heartbeat fires for every item, capped ones too"
    assert elapsed < 20, f"the wedged item must not starve the batch ({elapsed:.1f}s)"


def _dies_without_a_result(item):
    if item == 1:
        os._exit(9)          # segfault / OOM-killer stand-in
    return {"item": item, "lost": None}


def test_a_worker_that_vanishes_is_an_honest_row_not_a_hang():
    out = LocalPoolExecutor().map(_dies_without_a_result, [0, 1, 2], workers=3,
                                  timeout=30.0, on_lost=_lost)
    assert {r["item"]: r["lost"] for r in out} == {0: None, 1: "worker_died", 2: None}


def _raises(item):
    if item == 1:
        raise ValueError("boom")
    time.sleep(60)          # still in flight when the raise unwinds the map
    return item


def test_a_worker_exception_reaches_the_parent_and_orphans_nobody():
    """The watched path keeps Pool's contract: a bug raises, it does not quietly
    become a written-off row -- and leaving early reaps the children still
    running, the way `with Pool(...)` terminates on the way out. An orphan here
    would be another core burning for hours with nobody left to kill it."""
    t0 = time.monotonic()
    try:
        LocalPoolExecutor().map(_raises, [0, 1, 2], workers=3, timeout=300.0,
                                on_lost=_lost)
    except ValueError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("the worker's exception was swallowed")
    assert time.monotonic() - t0 < 30, "the sleeping children were waited on, not killed"
    assert not multiprocessing.active_children()


def test_no_timeout_leaves_the_plain_pool_path_untouched():
    assert sorted(LocalPoolExecutor().map(abs, [-1, 2, -3], workers=2)) == [1, 2, 3]
