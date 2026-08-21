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


def validate_plan(plan: Mapping, catalogue: Mapping[str, Mapping[str, type]],
                  oracles: Collection[str]) -> tuple[bool, str]:
    """``(ok, message)`` — fail-first, message names the offender.

    ``catalogue`` maps skill name -> {arg name: required python type}; it is
    authored by the skill side, never by the planner, which only selects and
    parameterizes. ``oracles`` are the verify predicates a plan may name.
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
        if not isinstance(node, Mapping) or set(node) != _NODE_KEYS:
            return False, (f"node[{i}] must be an object with exactly "
                           f"{sorted(_NODE_KEYS)}")
        nid = node["id"]
        if not isinstance(nid, str) or not nid:
            return False, f"node[{i}].id must be a non-empty string"
        if nid in ids:
            return False, f"duplicate node id {nid!r}"
        skill = node["skill"]
        if skill not in catalogue:
            return False, (f"node {nid!r} names unknown skill {skill!r}; "
                           f"catalogue is {sorted(catalogue)}")
        schema = catalogue[skill]
        args = node["args"]
        if not isinstance(args, Mapping):
            return False, f"node {nid!r}.args must be an object"
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
    return True, ""
