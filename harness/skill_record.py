"""The capability SkillRecord schema: what a record must say before a store takes it.

A recovery record (``plugins/rsi/workload.py``) says "this RSI rule was
promoted". A **capability** record says the other thing the skill library owes a
planner: *this executor can do this skill, under these preconditions, measured
this way.* Both live in the same store behind the same ``publish()`` door and are
told apart by ``kind`` -- a record without one is a recovery record and is not
touched here.

The shape, minimal on purpose::

    {"kind": "capability",
     "skill": "place",                      # the CATALOGUE name a planner may select
     "task": "kitchen_thaw",                # mission context
     "binding": {"ref": "plugins.policy_vla_remote:provider",
                 "checkpoint_sha": "<64 hex>"},          # who executes
     "preconditions": ["plugins.embodiment_robocasa.predicates:obj_grasped"],
     "effects":       ["plugins.embodiment_robocasa.predicates:obj_in_microwave"],
     "measured": {"predicate": "plugins.embodiment_robocasa.predicates:obj_in_microwave",
                  "successes": 12, "n": 20}}

``preconditions``/``effects`` are predicate refs in the SAME ``"module:factory"``
form the mission cards' verify tables use (``plugins/embodiment_robocasa/predicates.py``
``PREDICATES``), resolved through ``harness.registry.load_provider`` to
``pred(env) -> bool``. Preconditions and verifies are one kind of thing: one
checks entry, one checks exit. Prose is not admissible, because the whole point
is that a dispatcher EVALUATES these against live state and skips a skill whose
preconditions do not hold. Composition is therefore predicate-level and live:
chain A into B when B's preconditions hold on the state A actually left.

**Known limitation, deliberately not a field.** Matching predicate names do not
guarantee B's measured rate transfers: A may hand over states outside the
distribution B was measured on, and both predicates still read True. A handover
measurement is running to find out whether that bites in practice; the field to
carry it gets added when there is evidence it is needed, not before.

Refs are checked for SHAPE, never resolved. Resolving would import an arbitrary
plugin (torch/JAX behind a policy card) inside a store that must stay light, and
``tests/test_boundaries.py`` forbids the sibling-plugin import outright. A dead
ref reddens at ``scripts/plugin_doctor.py`` Tier A, which is where refs are
resolved for a living.

Why ``harness/`` and not next to the one store that enforces it: plugins never
import each other, so a schema inside ``plugins/graphs`` would be unreachable to
the rsi workload, to any mission card that publishes, and to a second
SkillLibrary implementation -- each would re-author the ref regex and drift.
Stdlib-only, so ``plugins/graphs`` stays importable in a real robot's minimal venv.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from harness.config import sha_json
from harness.protocol import graph_sha, plan_lower_bound, validate_graph
from harness.skill_executor import TRANSPORTS

#: The ``kind`` discriminator. Any other value (including no ``kind`` at all) is
#: some other record shape and passes through untouched.
CAPABILITY = "capability"
#: A promoted whole-task graph (``harness.protocol.PlanRecord``): same store,
#: same door, told apart by kind.
PLAN = "plan"
PLAN_REQUIRED = frozenset({"kind", "id", "task", "goal", "graph", "embodiment", "arm",
                           "evidence", "rule", "published_from"})

#: Exactly the keys a capability record may carry. Unknown keys are REJECTED:
#: a typo'd field name is how evidence gets silently dropped and a record ends
#: up claiming more than it measured.
REQUIRED = frozenset({"kind", "skill", "task", "binding", "preconditions",
                      "effects", "measured"})
#: Permitted but not required. ``mount_plan_sha`` is the base the measurement ran
#: against, the same field the recovery records already carry.
OPTIONAL = frozenset({"mount_plan_sha"})

#: Scene splits a rate may be attributed to. RoboCasa: ``train`` = layouts 11-60,
#: ``test`` = layouts 1-10. A rate without one does not say whether it is
#: capability or generalisation, so an unrecognised value is refused rather than
#: filed as a free-text note.
SPLITS = ("train", "test")

_REF = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class SkillRecordError(ValueError):
    """A record that would have claimed more than it measured. Never caught to continue."""


def _ref(value, where: str) -> None:
    if not isinstance(value, str) or not _REF.match(value):
        raise SkillRecordError(
            f"{where} must be a 'module:factory' predicate ref (the form "
            f"load_provider resolves), got {value!r}")


def _sha(value, where: str) -> None:
    if not isinstance(value, str) or not _SHA.match(value):
        raise SkillRecordError(
            f"{where} must be a 64-char lowercase hex digest, got {value!r}")


def _int(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SkillRecordError(f"{where} must be an int, got {value!r}")
    return value


def validate_plan(record: Mapping, records: Mapping | None = None,
                  facts=(), objects=()) -> None:
    """Raise ``SkillRecordError`` unless ``record`` is a well-formed plan record:
    ``id`` IS ``graph_sha(graph)``, the evidence is a real k-of-n, and ``rule``
    is the bound the record actually clears. With ``records`` (SkillRecordV0 by
    id/name) the graph is also run through ``validate_graph`` against
    ``facts``/``objects`` -- the goal is non-empty here, so Covered bites."""
    missing, unknown = sorted(PLAN_REQUIRED - set(record)), sorted(set(record) - PLAN_REQUIRED)
    if missing or unknown:
        raise SkillRecordError(f"plan record: missing {missing}, unknown {unknown}")
    for key in ("task", "embodiment", "arm"):
        if not isinstance(record[key], str) or not record[key]:
            raise SkillRecordError(f"{key} must be a non-empty string, got {record[key]!r}")
    graph = record["graph"]
    if not isinstance(graph, Mapping) or not isinstance(graph.get("nodes"), list) or not graph["nodes"]:
        raise SkillRecordError("graph must be a planner-format dict with non-empty nodes")
    if record["id"] != graph_sha(graph):
        raise SkillRecordError(f"id {record['id']!r} is not graph_sha(graph) {graph_sha(graph)!r}")
    ev, rule = record["evidence"], record["rule"]
    if not isinstance(ev, Mapping) or not isinstance(rule, Mapping):
        raise SkillRecordError("evidence and rule must be objects")
    n, k = _int(ev.get("n"), "evidence.n"), _int(ev.get("k"), "evidence.k")
    if n <= 0 or not 0 <= k <= n:
        raise SkillRecordError(f"evidence must be 0 <= k <= n with n > 0, got {k}/{n}")
    for key in ("theta", "n_min", "lower"):
        if key not in rule or isinstance(rule[key], bool) or not isinstance(rule[key], (int, float)):
            raise SkillRecordError(f"rule.{key} must be a number, got {rule.get(key)!r}")
    lower = plan_lower_bound(n, k)
    if rule["lower"] != lower or lower < rule["theta"] or n < rule["n_min"]:
        raise SkillRecordError(
            f"rule {dict(rule)} is not the bound {k}/{n} clears (lower={lower})")
    if records is not None:
        from harness.protocol import PlanRecord   # local: keeps the module light
        ok, problems = validate_graph(PlanRecord.from_dict(record).execution_graph(),
                                      records, facts, objects)
        if not ok:
            raise SkillRecordError(f"plan graph is not Legal(G): {problems}")


def validate_capability(record: Mapping, records: Mapping | None = None,
                        facts=(), objects=()) -> None:
    """Raise ``SkillRecordError`` unless ``record`` is a well-formed capability
    record (or, by ``kind``, a well-formed plan record -- see ``validate_plan``,
    which is where the optional ``records``/``facts``/``objects`` go). Called
    by the store at publish time -- a malformed record is not storable, and
    the refusal is loud rather than a warning plus a bad row."""
    if record.get("kind") == PLAN:
        return validate_plan(record, records, facts, objects)
    missing = sorted(REQUIRED - set(record))
    unknown = sorted(set(record) - REQUIRED - OPTIONAL)
    if missing or unknown:
        raise SkillRecordError(
            f"capability record: missing {missing}, unknown {unknown}; "
            f"required {sorted(REQUIRED)}, optional {sorted(OPTIONAL)}")

    for key in ("skill", "task"):
        if not isinstance(record[key], str) or not record[key]:
            raise SkillRecordError(f"{key} must be a non-empty string, got {record[key]!r}")

    binding = record["binding"]
    if not isinstance(binding, Mapping):
        raise SkillRecordError(f"binding must be an object, got {binding!r}")
    _ref(binding.get("ref"), "binding.ref")
    # Present-and-shaped, never required: an executor with no weights (a scripted
    # driver, an external package's own planner) has no digest to give, and
    # forcing one would manufacture the exact fiction this schema blocks. The
    # identity GATE lives where the weights are -- policy_vla_remote.reconcile
    # refuses to mount when a declared digest is not echoed by the server.
    if "checkpoint_sha" in binding:
        _sha(binding["checkpoint_sha"], "binding.checkpoint_sha")
    if binding.get("transport", "inproc") not in TRANSPORTS:
        raise SkillRecordError(
            f"binding.transport must be one of {TRANSPORTS}, got {binding.get('transport')!r}")

    for key in ("preconditions", "effects"):
        refs = record[key]
        # Empty is not "no entry condition", it is the maximally permissive claim
        # ("applies always"), and it is indistinguishable from a field nobody
        # filled in. A skill that really applies unconditionally names a
        # predicate saying so, which an auditor can grep for.
        if not isinstance(refs, list) or not refs:
            raise SkillRecordError(
                f"{key} must be a non-empty list of predicate refs; an empty "
                f"list is an implicit universal claim, so say it with a predicate")
        for i, ref in enumerate(refs):
            _ref(ref, f"{key}[{i}]")

    measured = record["measured"]
    if not isinstance(measured, Mapping):
        raise SkillRecordError(f"measured must be an object, got {measured!r}")
    for key in ("predicate", "successes", "n"):
        if key not in measured:
            raise SkillRecordError(f"measured.{key} is required: {sorted(measured)}")
    _ref(measured["predicate"], "measured.predicate")
    if measured["predicate"] not in record["effects"]:
        raise SkillRecordError(
            f"measured.predicate {measured['predicate']!r} is not one of effects "
            f"{record['effects']}: a record measured on one predicate while "
            f"claiming another is exactly the silent lie this schema blocks")
    n = _int(measured["n"], "measured.n")
    successes = _int(measured["successes"], "measured.successes")
    if n <= 0:
        raise SkillRecordError(f"measured.n must be > 0, got {n}")
    if not 0 <= successes <= n:
        raise SkillRecordError(f"measured.successes must be 0..{n}, got {successes}")

    seeds = measured.get("seeds")
    if seeds is not None and (not isinstance(seeds, list)
                              or not all(isinstance(s, int) and not isinstance(s, bool)
                                         for s in seeds)):
        raise SkillRecordError(f"measured.seeds must be a list of ints, got {seeds!r}")
    split = measured.get("split")
    if split is not None and split not in SPLITS:
        raise SkillRecordError(
            f"measured.split must be one of {list(SPLITS)}, got {split!r}")

    if "mount_plan_sha" in record:
        _sha(record["mount_plan_sha"], "mount_plan_sha")


def skill_index(records: Sequence[Mapping]) -> dict:
    """The planner's ONE-read view of the library, DERIVED from ``skills()``.

    A VLM planner reads the whole library once as context; ``skills()`` hands
    back N digest-addressed records and reassembling the picture N times is how
    two readers end up with two pictures. This is that picture, and it is
    computed on the spot every time -- never stored as a second copy of the
    truth, because a stored index drifts from the records and nothing notices.
    Compact on purpose: it lands in a context window, so no evidence blobs, no
    seed lists, no ablation tables.

    ``skills`` maps the CATALOGUE name a planner may select to its rows;
    ``edges`` is pure set containment over fields the records already carry --
    A -> B exists when B's ``preconditions`` are a subset of A's ``effects``, and
    ``via`` names the refs that make it hold. No inference, no model call, no
    heuristics. Edges are over skill NAMES, so a name with several records gets
    an edge when ANY of its records supports it; the composition is evaluated
    live at dispatch anyway (see this module's known limitation), and this is a
    reading aid, not a licence.

    Records of any other ``kind`` are ignored -- an RSI recovery record is not a
    thing a planner selects.
    """
    caps = [r for r in records if r.get("kind") == CAPABILITY]
    skills: dict[str, list[dict]] = {}
    for rec in caps:
        skills.setdefault(rec["skill"], []).append({
            # publish() names the file by sha_json(record); recomputing with the
            # same function is what makes this row point at that record.
            "digest": sha_json(dict(rec)),
            "binding": dict(rec["binding"]),
            "preconditions": list(rec["preconditions"]),
            "effects": list(rec["effects"]),
            "measured": {"successes": rec["measured"]["successes"],
                         "n": rec["measured"]["n"]},
        })
    edges: list[dict] = []
    seen: set[tuple] = set()
    for a in caps:
        for b in caps:
            via = tuple(b["preconditions"])
            key = (a["skill"], b["skill"], via)
            if key in seen or not set(via) <= set(a["effects"]):
                continue
            seen.add(key)
            edges.append({"from": a["skill"], "to": b["skill"], "via": list(via)})
    return {"skills": skills, "edges": edges}
