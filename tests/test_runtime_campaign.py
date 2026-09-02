"""RUNG 3: RSI campaign as an in-system task.

No real 15-40min campaign here -- a STUB script standing in for the CLI-shaped
campaign proves the runtime layer: kind=="campaign" passes the seed-ledger guard
(over the REAL STATUS.md ledger), spawns the fixed allowlisted script as a
subprocess, folds its published skills into the shared graph.skill root so a
FOLLOWING task's fresh mount re-globs them, and writes one
``runtime.campaign_scheduled`` note (prereg_sha + out) into the chain. A brief
whose declared dev∪heldout lands in a burned range is rejected to failed/ with a
runtime.task_error note and never spawns.

Campaigns are booted in EVOLUTION mode: the v4.1 hard rule accepts a campaign
brief only there (execution-mode rejection is covered in tests/test_mode.py).

# ponytail: real 15-40min campaign is a manual RECORD run, not a unit test.
"""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from harness import manifest
from plugins.graphs import skill_graph_provider
from scripts import harness_runtime as runtime

_STUB_DIGEST = "stubskill0000000000000000000000000000000000000000000000000000feed"
_STUB_PREREG = "stubprereg00000000000000000000000000000000000000000000000000cafe"

# A campaign script stand-in: writes one content-addressed skill record and a
# store index whose first row is the preregistration (the shape run_campaign
# writes), then exits 0 -- exactly what the runtime observes from a real run.
_STUB_SCRIPT = textwrap.dedent(f'''\
    import argparse, json, sys
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=10)
    out = ap.parse_args().out
    (out / "skills").mkdir(parents=True, exist_ok=True)
    (out / "skills" / "{_STUB_DIGEST}.json").write_text(
        json.dumps({{"generation": 1, "stub": True}}))
    (out / "index.jsonl").write_text(
        json.dumps({{"seq": 0, "kind": "preregistration", "sha": "{_STUB_PREREG}"}}) + "\\n")
    sys.exit(0)
''')


def _drop(inbox: Path, name: str, brief: dict) -> None:
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps(brief))
    os.replace(tmp, inbox / name)


def _stub_script(tmp_path: Path) -> Path:
    p = tmp_path / "stub_campaign.py"
    p.write_text(_STUB_SCRIPT)
    return p


def _campaign(monkeypatch, name: str, script: Path) -> None:
    """Point campaign ``name`` at a fast stub, the way installing a plugin would.

    The campaign allowlist is the manifest union now, not a module dict, so the
    stub is injected by folding it into the boot registry -- boot() reads
    ``runtime.discover``; base_profile's own discover is untouched, so the sealed
    mount_plan_sha never moves. An absolute stub path survives ``REPO_ROOT / ...``.
    """
    reg = replace(manifest.discover(),
                  campaigns={**manifest.discover().campaigns, name: str(script)})
    monkeypatch.setattr(runtime, "discover", lambda: reg)


def _burn(tmp_path: Path, dev: tuple[int, int], heldout: tuple[int, int]) -> Path:
    """Seal one preregistration under the test's runs root (``session.parent``):
    the guard derives its burned set from sealed preregs, never from prose."""
    from test_store import _mkstore
    prereg = {"dev": list(range(dev[0], dev[1] + 1)),
              "heldout": list(range(heldout[0], heldout[1] + 1)), "task": "stack"}
    return _mkstore(tmp_path / f"burn-{dev[0]}", [("preregistration", prereg)])


def test_campaign_publishes_skills_and_notes_the_chain(tmp_path, monkeypatch):
    _campaign(monkeypatch, "stub", _stub_script(tmp_path))
    _burn(tmp_path, (41000, 41580), (42000, 42199))
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    # clear seeds: the one sealed prereg burns <=42199, so 90000+ never hits.
    _drop(inbox, "camp.json", {"kind": "campaign", "campaign": "stub",
                               "dev": [[90000, 90499]], "heldout": [[90500, 90599]]})

    rt = runtime.main(session, drain=True, mode="evolution")

    assert (rt.done / "camp.json").exists(), "a clean campaign lands in done/"
    assert (rt.skills_root / f"{_STUB_DIGEST}.json").exists(), "skills copied to shared root"

    notes = [r for r in rt.log.rows() if r["kind"] == "runtime.campaign_scheduled"]
    assert len(notes) == 1
    data = notes[0]["data"]
    assert data["campaign"] == "stub"
    assert data["prereg_sha"] == _STUB_PREREG, "prereg hash read back from the store index"
    assert data["skills"] == [_STUB_DIGEST]

    # M4#4: a FOLLOWING task's fresh graph.skill mount re-globs the shared root
    # and sees the record with no extra plumbing.
    reglobbed = skill_graph_provider(root=str(rt.skills_root)).skills()
    assert any(s.get("stub") for s in reglobbed), "next task's fresh mount sees the new skill"

    assert runtime.SessionLog.load(session / "session-log").verify()


