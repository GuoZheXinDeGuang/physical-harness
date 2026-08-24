"""Round 98: execution-time governance — the mounted SkillRecords assemble into
the bundle whose evidence they earned and steer the REAL run through the same
governed_rollout that measured them.

THE GAP this closes: ``plugins/task/workload.py`` dispatched every node as
``governed_rollout(spec, None)`` — the mounted records were never assembled into
a Bundle, so execution tasks ran ungoverned (zero rule rows in the chain).

Unit (no robosuite): ``assemble_bundle``'s shape / budget / selection / refusal,
and the ``_dispatch`` wiring that threads the bundle and seals the governance
digests. ``rule_from_canonical`` + ``Bundle`` + the embodiment ``stages``
factories all import cleanly with robosuite unplugged, so only the ONE rollout
test needs the card. ``test_real_root_*`` reconstructs the sealed
``runs/session-main/skills`` root into its ``b026831`` chain WITHOUT a rollout.

Integration (@pytest.mark.robosuite): the assembled real bundle, run on a
place-failing place-campaign dev seed, fires its place-shaped ``replace`` rule
and RESCUES the episode; the SAME seed under ``bundle=None`` still fails with
zero rule rows. The fix is the governance, not the seed.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from harness.config import sha_json
from plugins.graphs import InMemorySkillGraph
from plugins.task import workload
from scripts import harness_runtime as runtime

# The sealed live-runtime skills root (not in git). Present on a checkout WITH
# the sealed runs/ evidence; the real-record tests skip on a fresh clone.
SKILLS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "runs" / "session-main" / "skills"


def _rec(feature: str, threshold: float, *, established=True, sensor_sd=0.02,
         task="stack") -> dict:
    """A SkillRecord shaped like plugins.rsi.workload.run publishes: preconditions
    IS the canonical trigger, recovery IS the canonical recovery. Zero-offset
    program so rule_from_canonical inverts it to an explicit RecoverySpec."""
    return {
        "kind": "grasp_recovery",
        "policy": "scripted",
        "task": task,
        "heldout_judgement_established": established,
        "preconditions": {"feature": feature, "op": "gt", "threshold": threshold,
                          "dwell": 1, "arm_after": 10, "reducer": "value"},
        "recovery": {"name": "regrasp", "strategy": "regrasp",
                     "program": [["descend", 10, 0.0, 0.0], ["close", 14, 0.0, 0.0]],
                     "sensor_sd": sensor_sd, "max_invocations": 1},
    }


# --- unit: assembly ----------------------------------------------------------

def test_assemble_shapes_bundle_from_records():
    """Three distinct established stack records -> a 3-rule Bundle, ids g1/g2/g3
    in skills() (content-digest) order, critic_budget = the SUM over distinct
    trigger features (two DIFFERENT privileged features => 2, which a naive max()
    would under-budget), action_budget = the MAX recovery percept privilege (the
    one sd=0.0 record => 1). Returned digests are the sha_json content stems in
    that same order."""
    records = [_rec("observable.finger_gap", 0.001),
               _rec("privileged.stack_xy_residual", 0.03, sensor_sd=0.0),
               _rec("privileged.object_z", 0.82)]
    graph = InMemorySkillGraph()
    for r in records:
        graph.publish(r)
    skills = graph.skills()

    bundle, digests = workload.assemble_bundle(skills, "stack")

    assert [r.rule_id for r in bundle.rules] == ["g1", "g2", "g3"]
    assert {r.trigger.feature for r in bundle.rules} == {
        "observable.finger_gap", "privileged.stack_xy_residual", "privileged.object_z"}
    assert bundle.critic_budget == 2, "critic budget must SUM distinct privileged features, not max"
    assert bundle.action_budget == 1, "action budget is the max recovery percept privilege"
    assert list(digests) == [sha_json(r) for r in skills]
    assert digests == sorted(digests), "digests follow skills()'s content-digest order"
    # Assembly is deterministic: same records -> same chain identity.
    again, _ = workload.assemble_bundle(skills, "stack")
    assert again.sha() == bundle.sha()


def test_zero_match_is_none():
    """No task match, or no records at all, -> (None, []) — the ungoverned path,
    byte-identical to a bare governed_rollout(spec, None)."""
    records = [_rec("observable.finger_gap", 0.001)]
    assert workload.assemble_bundle(records, "lift") == (None, [])
    assert workload.assemble_bundle((), "stack") == (None, [])


def test_unestablished_excluded():
    """Only records whose held-out judgement was ESTABLISHED steer a real run;
    established False (shrank to n.s.) and None (require_judgement off) are
    dropped — they never earned governance authority."""
    records = [_rec("observable.finger_gap", 0.001, established=True),
               _rec("privileged.stack_xy_residual", 0.03, established=False),
               _rec("privileged.object_z", 0.82, established=None)]

    bundle, digests = workload.assemble_bundle(records, "stack")

    assert [r.trigger.feature for r in bundle.rules] == ["observable.finger_gap"]
    assert bundle.critic_budget == 0 and bundle.action_budget == 0
    assert digests == [sha_json(records[0])]


def test_malformed_record_refused():
    """A valid-JSON-but-unassemblable record (missing recovery) raises out of
    assemble_bundle — which is exactly what the boot-time doctor pass surfaces as
    a boot refusal, never a mid-episode failure."""
    bad = _rec("observable.finger_gap", 0.001)
    del bad["recovery"]
    with pytest.raises(KeyError):
        workload.assemble_bundle([bad], "stack")


# --- unit: dispatch wiring + sealing -----------------------------------------

def test_dispatch_threads_bundle_and_seals_governance(monkeypatch):
    """_dispatch assembles the node's task bundle, passes it to _governed_rollout
    (not None), and seals the content digests + bundle_sha + budgets onto the
    result. A node whose task matches no record (grasp -> "lift") runs ungoverned
    with an honest-empty governance block."""
    records = [_rec("observable.finger_gap", 0.001),
               _rec("privileged.stack_xy_residual", 0.03)]
    skills = tuple(records)
    seen = {}

    def fake_rollout(spec, bundle):
        seen[spec.task] = bundle
        return {"success": True, "steps": 1, "stages": []}

    monkeypatch.setattr(workload, "_governed_rollout", fake_rollout)

    stack_node = {"id": "s", "skill": "stack", "args": {}, "after": []}
    res = workload._dispatch(stack_node, seed=1, env_ref="tests.fakes:env",
                             policy_ref="tests.fakes:policy", skills=skills)
    assert seen["stack"] is not None and len(seen["stack"].rules) == 2
    gov = res["governance"]
    assert gov["bundle_sha"] == seen["stack"].sha()
    assert gov["skills"] == [sha_json(r) for r in records]
    assert gov["critic_budget"] == 1 and gov["action_budget"] == 0

    grasp_node = {"id": "g", "skill": "grasp", "args": {"object": "cube"}, "after": []}
    res2 = workload._dispatch(grasp_node, seed=1, env_ref="tests.fakes:env",
                              policy_ref="tests.fakes:policy", skills=skills)
    assert seen["lift"] is None
    assert res2["governance"] == {"skills": [], "bundle_sha": None,
                                  "critic_budget": 0, "action_budget": 0}


# --- boot-time refusal --------------------------------------------------------

def test_boot_refuses_a_malformed_record_at_mount(tmp_path):
    """An execution boot dry-runs assemble_bundle over the skills root; a
    valid-JSON-but-unassemblable record refuses the boot loudly, so it can never
    surface as a mid-episode InvariantViolation."""
    session = tmp_path / "session"
    (session / "skills").mkdir(parents=True)
    bad = _rec("observable.finger_gap", 0.001)
    del bad["recovery"]
    (session / "skills" / "bad.json").write_text(json.dumps(bad))

    with pytest.raises(KeyError):
        runtime.boot(session, mode="execution")


def test_boot_accepts_a_wellformed_record(tmp_path):
    """The same pass admits a record that assembles cleanly — boot succeeds and
    seals it into the manifest."""
    session = tmp_path / "session"
    (session / "skills").mkdir(parents=True)
    (session / "skills" / "good.json").write_text(
        json.dumps(_rec("observable.finger_gap", 0.001)))

    rt = runtime.boot(session, mode="execution")

    assert rt.mode == "execution" and "good" in rt.skills_manifest


# --- real sealed root: identity, no rollout ----------------------------------

def _established_stack_records():
    if not SKILLS_ROOT.is_dir():
        return ()
    recs = InMemorySkillGraph(root=str(SKILLS_ROOT)).skills()
    return tuple(r for r in recs if r.get("task") == "stack"
                 and r.get("heldout_judgement_established") is True)


_HAVE_SEALED = len(_established_stack_records()) >= 3
_NEED_SEALED = pytest.mark.skipif(
    not _HAVE_SEALED, reason="sealed runs/session-main/skills root absent (fresh clone)")


@_NEED_SEALED
def test_real_root_assembles_to_sealed_b026831_chain():
    """The live-runtime skills root (stack-g1 regrasp + place-g2's two replace
    generations) reassembles — through the campaigns' own rule_from_canonical +
    Bundle.append — into exactly the sealed [regrasp, replace, replace] chain
    b026831, critic_budget 1 / action_budget 0. The digests ARE the on-disk
    content-addressed filename stems, so an auditor re-derives the same bundle."""
    recs = InMemorySkillGraph(root=str(SKILLS_ROOT)).skills()

    bundle, digests = workload.assemble_bundle(recs, "stack")

    assert [r.rule_id for r in bundle.rules] == ["g1", "g2", "g3"]
    assert bundle.critic_budget == 1 and bundle.action_budget == 0
    assert bundle.sha() == \
        "b026831c833aa6d8c47ea2270f670074aa7e0ffca126788a7718472c203bc2c9"
    assert set(digests) == {f.stem for f in SKILLS_ROOT.glob("*.json")}


# --- integration: the assembled bundle rescues a real episode -----------------

@pytest.mark.robosuite
@_NEED_SEALED
def test_assembled_real_bundle_rescues_a_place_failure(tmp_path):
    """seed 47236 (a place-campaign dev seed) places the cube wrong: ungoverned it
    FAILS with zero rule rows. Under the bundle assembled from the SAME mounted
    records, the place-shaped `replace` rule (g3, a privileged stack_xy_residual
    trigger) fires and the episode flips to success — the fix is the governance,
    not the seed. The critic runs at budget 1 with no InvariantViolation, so the
    assembled privileged budget is byte-consistent with what the episode attests.

    The spec carries policy_provider = the stack scripted driver the runtime
    mounts for this task (plugins.policies:stack_scripted_provider); a bare spec
    would resolve a different driver and never reproduce the campaign trajectory."""
    for f in SKILLS_ROOT.glob("*.json"):
        shutil.copy2(f, tmp_path / f.name)
    skills = InMemorySkillGraph(root=str(tmp_path)).skills()
    bundle, _digests = workload.assemble_bundle(skills, "stack")

    from harness.spec import EpisodeSpec
    from plugins.embodiment_robosuite.env import stack_stages
    from plugins.rsi.governed import governed_rollout

    def spec():
        return EpisodeSpec(seed=47236, task="stack", stages=stack_stages(),
                           terminal_label=True, percept_noise=0.012,
                           policy_provider="plugins.policies:stack_scripted_provider")

    ungoverned = governed_rollout(spec(), None)
    governed = governed_rollout(spec(), bundle)

    assert ungoverned["success"] is False and ungoverned["fired_rules"] == []
    assert governed["fired_at"] is not None
    assert "g3" in governed["fired_rules"], "the place-shaped replace rule must fire"
    assert governed["success"] is True, "the assembled governance rescues the place failure"
