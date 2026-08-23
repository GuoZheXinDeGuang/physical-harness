"""R4: the mode mechanism (v4.1 hard rule, M4 rung-3 revision).

Pure fakes -- no simulation, no real campaign. The campaign handler is
monkeypatched to a recorder so what is under test is the MODE layer alone:

  1. execution (the fail-safe default) rejects a campaign brief to failed/ with a
     runtime.task_error note, and never reaches _run_campaign;
  2. evolution accepts it (reaches _run_campaign, lands in done/);
  3. a mismatched --mode on re-boot is refused (MODE is write-once);
  4. the boot seal is chain row 0 = {mode, skills_manifest, mount_plan_sha};
  5. a skill added to skills_root out-of-band mid-session makes the next
     execution task raise the set-equality immutability audit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.config import resolve_plan
from harness.events import SessionLog
from profiles import base_profile
from scripts import harness_runtime as runtime


def _drop(inbox: Path, name: str, brief: dict) -> None:
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps(brief))
    os.replace(tmp, inbox / name)


def test_execution_rejects_campaign_to_failed(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(runtime, "_run_campaign", lambda *a, **k: called.append(a))
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "camp.json", {"kind": "campaign", "campaign": "stack"})

    rt = runtime.main(session, drain=True)  # default execution

    assert not called, "a campaign brief must never reach _run_campaign in execution mode"
    assert (rt.failed / "camp.json").exists()
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1 and "evolution mode" in errors[0]["data"]["error"]
    assert SessionLog.load(session / "session-log").verify()


def test_evolution_accepts_campaign(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(runtime, "_run_campaign", lambda *a, **k: called.append(a))
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "camp.json", {"kind": "campaign", "campaign": "stack"})

    rt = runtime.main(session, drain=True, mode="evolution")

    assert len(called) == 1, "an evolution-mode campaign brief reaches _run_campaign"
    assert (rt.done / "camp.json").exists()
    assert not [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]


def test_mode_is_write_once_across_reboot(tmp_path):
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    runtime.boot(session, mode="execution")
    assert (session / "MODE").read_text().strip() == "execution"

    with pytest.raises(ValueError, match="write-once"):
        runtime.boot(session, mode="evolution")

    # re-booting with the SAME mode is fine (the resume path).
    rt = runtime.boot(session, mode="execution")
    assert rt.mode == "execution"


def test_boot_seal_is_chain_row_zero(tmp_path):
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    # a skill already present at first boot enters the sealed manifest.
    skills = session / "skills"
    skills.mkdir()
    (skills / "cafe.json").write_text("{}")

    rt = runtime.boot(session, mode="evolution")

    row0 = rt.log.rows()[0]
    assert row0["kind"] == "runtime.boot"
    assert row0["data"] == {
        "mode": "evolution",
        "skills_manifest": ["cafe"],
        "mount_plan_sha": resolve_plan(base_profile()).sha(),
    }
    assert rt.skills_manifest == ("cafe",)
    assert SessionLog.load(session / "session-log").verify()


def test_out_of_band_skill_fails_next_execution_task(tmp_path):
    session = tmp_path / "session-main"
    (session / "inbox").mkdir(parents=True)
    rt = runtime.boot(session, mode="execution")  # seals an empty manifest
    assert rt.skills_manifest == ()

    # a record appears in the frozen root that the boot seal never admitted.
    (rt.skills_root / "deadbeef.json").write_text("{}")

    _drop(rt.inbox, "t.json", {"kind": "task", "task": "stack", "seed": 90000})
    runtime._process(rt, rt.inbox / "t.json")  # audit fires before _run_task

    assert (rt.failed / "t.json").exists()
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1 and "skills-root mutated" in errors[0]["data"]["error"]
    assert SessionLog.load(session / "session-log").verify()