def test_second_campaign_is_idempotent_on_shared_skills(tmp_path, monkeypatch):
    # Re-running a campaign that republishes the same content-addressed record
    # must not error or double-copy -- the stem IS the digest.
    _campaign(monkeypatch, "stub", _stub_script(tmp_path))
    _burn(tmp_path, (41000, 41580), (42000, 42199))
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "one.json", {"kind": "campaign", "campaign": "stub", "dev": [[90000, 90099]]})
    _drop(inbox, "two.json", {"kind": "campaign", "campaign": "stub", "dev": [[90100, 90199]]})

    rt = runtime.main(session, drain=True, mode="evolution")

    assert (rt.done / "one.json").exists() and (rt.done / "two.json").exists()
    assert list(rt.skills_root.glob("*.json")) == [rt.skills_root / f"{_STUB_DIGEST}.json"]
    notes = [r for r in rt.log.rows() if r["kind"] == "runtime.campaign_scheduled"]
    assert [n["data"]["skills"] for n in notes] == [[_STUB_DIGEST], [_STUB_DIGEST]]


def test_acceptance_campaign_cmd_threads_the_claim_card_dir():
    """A [campaigns] entry pointing at the parameterized acceptance_campaign is
    runnable via submit_brief: the runtime threads --claim <owning card dir>,
    resolved from the manifest that declares the campaign name (round 97)."""
    out = Path("/tmp/x")
    script = runtime.REPO_ROOT / "scripts" / "acceptance_campaign.py"
    cmd = runtime._campaign_cmd("lift_geometric", script, out)
    assert "--claim" in cmd
    assert cmd[cmd.index("--claim") + 1].endswith("plugins/skill_geometric_grasp")
    assert cmd[cmd.index("--out") + 1] == str(out)


def test_self_contained_campaign_cmd_has_no_claim():
    """A self-contained campaign script (stack_campaign) takes only --out; the
    runtime must not slip a --claim it does not accept."""
    script = runtime.REPO_ROOT / "scripts" / "stack_campaign.py"
    cmd = runtime._campaign_cmd("stack", script, Path("/tmp/x"))
    assert "--claim" not in cmd


def test_burned_range_brief_is_rejected_without_spawning(tmp_path, monkeypatch):
    # Point the real "stack" key at the stub too: if the guard WRONGLY passed,
    # the stub would run and write a campaign_scheduled note instead -- the
    # assertion on the overlap reason then fails fast rather than hanging.
    _campaign(monkeypatch, "stack", _stub_script(tmp_path))
    # The guard derives the burned set from sealed preregs under the runs root,
    # so seal one of our own instead of scanning whatever this box has burned.
    _burn(tmp_path, (41000, 41580), (42000, 42199))
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    # 41000-41100 intersects the burned 41000-41580 the fixture ledger declares.
    _drop(inbox, "burned.json", {"kind": "campaign", "campaign": "stack",
                                 "dev": [[41000, 41100]]})

    rt = runtime.main(session, drain=True, mode="evolution")

    assert (rt.failed / "burned.json").exists(), "a burned-range brief is rejected"
    assert not list(rt.skills_root.glob("*.json")), "the guard fires before any spawn"
    scheduled = [r for r in rt.log.rows() if r["kind"] == "runtime.campaign_scheduled"]
    assert scheduled == [], "a rejected campaign never notes success"
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1
    assert "seed-ledger overlap" in errors[0]["data"]["error"]


def test_guard_refuses_gate_without_any_store_but_allows_calibration(tmp_path):
    """An absent ledger is not an empty one: no sealed prereg anywhere under the
    runs root refuses a dev/held-out claim outright, while a calibration-only
    brief (nothing to gate) never consults the ledger."""
    runtime._assert_unburned({"cal": [[1, 150]]}, "cal-only", tmp_path)
    with pytest.raises(ValueError, match="no campaign store"):
        runtime._assert_unburned({"dev": [[1, 300]]}, "gate", tmp_path)
    _burn(tmp_path, (41000, 41580), (42000, 42199))
    runtime._assert_unburned({"dev": [[1, 300]], "heldout": [[301, 500]]}, "gate", tmp_path)
    with pytest.raises(ValueError, match="seed-ledger overlap"):
        runtime._assert_unburned({"heldout": [[42100, 42300]]}, "gate", tmp_path)
