"""Capability SkillRecords: the store stops a claim it cannot back.

The skill library has to be able to say "this executor can do this skill, under
these preconditions, measured this way" -- the record a dispatcher reads to pick
an executor per segment. It shares the store and the ``publish()`` door with the
RSI recovery records and is discriminated by ``kind``.

The load-bearing claims, in test order:

(a) a valid capability record round-trips publish/skills and survives the
    cross-process reload, exactly like a recovery record;
(b) the three executor kinds a planner may chain -- a scripted in-process
    driver, a learned policy behind a socket, an external package's card -- are
    one ``binding`` shape, and only the one with weights carries a digest;
(c) every validation rule refuses, loudly, at publish time (nothing malformed is
    storable);
(d) records without ``kind`` -- and records with some OTHER kind -- are
    untouched, digests unmoved;
(e) ``skill_index`` is the planner's one-read view, DERIVED from ``skills()``
    (never stored, never hand-maintained) and dropped at boot from the same
    records the boot seal counted.
"""

from __future__ import annotations

import json
import re

import pytest

from harness.config import sha_json
from harness.skill_record import SkillRecordError, skill_index, validate_capability
from plugins.graphs import InMemorySkillGraph

_GRASPED = "plugins.embodiment_robocasa.predicates:obj_grasped"
_INSIDE = "plugins.embodiment_robocasa.predicates:obj_in_microwave"
_SHA = "a" * 64


def _record(**over) -> dict:
    """A π0.5 `place` capability record: grasped in, inside-the-microwave out."""
    rec = {
        "kind": "capability",
        "skill": "place",
        "task": "kitchen_thaw",
        "binding": {"ref": "plugins.policy_vla_remote:provider",
                    "checkpoint_sha": _SHA},
        "preconditions": [_GRASPED],
        "effects": [_INSIDE],
        "measured": {"predicate": _INSIDE, "successes": 12, "n": 20,
                     "seeds": [42000, 42001], "split": "test"},
        "mount_plan_sha": "b" * 64,
    }
    rec.update(over)
    return rec


def _recovery() -> dict:
    """A real promoted RSI record (runs/inventory-build-gov2 shape, trimmed).

    It violates the capability rules on purpose -- ``preconditions`` is a trigger
    OBJECT, ``effects`` is a gate object, half the keys are unknown to the
    capability schema -- so storing it proves the gate is scoped by ``kind``.
    """
    return {
        "kind": "grasp_recovery", "policy": "scripted", "task": "lift",
        "generation": 1,
        "preconditions": {"feature": "observable.finger_gap", "op": "lt",
                          "threshold": 0.009605, "dwell": 1, "arm_after": 97,
                          "reducer": "value"},
        "recovery": {"name": "regrasp", "strategy": "regrasp",
                     "program": [["descend", 10, 0.0, 0.0]],
                     "sensor_sd": 0.02, "max_invocations": 1},
        "effects": {"dev_gate_vs_parent": {"n": 140, "fixed": 15}},
        "heldout_judgement_established": True,
        "mount_plan_sha": "d" * 64,
    }


# --- (a) round trip ----------------------------------------------------------

def test_a_valid_capability_record_round_trips(tmp_path):
    graph = InMemorySkillGraph(root=str(tmp_path))
    rec = _record()
    digest = graph.publish(rec)

    assert graph.skills() == (rec,)
    assert digest == sha_json(rec), "capability records are content-addressed like every other"
    # the cross-process half: a later reader mounts the same root
    assert InMemorySkillGraph(root=str(tmp_path)).skills() == (rec,)


def test_the_optional_evidence_keys_are_permitted_but_not_required():
    graph = InMemorySkillGraph()
    bare = _record(measured={"predicate": _INSIDE, "successes": 0, "n": 20})
    del bare["mount_plan_sha"]
    graph.publish(bare)  # 0/20 is an honest null, and it must be publishable
    assert graph.skills()[0]["measured"]["successes"] == 0


# --- (b) three executor kinds, one binding shape -----------------------------

def test_scripted_learned_and_external_executors_share_one_binding():
    graph = InMemorySkillGraph()
    bindings = [
        {"ref": "plugins.mission_kitchen_thaw.planner:provider"},   # scripted driver
        {"ref": "plugins.policy_vla_remote:provider",               # learned, over a socket
         "checkpoint_sha": _SHA},
        {"ref": "plugins.embodiment_robocasa.drivers:provider"},    # external package's card
    ]
    for i, binding in enumerate(bindings):
        graph.publish(_record(binding=binding, skill=f"place{i}"))
    assert len(graph.skills()) == 3, "no transport abstraction: the card boundary is it"


# --- (c) every rule refuses ---------------------------------------------------

