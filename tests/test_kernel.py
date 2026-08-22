"""Kernel invariants: boundary, contracts, accounting, privilege, config hashing."""

import ast
import dataclasses as dc
import pathlib

import pytest

from harness import (
    Bundle,
    ContractViolation,
    Kernel,
    MissingProvider,
    Mount,
    Patch,
    PrivilegeViolation,
    Profile,
    SessionLog,
    UnknownCapability,
    resolve_plan,
)
from harness.definitions import CAPABILITIES


def _kernel(**kw):
    return Kernel(CAPABILITIES, **kw)


def test_kernel_imports_no_plugins_and_no_governor():
    """Everything-is-a-plugin is enforced, not conventional."""
    for path in pathlib.Path("harness").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(("governor", "plugins")), \
                    f"{path}:{node.lineno} imports {name}"


def test_provider_must_satisfy_the_contract():
    class NotAGraph:
        pass

    k = _kernel()
    with pytest.raises(ContractViolation):
        k.provide("graph.skill", NotAGraph(), ref="tests:fake")


def test_unknown_capability_is_rejected_everywhere():
    k = _kernel()
    with pytest.raises(UnknownCapability):
        k.provide("no.such.thing", object(), ref="x:y")
    with pytest.raises(UnknownCapability):
        k.resolve("no.such.thing", consumer="test")
    with pytest.raises(MissingProvider):
        k.resolve("graph.skill", consumer="test")


def test_every_resolution_is_accounted_and_logged():
    from plugins.graphs import skill_graph_provider

    log = SessionLog()
    k = _kernel(log=log)
    k.provide("graph.skill", skill_graph_provider(), ref="plugins.graphs:skill_graph_provider")
    k.resolve("graph.skill", consumer="rsi")
    k.resolve("graph.skill", consumer="report")
    assert [r.consumer for r in k.resolutions()] == ["rsi", "report"]
    kinds = [row["kind"] for row in log.rows()]
    assert kinds.count("capability.resolve") == 2
    assert log.verify()


def test_privileged_resolution_draws_on_the_budget():
    class Oracle:
        def object_pose(self, obs, spec):
            return (0.0, 0.0, 0.0)

    k = _kernel(privilege_budget=0)
    k.provide("embodiment.ground_truth", Oracle(), ref="tests:oracle")
    with pytest.raises(PrivilegeViolation):
        k.resolve("embodiment.ground_truth", consumer="recovery")

    k2 = _kernel(privilege_budget=1)
    k2.provide("embodiment.ground_truth", Oracle(), ref="tests:oracle")
    k2.resolve("embodiment.ground_truth", consumer="diagnosis")
    k2.resolve("embodiment.ground_truth", consumer="diagnosis")  # same capability, same budget slot
    assert k2.privileged_used() == frozenset({"embodiment.ground_truth"})
    assert all(r.privileged for r in k2.resolutions())


def test_mount_plan_layering_and_provenance():
    profile = Profile("base", (
        Mount("graph.skill", "plugins.graphs:skill_graph_provider"),
        Mount("graph.scene", "plugins.graphs:scene_graph_provider"),
        Mount("exec.rollouts", "harness.executor:provider"),
    ))
    bundle = Bundle("alt-exec", (Mount("exec.rollouts", "harness.executor:provider",
                                       {"tag": "bundled"}),))
    patch = Patch("drop-scene", remove=("graph.scene",),
                  override=(Mount("graph.skill", "plugins.graphs:skill_graph_provider",
                                  {"root": "x"}),))
    plan = resolve_plan(profile, (bundle,), (patch,))
    caps = [m.capability for m in plan.mounts]
    assert caps == sorted(caps) and "graph.scene" not in caps
    by_cap = dict(zip(caps, plan.sources))
    assert by_cap["graph.skill"] == "patch:drop-scene"
    assert by_cap["exec.rollouts"] == "bundle:alt-exec"


