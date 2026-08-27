"""Execution fabric: the local-pool provider for exec.rollouts.

Distribution later means another provider behind the same contract, not a
rewrite of any workload.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Sequence
from multiprocessing import Pool
from typing import Any

#: Parent-side poll cadence on the watched path. Invisible against episodes
#: measured in minutes, and it bounds how far a hung item overshoots its cap.
_TICK = 0.25

#: Sentinel for an exhausted input iterator (``None`` is a legal item).
_DONE = object()


def _child(fn: Any, item: Any, conn: Any) -> None:
    """Run ONE item in a fresh process and hand the outcome back over the pipe.

    The exception rides home too, so the watched path keeps ``Pool``'s contract
    of re-raising a worker's error in the parent instead of quietly turning a
    bug into a data row. An unpicklable exception cannot be sent; the parent
    then sees a child that exited with nothing, which is the ``worker_died``
    row -- honest either way.
    """
    try:
        conn.send(("ok", fn(item)))
    except BaseException as exc:  # noqa: BLE001 -- re-raised in the parent
        try:
            conn.send(("err", exc))
        except Exception:  # noqa: BLE001,S110 -- unpicklable: the EOF speaks for it
            pass
    finally:
        conn.close()


class LocalPoolExecutor:
    def map(self, fn: Any, items: Sequence, *, workers: int,
            on_result: Any = None, timeout: float | None = None,
            on_lost: Any = None) -> list:
        """Parallel map. ``on_result(result)`` (optional) fires in the parent as
        each item FINISHES (completion order, not input order) -- the progress
        heartbeat hook for long batteries. With it, the returned list is in
        completion order too; callers that need a canonical order sort (the
        probes already sort by seed).

        ``timeout`` (seconds, per item) arms a PARENT-SIDE hard watchdog and
        requires ``on_lost(item, reason)`` -> the honest result row for an item
        the parent had to write off (``reason`` is ``"wall_timeout"`` or
        ``"worker_died"``). Without it the plain ``Pool`` path runs, unchanged.

        Why the watched path is not a ``Pool``: an in-worker cap (SIGALRM) only
        fires between bytecodes, so a worker spinning inside native code never
        sees it -- 8 workers burnt 3h each inside MuJoCo on 2026-08-28 and took
        140 finished episodes down with them. Only a process OUTSIDE the stuck
        one can end it, and ``Pool`` gives the parent no handle on which worker
        holds which item (SIGKILLing one silently strands its task and
        ``imap_unordered`` waits forever). So the watched path fans out one
        child PER ITEM, at most ``workers`` alive: the parent knows every pid
        and start time, kills only the offender, and the batch finishes.
        ``concurrent.futures`` was the other candidate and loses here --
        ``Future.result(timeout)`` neither frees the slot nor stops the work,
        and killing the worker breaks the whole executor.

        ponytail: fork-per-item pays one interpreter warm-up per item instead
        of per worker (~seconds against minute-long episodes). Reuse workers
        again only if that ratio ever inverts.
        """
        if timeout is None:
            with Pool(workers) as pool:
                if on_result is None:
                    return pool.map(fn, items)
                out = []
                for result in pool.imap_unordered(fn, items):
                    out.append(result)
                    on_result(result)
                return out
        if on_lost is None:
            raise ValueError("timeout= needs on_lost(item, reason) -- the caller "
                             "owns the row shape a written-off item returns")

        ctx = multiprocessing.get_context("fork")
        queue = iter(items)
        live: dict = {}          # Process -> (item, parent_conn, started)
        out: list = []
        exhausted = False
        try:
            while not exhausted or live:
                while not exhausted and len(live) < workers:
                    item = next(queue, _DONE)
                    if item is _DONE:
                        exhausted = True
                        break
                    rx, tx = ctx.Pipe(duplex=False)
                    proc = ctx.Process(target=_child, args=(fn, item, tx), daemon=True)
                    proc.start()
                    tx.close()   # drop the parent's write end: EOF now means death
                    live[proc] = (item, rx, time.monotonic())
                settled = []
                for proc, (item, rx, started) in live.items():
                    # Sample liveness BEFORE draining the pipe: a child that was
                    # alive here may still send, but one already dead has sent
                    # everything it will ever send, so poll() cannot miss it.
                    dead = not proc.is_alive()
                    if rx.poll(0):
                        try:
                            tag, payload = rx.recv()
                        except EOFError:   # gone, write end closed, nothing sent
                            settled.append((proc, on_lost(item, "worker_died")))
                            continue
                        if tag == "err":
                            raise payload
                        settled.append((proc, payload))
                    elif dead:
                        settled.append((proc, on_lost(item, "worker_died")))
                    elif time.monotonic() - started > timeout:
                        proc.kill()      # SIGKILL: what native code cannot outrun
                        settled.append((proc, on_lost(item, "wall_timeout")))
                for proc, result in settled:
                    live.pop(proc)[1].close()
                    proc.join()
                    out.append(result)
                    if on_result is not None:
                        on_result(result)
                if not settled:
                    time.sleep(_TICK)
        finally:
            # Leaving early (a worker's exception, Ctrl-C) must not orphan a
            # child still burning a core -- ``with Pool(...)`` terminates on the
            # way out and so does this. Empty on the normal path.
            for proc, (_item, rx, _started) in live.items():
                proc.kill()
                rx.close()
                proc.join()
        return out


def provider() -> LocalPoolExecutor:
    return LocalPoolExecutor()
