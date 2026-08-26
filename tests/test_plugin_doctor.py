"""R6: plugin_doctor 体检 -- Tier A shape + Tier B per-kind fake-episode smoke.

The acceptance IS this test: the doctor GREENS the fakes card (every base seam,
run on ``harness.fakes``) and the robosuite card (the real-sim tier, gated by
``needs_sim``), and REDDENS a contract-violating provider (Tier A isinstance) and
a non-deterministic percept (Tier B determinism-required). The two red fixtures
are built tiny and inline here -- a manifest pointing a percept mount at a
wrong-shaped provider, and one at the deliberately flaky provider below.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from scripts.plugin_doctor import check, verify, verify_claim

_REPO = Path(__file__).resolve().parent.parent

#: The fakes card: every non-privileged base seam mounted to its null provider.
#: Not a committed plugin dir (it would collide with the real cards in
#: ``discover``) -- it is exactly the doctor's own fixture set, declared here.
_FAKES = """\
[mounts."embodiment.env"]
ref = "harness.fakes:env_provider"
[mounts."policy.driver"]
ref = "harness.fakes:policy_provider"
[mounts."percept.model"]
ref = "harness.fakes:percept_provider"
[mounts."reasoner.proposer"]
ref = "harness.fakes:reasoner_provider"
[mounts."task.planner"]
ref = "harness.fakes:task_planner_provider"
[mounts."graph.skill"]
ref = "harness.fakes:skill_graph_provider"
[mounts."graph.scene"]
ref = "harness.fakes:scene_graph_provider"
"""

_SEAMS = {"embodiment.env", "policy.driver", "percept.model", "reasoner.proposer",
          "task.planner", "graph.skill", "graph.scene"}


class _NonDetPercept:
    """Right shape (has ``object_estimate``), wrong discipline: a fresh draw every
    call, so the determinism-required smoke sees two different outputs."""

    def object_estimate(self, obs, spec, sensor_sd, draw):
        return [random.random(), random.random(), 0.0]


def nondet_percept_provider():  # referenced by ref from the red fixture manifest
    return _NonDetPercept()


class _NonDetPlanner:
    """A model-backed planner: right shape, fresh node id every call. With the
    explicit ``deterministic = False`` marker the doctor validates shape and
    never diffs; without it the determinism-required policy reddens it."""

    deterministic = False

    def plan(self, brief):
        return {"goal": "g", "nodes": [{"id": f"n-{random.random()}",
                                        "skill": "s", "args": {}, "after": []}],
                "verify": []}


class _NonDetPlannerUnmarked(_NonDetPlanner):
    deterministic = True  # claims determinism it does not have -> diffed -> red


def nondet_planner_provider():
    return _NonDetPlanner()


def nondet_planner_unmarked_provider():
    return _NonDetPlannerUnmarked()


def _card(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.toml").write_text(body)
    return d


def _fails(rep) -> list[tuple[str, str, str]]:
    return [(r.tier, r.name, r.detail) for r in rep.results if r.status == "FAIL"]


def test_doctor_greens_the_fakes_card(tmp_path):
    rep = check(_card(tmp_path, "fakes", _FAKES))
    assert rep.green, _fails(rep)
    # every seam cleared Tier A (shape) AND ran a Tier B smoke that passed.
    passed_b = {r.name for r in rep.results if r.tier == "B" and r.status == "PASS"}
    assert passed_b == _SEAMS, passed_b


def test_doctor_reddens_a_contract_violating_provider(tmp_path):
    # a scene-graph provider mounted as the percept seam: right nothing, wrong shape.
    body = '[mounts."percept.model"]\nref = "harness.fakes:scene_graph_provider"\n'
    rep = check(_card(tmp_path, "bad_shape", body))
    assert not rep.green
    a = [r for r in rep.results if r.tier == "A" and r.name == "percept.model"]
    assert a and a[0].status == "FAIL" and "PerceptModel" in a[0].detail, _fails(rep)


def test_doctor_reddens_a_non_deterministic_percept(tmp_path):
    body = ('[mounts."percept.model"]\n'
            'ref = "tests.test_plugin_doctor:nondet_percept_provider"\n')
    rep = check(_card(tmp_path, "flaky", body))
    assert not rep.green
    # it CLEARS Tier A (correct shape) -- the redness is determinism, Tier B.
    a = [r for r in rep.results if r.tier == "A" and r.name == "percept.model"]
    assert a and a[0].status == "PASS"
    b = [r for r in rep.results if r.tier == "B" and r.name == "percept.model"]
    assert b and b[0].status == "FAIL" and "non-deterministic" in b[0].detail


def test_doctor_exempts_a_planner_that_declares_nondeterminism(tmp_path):
    body = ('[mounts."task.planner"]\n'
            'ref = "tests.test_plugin_doctor:nondet_planner_provider"\n')
    rep = check(_card(tmp_path, "vlm_planner", body))
    assert rep.green, _fails(rep)
    b = [r for r in rep.results if r.tier == "B" and r.name == "task.planner"]
    assert b and b[0].status == "PASS" and "not diffed" in b[0].detail


def test_doctor_reddens_a_nondeterministic_planner_without_the_marker(tmp_path):
    body = ('[mounts."task.planner"]\n'
            'ref = "tests.test_plugin_doctor:nondet_planner_unmarked_provider"\n')
    rep = check(_card(tmp_path, "flaky_planner", body))
    assert not rep.green
    b = [r for r in rep.results if r.tier == "B" and r.name == "task.planner"]
    assert b and b[0].status == "FAIL" and "non-deterministic" in b[0].detail


def test_doctor_refuses_actuation_real(tmp_path):
    body = ('actuation = "real"\n[mounts."embodiment.env"]\n'
            'ref = "harness.fakes:env_provider"\n')
    rep = check(_card(tmp_path, "real_arm", body))
    assert not rep.green
    assert any(r.name == "actuation" and r.status == "FAIL" for r in rep.results)


def test_doctor_reddens_a_task_binding_with_dead_refs(tmp_path):
    # GOAL v4.2: 不合格在 mount 报错而非任务中失败 -- a binding whose planner
    # cannot even import must redden at 体检, not at brief dispatch (the hole
    # the round-96 charter verifier caught: this card used to pass GREEN).
    body = ('[task_bindings.bogus]\npolicy = "does.not.exist:nope"\n'
            'planner = "does.not.exist:nope"\n'
            'catalogue = "does.not.exist:C"\noracles = "does.not.exist:O"\n')
    rep = check(_card(tmp_path, "task_dead", body))
    assert not rep.green
    assert any(r.name == "task:bogus" and r.status == "FAIL" for r in rep.results)


def test_doctor_greens_a_task_binding_with_live_refs(tmp_path):
    # the committed reference card's own refs, resolved exactly as the runtime
    # would at dispatch: policy+planner through Kernel.provide, catalogue/oracles
    # as importable attributes.
    body = ('[task_bindings.toy]\n'
            'policy = "plugins.policies:stack_scripted_provider"\n'
            'planner = "plugins.skill_toy.planner:provider"\n'
            'catalogue = "plugins.skill_toy.planner:CATALOGUE"\n'
            'oracles = "plugins.skill_toy.planner:ORACLES"\n')
    rep = check(_card(tmp_path, "task_live", body))
    assert rep.green
    assert any(r.name == "task:toy" and r.status == "PASS" for r in rep.results)


# ── the new binding kind: a heterogeneous mission's PREDICATES table ──────────
# (m6-mission-design §2b) A perceive/decide/verify node names a predicate the
# card's PREDICATES table maps to a "module:factory" ref. A dead predicate ref is
# a malformed declaration that must redden at 体检, not mid-brief.

_PRED_BINDING = ('[task_bindings.het]\n'
                 'policy = "plugins.policies:stack_scripted_provider"\n'
                 'planner = "plugins.task.planner_stack:provider"\n'
                 'catalogue = "plugins.task.planner_stack:CATALOGUE"\n'
                 'oracles = "plugins.task.planner_stack:ORACLES"\n'
                 'predicates = "{ref}"\n')


def test_doctor_greens_a_binding_with_a_live_predicate_table(tmp_path):
    body = _PRED_BINDING.format(ref="tests.test_task_seam:HET_PREDICATES")
    rep = check(_card(tmp_path, "het_live", body))
    assert rep.green, _fails(rep)
    assert any(r.name == "task:het" and r.status == "PASS" for r in rep.results)


def test_doctor_reddens_a_binding_with_a_dead_predicate_ref(tmp_path):
    body = _PRED_BINDING.format(ref="tests.test_task_seam:HET_PREDICATES_DEAD")
    rep = check(_card(tmp_path, "het_dead", body))
    assert not rep.green
    assert any(r.name == "task:het" and r.status == "FAIL" for r in rep.results)


# ── 验货 (--verify): the acceptance reader over a published skill store ───────

def _skill_record(**over) -> dict:
    """A SkillRecord the shape ``plugins.rsi.workload.run`` publishes -- only the
    four fields ``verify`` reads matter here (fakes)."""
    rec = {"kind": "grasp_recovery", "prereg_sha": "deadbeef",
           "heldout_judgement_established": True,
           "bundle_evidence": {"ablation": [[0.0, {}], [0.02, {}]]}}
    rec.update(over)
    return rec


def _store(tmp_path: Path, *records: dict) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    for i, rec in enumerate(records):
        (d / f"{i}.json").write_text(json.dumps(rec))
    return d


def test_verify_greens_a_store_with_an_established_skill(tmp_path):
    rep = verify(_store(tmp_path, _skill_record()))
    assert rep.green, _fails(rep)


def test_verify_reddens_a_store_without_promotion(tmp_path):
    rep = verify(_store(tmp_path))  # empty: a campaign that promoted nothing
    assert not rep.green
    assert any(r.name == "promoted skill" and r.status == "FAIL" for r in rep.results)


def test_verify_reddens_unestablished_judgement(tmp_path):
    rep = verify(_store(tmp_path, _skill_record(heldout_judgement_established=False)))
    assert not rep.green
    assert any(r.name == "heldout judgement" and r.status == "FAIL" for r in rep.results)


def test_verify_reddens_missing_ablation(tmp_path):
    rep = verify(_store(tmp_path, _skill_record(bundle_evidence={})))
    assert not rep.green
    assert any(r.name == "ablation" and r.status == "FAIL" for r in rep.results)


def test_verify_reddens_missing_prereg_sha(tmp_path):
    rep = verify(_store(tmp_path, _skill_record(prereg_sha=None)))
    assert not rep.green
    assert any(r.name == "prereg_sha" and r.status == "FAIL" for r in rep.results)


# ── --verify-claim: a skill card's sealed claim vs the store it names (R9) ────

def test_verify_claim_greens_the_committed_stack_card():
    """The stack skill card (plugins/task) claims runs/stack-g1: --verify-claim
    reads the [claim.sealed] table and greens it against the sealed store."""
    if not (_REPO / "runs" / "stack-g1").is_dir():
        pytest.skip("runs/stack-g1 sealed store not present in this checkout")
    rep = verify_claim(_REPO / "plugins" / "task")
    assert rep.green, _fails(rep)


def test_verify_claim_greens_the_committed_place_card():
    if not (_REPO / "runs" / "place-g2").is_dir():
        pytest.skip("runs/place-g2 sealed store not present in this checkout")
    rep = verify_claim(_REPO / "plugins" / "skill_place")
    assert rep.green, _fails(rep)
    # both sealed place digests are pinned and the rescore blocks are present.
    names = {r.name for r in rep.results if r.status == "PASS"}
    assert "sealed digests" in names
    assert any(n.startswith("rescore runs/place-g2-rescore") for n in names)


def test_verify_claim_reddens_a_card_without_a_sealed_table(tmp_path):
    card = _card(tmp_path, "no_sealed", '[claim]\ntask = "stack"\n')
    rep = verify_claim(card)
    assert not rep.green
    assert any(r.name == "claim.sealed" and r.status == "FAIL" for r in rep.results)


def test_verify_claim_reddens_a_wrong_digest_pin(tmp_path):
    """A claim pinning a digest the store does not hold breaks set-equality -- the
    content commitment catches a claim drifting off its evidence."""
    card = _card(tmp_path, "wrong_pin",
                 '[claim.sealed]\n'
                 'store = "runs/stack-g1"\n'
                 'skills = ["deadbeef"]\n'
                 'heldout_judgement_established = true\n')
    rep = verify_claim(card)
    assert not rep.green
    assert any(r.name == "sealed digests" and r.status == "FAIL" for r in rep.results)


@pytest.mark.robosuite
def test_doctor_greens_the_robosuite_card():
    rep = check(_REPO / "plugins" / "embodiment_robosuite")
    assert rep.green, _fails(rep)
    got = {(r.tier, r.name, r.status) for r in rep.results}
    # Tier A shape on both declared seams, Tier B real-sim smoke on both, and the
    # percept smoke passed the determinism-required policy.
    assert ("A", "embodiment.env", "PASS") in got
    assert ("A", "percept.model", "PASS") in got
    assert ("B", "embodiment.env", "PASS") in got
    assert ("B", "percept.model", "PASS") in got


@pytest.mark.robocasa
def test_doctor_greens_the_robocasa_card():
    # The SECOND simulator behind the same two seams, checked in the robocasa venv
    # (its third_party -- robocasa/robosuite/mujoco -- importable there). The card
    # is enabled=false, but check() reads the manifest directly (card_mounts),
    # ignoring enabled, so the doctor greens it regardless of the base fold.
    rep = check(_REPO / "plugins" / "embodiment_robocasa")
    assert rep.green, _fails(rep)
    got = {(r.tier, r.name, r.status) for r in rep.results}
    assert ("A", "embodiment.env", "PASS") in got
    assert ("A", "percept.model", "PASS") in got
    assert ("B", "embodiment.env", "PASS") in got
    assert ("B", "percept.model", "PASS") in got