@pytest.mark.parametrize("over, why", [
    # 1. well-formed "module:attr" refs, everywhere a predicate is named
    ({"preconditions": ["the gripper is holding the meat"]}, "preconditions[0]"),
    ({"preconditions": ["plugins.x.predicates"]}, "preconditions[0]"),
    ({"effects": [":obj_in_microwave"]}, "effects[0]"),
    ({"effects": [_INSIDE, 7]}, "effects[1]"),
    ({"measured": {"predicate": "obj_in_microwave", "successes": 1, "n": 2}},
     "measured.predicate"),
    # 2. measured.predicate in effects -- claiming one thing, measuring another
    ({"measured": {"predicate": _GRASPED, "successes": 12, "n": 20}},
     "is not one of effects"),
    # 3. 0 <= successes <= n, n > 0
    ({"measured": {"predicate": _INSIDE, "successes": 21, "n": 20}}, "0..20"),
    ({"measured": {"predicate": _INSIDE, "successes": -1, "n": 20}}, "0..20"),
    ({"measured": {"predicate": _INSIDE, "successes": 0, "n": 0}}, "must be > 0"),
    ({"measured": {"predicate": _INSIDE, "successes": 1, "n": 2.5}}, "must be an int"),
    ({"measured": {"predicate": _INSIDE, "successes": 1}}, "measured.n is required"),
    # 4. split is one of the declared values
    ({"measured": {"predicate": _INSIDE, "successes": 1, "n": 2, "split": "dev"}},
     "measured.split"),
    ({"measured": {"predicate": _INSIDE, "successes": 1, "n": 2, "seeds": "42000"}},
     "measured.seeds"),
    # 5. checkpoint_sha is a 64-char LOWERCASE hex digest when present
    ({"binding": {"ref": "plugins.p:provider", "checkpoint_sha": "A" * 64}},
     "binding.checkpoint_sha"),
    ({"binding": {"ref": "plugins.p:provider", "checkpoint_sha": "abc"}},
     "binding.checkpoint_sha"),
    ({"mount_plan_sha": "not-a-digest"}, "mount_plan_sha"),
    # 6. unknown top-level keys -- a typo'd field silently drops evidence
    ({"measurd": {}}, "unknown ['measurd']"),
    ({"checkpoint_sha": _SHA}, "unknown ['checkpoint_sha']"),
    # binding is who executes; a planner may not hand one over as prose
    ({"binding": {"checkpoint_sha": _SHA}}, "binding.ref"),
    ({"binding": "plugins.policy_vla_remote:provider"}, "binding must be an object"),
    # empty preconditions is an implicit universal claim, not "no entry condition"
    ({"preconditions": []}, "empty"),
    ({"effects": []}, "empty"),
    ({"skill": ""}, "skill must be a non-empty string"),
])
def test_a_malformed_capability_record_is_not_storable(over, why):
    graph = InMemorySkillGraph()
    with pytest.raises(SkillRecordError, match=re.escape(why)):
        graph.publish(_record(**over))
    assert graph.skills() == (), "a refused record must not land in the store"


def test_a_missing_required_key_is_named():
    rec = _record()
    del rec["effects"]
    with pytest.raises(SkillRecordError, match=r"missing \['effects'\]"):
        validate_capability(rec)


# --- (d) every other kind passes through untouched ----------------------------

def test_recovery_and_other_kinds_pass_through_unchanged(tmp_path):
    graph = InMemorySkillGraph(root=str(tmp_path))
    recovery = _recovery()
    probe = {"kind": "doctor_probe", "value": 1}          # plugin_doctor's smoke record
    legacy = {"trigger": "observable.finger_gap < 0.002",  # no kind at all
              "effect": {"delta": 0.32}}

    digests = [graph.publish(r) for r in (recovery, probe, legacy)]

    assert digests == [sha_json(r) for r in (recovery, probe, legacy)], \
        "the capability gate moved a digest it must never touch"
    assert sorted(graph.skills(), key=repr) == sorted(
        [recovery, probe, legacy], key=repr)


# --- (e) the derived index ----------------------------------------------------

_AT_FRIDGE = "plugins.mission_kitchen_thaw.planner:v_at_fridge"
_CLOSED = "plugins.embodiment_robocasa.predicates:microwave_closed"


def _chain() -> list[dict]:
    """grasp -> place -> close: each one's effect is the next one's precondition."""
    def leg(skill, pre, eff, successes, n):
        return _record(skill=skill, preconditions=[pre], effects=[eff],
                       measured={"predicate": eff, "successes": successes, "n": n})
    return [leg("grasp", _AT_FRIDGE, _GRASPED, 49, 100),
            leg("place", _GRASPED, _INSIDE, 0, 22),
            leg("close", _INSIDE, _CLOSED, 150, 150)]


def test_the_index_is_one_compact_document_derived_from_the_store():
    graph = InMemorySkillGraph()
    digests = [graph.publish(r) for r in _chain()]
    graph.publish(_recovery())  # not a thing a planner selects

    index = skill_index(graph.skills())

    assert sorted(index["skills"]) == ["close", "grasp", "place"], \
        "an RSI recovery record must not appear in the planner's catalogue view"
    row = index["skills"]["place"][0]
    assert row["digest"] in digests and row["measured"] == {"successes": 0, "n": 22}
    assert set(row) == {"digest", "binding", "preconditions", "effects", "measured"}, \
        "the index lands in a context window: no evidence blobs"


def test_edges_are_set_containment_over_the_fields_the_records_already_carry():
    index = skill_index(_chain())
    edges = {(e["from"], e["to"]): e["via"] for e in index["edges"]}

    assert edges == {("grasp", "place"): [_GRASPED], ("place", "close"): [_INSIDE]}, \
        "an edge is exactly: B's preconditions are a subset of A's effects"


def test_an_empty_library_indexes_to_an_honest_empty_document():
    assert skill_index(()) == {"skills": {}, "edges": []}


def test_boot_drops_an_index_derived_from_the_records_it_mounted(tmp_path):
    from scripts import harness_runtime as runtime

    session = tmp_path / "session-main"
    skills = session / "skills"
    skills.mkdir(parents=True)
    for rec in _chain():
        (skills / f"{sha_json(rec)}.json").write_text(json.dumps(rec))

    rt = runtime.main(session, drain=True)  # execution mode; also dry-runs assembly

    dropped = json.loads((session / "skill_index.json").read_text())
    assert dropped == skill_index(InMemorySkillGraph(root=str(rt.skills_root)).skills())
    assert sorted(dropped["skills"]) == ["close", "grasp", "place"]
    assert len(rt.skills_manifest) == 3, "same records the boot seal counted"
