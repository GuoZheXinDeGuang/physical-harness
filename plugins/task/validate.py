"""Plan validation: the untrusted-planner boundary, harness-native.

Same stance as ``governor.proposer.parse_proposal``: every branch is a refusal
a real planner can trigger, and the message is written to be folded straight
back into the next brief. Stdlib-only on purpose — the AST boundary test
forbids reaching zos or sibling plugins, so the boundary is re-authored here,
never reused from zos/graph.py.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

#: The exact key set every node must carry: the 4-key graph dialect zos's
#: cockpit renders is the same JSON this validator admits.
_NODE_KEYS = frozenset({"id", "skill", "args", "after"})

#: The node KINDS the loop can dispatch. This frozenset is the single source of
#: truth for the NAMES; ``workload._KIND_HANDLERS`` maps exactly these to their
#: generic handlers and self-checks coverage against this set at import. A node
#: with no ``kind`` defaults to ``"manipulate"`` -- so existing cards (no kind on
#: any node) validate byte-identically, sealed plan shas unmoved.
NODE_KINDS = frozenset({"manipulate", "segment", "perceive", "decide", "verify"})


def validate_plan(plan: Mapping, catalogue: Mapping[str, Mapping[str, type]],
                  oracles: Collection[str],
                  done: Collection[Mapping] = (),
                  requirements: Mapping | None = None) -> tuple[bool, str]:
    """``(ok, message)`` — fail-first, message names the offender.

    ``catalogue`` maps skill name -> {arg name: required python type}; it is
    authored by the skill side, never by the planner, which only selects and
    parameterizes. ``oracles`` are the verify predicates a plan may name.

    ``done`` is the workload's own ledger of completed nodes (``{id, skill,
    args}`` each), non-empty only on a replan: the new graph must carry every
    one of them verbatim, because attribution, per-node billing, and
    completed-node skipping all key on node ids across replans — a planner
    that renames or rewrites finished work re-bills it silently.

    ``requirements`` is optional task-authored grounding. An ``objects`` list
    plus ``required_per_object_order`` requires exactly one call to each named
    skill for every object, with dependency ancestry preserving that order.
    ``target_by_object`` fixes any target argument carried by those calls.
    """
    if not isinstance(plan, Mapping):
        return False, f"plan must be a JSON object, got {type(plan).__name__}"
    goal = plan.get("goal")
    if not isinstance(goal, str) or not goal:
        return False, "plan.goal must be a non-empty string"
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return False, "plan.nodes must be a non-empty list of skill calls"
    ids: list[str] = []
    for i, node in enumerate(nodes):
        keys = set(node) if isinstance(node, Mapping) else set()
        if not isinstance(node, Mapping) or keys - {"kind"} != _NODE_KEYS:
            return False, (f"node[{i}] must be an object with exactly "
                           f"{sorted(_NODE_KEYS)} (optional 'kind')")
        nid = node["id"]
        if not isinstance(nid, str) or not nid:
            return False, f"node[{i}].id must be a non-empty string"
        if nid in ids:
            return False, f"duplicate node id {nid!r}"
        kind = node.get("kind", "manipulate")
        if kind not in NODE_KINDS:
            return False, (f"node {nid!r} declares unknown kind {kind!r}; "
                           f"known kinds: {sorted(NODE_KINDS)}")
        skill = node["skill"]
        if skill not in catalogue:
            return False, (f"node {nid!r} names unknown skill {skill!r}; "
                           f"catalogue is {sorted(catalogue)}")
        schema = catalogue[skill]
        args = node["args"]
        if not isinstance(args, Mapping):
            return False, f"node {nid!r}.args must be an object"
        missing = set(schema) - set(args)
        if missing:
            return False, (f"node {nid!r} is missing required args "
                           f"{sorted(missing)} for {skill!r}")
        for key, value in args.items():
            if key not in schema:
                return False, (f"node {nid!r} passes unknown arg {key!r} to "
                               f"{skill!r}; declared args: {sorted(schema)}")
            if not isinstance(value, schema[key]):
                return False, (f"node {nid!r} arg {key!r} must be "
                               f"{schema[key].__name__}, got {type(value).__name__}")
        after = node["after"]
        # EARLIER ids only: admits exactly the topologically ordered DAGs, so
        # execution order is the list order — determinism for free.
        if not isinstance(after, list) or not all(a in ids for a in after):
            return False, (f"node {nid!r}.after must list ids of earlier nodes; "
                           f"earlier ids: {ids}")
        ids.append(nid)
    verify = plan.get("verify")
    if not isinstance(verify, list) or not verify:
        return False, "plan.verify must be a non-empty list: an unverified plan is vacuous"
    for i, v in enumerate(verify):
        if not isinstance(v, Mapping) or v.get("after") not in ids:
            return False, f"verify[{i}].after must name a node id from {ids}"
        pred = v.get("predicate")
        if pred not in oracles:
            return False, (f"verify[{i}] names unknown predicate {pred!r}; "
                           f"declared oracles: {sorted(oracles)}")
    # Verify coverage: every manipulate/segment node must be gated by a machine
    # check — a verify-list edge after it, or a verify-KIND successor node.
    # Without this a planner can emit six action nodes and one verify edge and
    # the five misses fail silently (audit oracles before trusting them).
    covered = {v["after"] for v in verify}
    for node in nodes:
        if node.get("kind", "manipulate") == "verify":
            covered.update(node["after"])
    for node in nodes:
        kind = node.get("kind", "manipulate")
        if kind in ("manipulate", "segment") and node["id"] not in covered:
            return False, (f"node {node['id']!r} (kind {kind!r}) is not covered "
                           "by any verify: add a verify entry after it or a "
                           "verify-kind successor node")
    if requirements:
        objects = requirements.get("objects")
        required_order = requirements.get("required_per_object_order")
        if objects is not None or required_order is not None:
            if (not isinstance(objects, list) or not objects
                    or not all(isinstance(obj, str) and obj for obj in objects)):
                return False, "planning_context.objects must be a non-empty string list"
            if (not isinstance(required_order, list) or not required_order
                    or not all(isinstance(skill, str) and skill
                               for skill in required_order)):
                return False, ("planning_context.required_per_object_order must "
                               "be a non-empty string list")
            ancestors: dict[str, set[str]] = {}
            for node in nodes:
                direct = set(node["after"])
                ancestors[node["id"]] = direct.union(
                    *(ancestors[parent] for parent in direct)) if direct else set()
            known_objects = set(objects)
            for node in nodes:
                if (node["skill"] in required_order
                        and node["args"].get("object") not in known_objects):
                    return False, (f"node {node['id']!r} applies required skill "
                                   f"{node['skill']!r} to undeclared object "
                                   f"{node['args'].get('object')!r}")
            for obj in objects:
                previous: Mapping | None = None
                for skill in required_order:
                    calls = [node for node in nodes
                             if node["skill"] == skill
                             and node["args"].get("object") == obj]
                    if len(calls) != 1:
                        return False, (f"object {obj!r} requires exactly one "
                                       f"{skill!r} call, got {len(calls)}")
                    current = calls[0]
                    if (previous is not None
                            and previous["id"] not in ancestors[current["id"]]):
                        return False, (f"object {obj!r} requires {skill!r} after "
                                       f"{previous['skill']!r}")
                    previous = current
            target_by_object = requirements.get("target_by_object")
            if target_by_object is not None:
                if not isinstance(target_by_object, Mapping):
                    return False, "planning_context.target_by_object must be an object"
                for node in nodes:
                    args = node["args"]
                    obj = args.get("object")
                    if obj in known_objects and "target" in args:
                        expected = target_by_object.get(obj)
                        if args["target"] != expected:
                            return False, (f"node {node['id']!r} targets "
                                           f"{args['target']!r} for object {obj!r}; "
                                           f"expected {expected!r}")
    # Replan stability: every completed node must reappear byte-identical.
    by_id = {n["id"]: n for n in nodes}
    for d in done:
        node = by_id.get(d["id"])
        if node is None:
            return False, (f"replan dropped completed node {d['id']!r}; a replan "
                           "must preserve every done node's {id, skill, args} verbatim")
        if node["skill"] != d["skill"] or dict(node["args"]) != dict(d["args"]):
            return False, (f"replan rewrote completed node {d['id']!r}: got "
                           f"skill {node['skill']!r} args {dict(node['args'])}, "
                           f"completed as skill {d['skill']!r} args {dict(d['args'])}; "
                           "done nodes must be preserved verbatim")
    return True, ""
