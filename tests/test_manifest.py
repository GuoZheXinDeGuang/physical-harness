"""R5: manifest self-registration + plugin-declared task binding.

The charter acceptance IS this test: drop a plugin dir carrying a manifest that
binds a NEW task name, and the runtime accepts a brief for it -- with no edit to
the base (harness/, profiles, harness_runtime, test_boundaries). Here the toy
card (``plugins/skill_toy/``) is that drop; it needs zero base lines, so its mere
presence-as-a-dir is the proof. The rest pins the fold's guarantees: the base
plan sha is a pure function of the installed manifest set (a mount-declaring card
moves it, a task-only card does not), a duplicate capability across manifests is
loud, an ``actuation:real`` card is refused, and a brief still cannot name a
provider.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness.config import Profile, resolve_plan
from harness.events import SessionLog
from harness.manifest import discover
from plugins.task import workload
from profiles import base_profile
from scripts import harness_runtime as runtime

#: The base plan sha sealed in runs/round25-rerun (a kernel.mount plan_sha). The
#: manifest fold must reproduce it byte-for-byte -- installing a task-only card
#: (skill_toy) declares no mount, so the base identity is unchanged.
_SEALED_BASE_SHA = "b905a5119415de325c8c1d13f7c8a552eb3b2e8565e0a8af4ef7ca9c5f4c1a7c"


def _ok_rollout(spec, bundle=None):
    return {"success": True, "steps": 10, "stages": [
        {"name": "grasp", "success": True}, {"name": "place", "success": True}]}


def _drop(inbox: Path, name: str, brief: dict) -> None:
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps(brief))
    os.replace(tmp, inbox / name)


def _write_manifest(root: Path, plugin: str, body: str) -> None:
    (root / plugin).mkdir(parents=True, exist_ok=True)
    (root / plugin / "manifest.toml").write_text(body)


def test_toy_card_registers_a_task_without_touching_the_base():
    """The committed toy card's binding is in the union, and the base sha is
    unchanged -- a task-only card is a new selector, not a new experiment identity."""
    reg = discover()
    assert "toy" in reg.task_bindings, "the dropped-in card's task is in the union"
    assert reg.task_bindings["toy"]["planner"] == "plugins.skill_toy.planner:provider"
    assert resolve_plan(base_profile()).sha() == _SEALED_BASE_SHA


def test_runtime_accepts_the_manifest_declared_task(tmp_path, monkeypatch):
    """The whole acceptance: {"kind":"task","task":"toy"} runs to done/ with the
    runtime reading NO hard-coded task table -- only the manifest union."""
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "session-main"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "toy.json", {"kind": "task", "task": "toy", "seed": 90000})

    rt = runtime.main(session, drain=True)  # default execution mode

    assert (rt.done / "toy.json").exists(), "a manifest-declared task is accepted"
    completes = [r for r in rt.log.rows() if r["kind"] == "task.plan_complete"]
    assert len(completes) == 1 and completes[0]["data"]["success"]
    assert SessionLog.load(session / "session-log").verify()


def test_a_brief_still_cannot_name_a_provider(tmp_path, monkeypatch):
    """Authority preserved: the task string is the only selector; a smuggled
    provider ref is an unknown key and is rejected to failed/."""
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "s"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "evil.json",
          {"kind": "task", "task": "toy", "planner": "evil.module:provider"})

    rt = runtime.main(session, drain=True)

    assert (rt.failed / "evil.json").exists()
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1 and "unknown brief keys" in errors[0]["data"]["error"]


def test_an_unknown_task_is_refused(tmp_path, monkeypatch):
    """A task no installed card declares has no binding -- refused before any mount."""
    monkeypatch.setattr(workload, "_governed_rollout", _ok_rollout)
    session = tmp_path / "s"
    inbox = session / "inbox"
    inbox.mkdir(parents=True)
    _drop(inbox, "ghost.json", {"kind": "task", "task": "ghost", "seed": 90000})

    rt = runtime.main(session, drain=True)

    assert (rt.failed / "ghost.json").exists()
    errors = [r for r in rt.log.rows() if r["kind"] == "runtime.task_error"]
    assert len(errors) == 1 and "no task binding for 'ghost'" in errors[0]["data"]["error"]


def test_a_mount_declaring_card_moves_the_plan_sha(tmp_path):
    """The mode-seal principle reaching plugins: a card that CHANGES the mounts
    changes base_profile's sha = a different experiment identity."""
    _write_manifest(tmp_path, "card_a", '[mounts."graph.skill"]\n'
                    'ref = "plugins.graphs:skill_graph_provider"\n')
    base = resolve_plan(Profile("base", discover(tmp_path).mounts)).sha()

    _write_manifest(tmp_path, "card_b", '[mounts."graph.scene"]\n'
                    'ref = "plugins.graphs:scene_graph_provider"\n')
    grown = resolve_plan(Profile("base", discover(tmp_path).mounts)).sha()

    assert base != grown, "adding a mount-declaring card must move the plan sha"