def test_a_patch_changes_the_plan_sha():
    profile = Profile("base", (Mount("graph.skill", "plugins.graphs:skill_graph_provider"),))
    a = resolve_plan(profile)
    b = resolve_plan(profile, patches=(Patch("p", override=(
        Mount("graph.skill", "plugins.graphs:skill_graph_provider", {"variant": "y"}),)),))
    assert a.sha() != b.sha()


def test_output_root_does_not_move_the_plan_sha():
    """round 78 tolerated this drift as a path artifact; the output root is now
    excluded from the hash, so the SAME plan written to two different --out
    directories is byte-identical."""
    profile = Profile("base", (Mount("graph.skill", "plugins.graphs:skill_graph_provider"),))
    a = resolve_plan(profile, patches=(Patch("p", override=(
        Mount("graph.skill", "plugins.graphs:skill_graph_provider", {"root": "runs/a/skills"}),)),))
    b = resolve_plan(profile, patches=(Patch("p", override=(
        Mount("graph.skill", "plugins.graphs:skill_graph_provider", {"root": "runs/b/skills"}),)),))
    assert a.sha() == b.sha()


def test_mount_plan_canonical_covers_every_field():
    """Round 34's invariant, applied to the config layer from day one."""
    plan = resolve_plan(Profile("base", (
        Mount("graph.skill", "plugins.graphs:skill_graph_provider"),)))
    assert {f.name for f in dc.fields(plan)} == set(plan.canonical())
    mount = plan.mounts[0]
    assert {f.name for f in dc.fields(mount)} == set(mount.canonical())


def test_mounting_a_real_plan_and_publishing_a_skill():
    plan = resolve_plan(Profile("base", (
        Mount("graph.skill", "plugins.graphs:skill_graph_provider"),
        Mount("exec.rollouts", "harness.executor:provider"),
    )))
    log = SessionLog()
    k = _kernel(log=log)
    k.mount(plan)
    graph = k.resolve("graph.skill", consumer="test")
    digest = graph.publish({"trigger": "observable.finger_gap < 0.002",
                            "effect": {"delta": 0.32}})
    assert graph.skills()[0]["effect"]["delta"] == 0.32
    assert digest == graph.publish({"trigger": "observable.finger_gap < 0.002",
                                    "effect": {"delta": 0.32}}), "content addressing broke"
    assert any(row["kind"] == "kernel.mount" and row["data"]["plan_sha"] == plan.sha()
               for row in log.rows())


def test_session_log_catches_in_place_tampering():
    log = SessionLog()
    for i in range(3):
        log.append("event", {"i": i})
    assert log.verify()
    log._rows[1]["data"]["i"] = 99
    assert not log.verify(), "tampering a logged event went unnoticed"


def test_local_pool_executor_maps():
    from harness.executor import LocalPoolExecutor

    assert LocalPoolExecutor().map(abs, [-1, 2, -3], workers=2) == [1, 2, 3]


def test_a_written_ledger_verifies_offline_and_catches_disk_tampering(tmp_path):
    """Round 59: the chain must protect the FILE, not only the process memory."""
    log = SessionLog(tmp_path)
    for i in range(4):
        log.append("event", {"i": i})
    assert SessionLog.load(tmp_path).verify(), "a clean written ledger failed to verify"

    path = tmp_path / "rows.jsonl"
    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"i": 1', '"i": 9')
    path.write_text("\n".join(lines) + "\n")
    assert not SessionLog.load(tmp_path).verify(), "an on-disk edit went unnoticed"

    # A fresh writer refuses an existing ledger: it would interleave a second
    # chain into the file and nothing could ever verify it again.
    with pytest.raises(FileExistsError):
        SessionLog(tmp_path)


def test_a_reopened_ledger_continues_the_same_chain(tmp_path):
    log = SessionLog(tmp_path)
    log.append("event", {"i": 0})
    first_chain = log.rows()[-1]["chain"]

    loaded = SessionLog.load(tmp_path)
    loaded.append("event", {"i": 1})
    assert loaded.rows()[0]["chain"] == first_chain
    assert loaded.verify(), "continuation broke the chain"
    assert SessionLog.load(tmp_path).verify(), "the on-disk continuation does not verify"
