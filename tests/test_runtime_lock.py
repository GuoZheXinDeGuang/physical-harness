"""One runtime per session dir, enforced by the runtime itself.

Before this, only scripts/cockpit's `ps` scan stopped a double-run -- a guard
outside the thing it guards, which every hand-started runtime walked around.
Two runtimes on one session dir double-run briefs, interleave two writers into
one session chain, and fight over runtime_status.json.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import time

import pytest

from scripts import harness_runtime as hr


def _hold(session: str) -> None:
    hr._claim_session(hr.Path(session))
    time.sleep(120)


def _holder(session) -> multiprocessing.Process:
    """A live process holding the session claim; SIGKILLed by the caller."""
    session.mkdir(parents=True, exist_ok=True)
    proc = multiprocessing.get_context("fork").Process(
        target=_hold, args=(str(session),), daemon=True)
    proc.start()
    for _ in range(200):                      # wait for the claim to land
        try:
            if (session / hr.LOCKFILE).read_text().strip() == str(proc.pid):
                return proc
        except OSError:
            pass
        time.sleep(0.02)
    proc.kill()
    raise AssertionError("holder never took the claim")


def test_second_runtime_refuses_to_boot_and_names_the_holder(tmp_path):
    session = tmp_path / "session-main"
    proc = _holder(session)
    try:
        with pytest.raises(SystemExit) as exc:
            hr.boot(session)
        assert str(proc.pid) in str(exc.value)
        assert "already served" in str(exc.value)
        # refused BEFORE it touched anything: no MODE written, no chain opened
        assert not (session / "MODE").exists()
    finally:
        proc.kill()
        proc.join()


def test_drain_is_guarded_too(tmp_path):
    session = tmp_path / "session-drain"
    proc = _holder(session)
    try:
        with pytest.raises(SystemExit):
            hr.main(session, drain=True)
    finally:
        proc.kill()
        proc.join()


def test_a_killed_holder_leaves_no_zombie_claim(tmp_path):
    """flock, not 'the file exists': SIGKILL -9 releases it, so recovery is
    starting the runtime again -- never deleting a stale lock by hand."""
    session = tmp_path / "session-kill"
    proc = _holder(session)
    os.kill(proc.pid, signal.SIGKILL)
    proc.join()
    assert (session / hr.LOCKFILE).exists()   # the file outlives its holder
    rt = hr.boot(session)                     # ... and means nothing on its own
    assert rt.mode == "execution"


def test_reboot_by_the_same_process_is_not_a_self_refusal(tmp_path):
    """flock is keyed to the open file description, so a naive second open()
    would have this process refuse itself on a re-boot."""
    session = tmp_path / "session-reboot"
    hr.boot(session)
    hr.boot(session)