def test_duplicate_capability_across_manifests_is_loud(tmp_path):
    body = '[mounts."graph.scene"]\nref = "plugins.graphs:scene_graph_provider"\n'
    _write_manifest(tmp_path, "card_a", body)
    _write_manifest(tmp_path, "card_b", body)
    with pytest.raises(ValueError, match="duplicate capability 'graph.scene'"):
        discover(tmp_path)


def test_a_task_and_a_campaign_may_share_a_name(tmp_path):
    """Independent name spaces: only a second card claiming the SAME kind collides."""
    _write_manifest(tmp_path, "card", '[task_bindings.stack]\n'
                    'policy = "p:x"\nplanner = "p:x"\ncatalogue = "p:C"\noracles = "p:O"\n'
                    '[campaigns]\nstack = "scripts/stack_campaign.py"\n')
    reg = discover(tmp_path)
    assert "stack" in reg.task_bindings and "stack" in reg.campaigns


def test_actuation_real_is_refused(tmp_path):
    _write_manifest(tmp_path, "real_arm", 'actuation = "real"\n'
                    '[mounts."embodiment.env"]\nref = "some.real:provider"\n')
    with pytest.raises(ValueError, match="actuation:real"):
        discover(tmp_path)


# ── bundles: card-owned overlays folded off profiles (R9) ────────────────────

def test_committed_cards_declare_their_bundles_in_the_union():
    """The bundle wiring lives in the cards, not profiles: the embodiment card
    owns ``sawyer`` (embodiment + driver), the graph card owns ``robot-world``."""
    reg = discover()
    saw = {m.capability: m.provider for m in reg.bundles["sawyer"]}
    assert saw == {"embodiment.env": "plugins.embodiment_robosuite:sawyer_provider",
                   "policy.driver": "plugins.policies:sawyer_scripted_provider"}
    rw = {m.capability: m.provider for m in reg.bundles["robot-world"]}
    assert rw == {"graph.scene": "plugins.graphs:world_scene_graph_provider"}


def test_bundles_do_not_move_the_base_plan_sha():
    """Bundles are alternate overlays, never folded into base_profile -- declaring
    them leaves the sealed base identity untouched."""
    assert resolve_plan(base_profile()).sha() == _SEALED_BASE_SHA


def test_profiles_bundle_builds_and_is_absent_from_the_base_mounts():
    """profiles.bundle(name) reads the manifest overlay; its mounts are NOT in the
    base plan (a bundle is layered, not folded)."""
    from profiles import bundle
    b = bundle("sawyer")
    assert b.name == "sawyer" and len(b.mounts) == 2
    base_refs = {m.provider for m in resolve_plan(base_profile()).mounts}
    assert "plugins.embodiment_robosuite:sawyer_provider" not in base_refs


def test_unknown_bundle_is_loud():
    from profiles import bundle
    with pytest.raises(KeyError, match="no bundle 'nope'"):
        bundle("nope")


def test_duplicate_bundle_across_manifests_is_loud(tmp_path):
    body = '[bundles.dup]\n"graph.scene" = "plugins.graphs:scene_graph_provider"\n'
    _write_manifest(tmp_path, "card_a", body)
    _write_manifest(tmp_path, "card_b", body)
    with pytest.raises(ValueError, match="duplicate bundle 'dup'"):
        discover(tmp_path)
