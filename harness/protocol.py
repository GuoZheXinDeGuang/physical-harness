"""Skill graph protocol v0: the typed objects and the graph legality check.

Predicate refs are canonical strings ``name(arg1,arg2)`` (``pred_ref_str``).
Inside a ``SkillRecordV0`` a pred arg that names one of the record's ``args``
is a template slot, instantiated with the node's arg value at validation
(``instantiate``). Predicates are three-valued: True / False / None(unknown).
Stdlib-only; hashes go through ``harness.config.sha_json``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any

from harness.config import sha_json

#: Arg schema type names. ``entity`` is a scene object and must be grounded
#: (Legal.Grounded); ``str`` is a free string (a location label, a template).
TYPES: dict[str, type] = {"entity": str, "str": str, "int": int,
                          "float": float, "bool": bool}
TYPES_BY_PY: dict[type, str] = {float: "float", bool: "bool", int: "int", str: "str"}

_PRED = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*$")


# ---------------------------------------------------------------- pred refs

def parse_pred_ref(ref: Any) -> tuple[str, tuple[str, ...]]:
    """``(name, args)`` from a string ``'holding(apple)'``, a ``{name, args}``
    mapping, a ``(name, *args)`` tuple or a ``PredicateRecord``."""
    if isinstance(ref, PredicateRecord):
        return ref.name, tuple(ref.args)
    if isinstance(ref, Mapping):
        return str(ref["name"]), tuple(str(a) for a in ref.get("args", ()))
    if isinstance(ref, (tuple, list)):
        return str(ref[0]), tuple(str(a) for a in ref[1:])
    m = _PRED.match(str(ref))
    if not m:
        raise ValueError(f"bad predicate ref {ref!r}")
    args = tuple(a.strip() for a in m.group(2).split(",") if a.strip()) if m.group(2) else ()
    return m.group(1), args


def pred_ref_str(ref: Any) -> str:
    """Canonical ``name(a,b)`` form; ``name()`` for a nullary predicate."""
    name, args = parse_pred_ref(ref)
    return f"{name}({','.join(args)})"


def instantiate(ref: Any, args: Mapping[str, Any]) -> str:
    """Substitute template slots (pred args equal to a record arg name)."""
    name, pargs = parse_pred_ref(ref)
    return pred_ref_str((name, *(str(args[a]) if a in args else a for a in pargs)))


# ------------------------------------------------------------- three-valued

def tri(value: Any) -> bool | None:
    """Normalise a predicate result to True / False / None(unknown)."""
    return None if value is None else bool(value)


def all3(values: Iterable[bool | None]) -> bool | None:
    """Kleene AND: False if any False, else None if any unknown, else True."""
    out: bool | None = True
    for v in values:
        if v is False:
            return False
        if v is None:
            out = None
    return out


def eval_predicate(pred: PredicateRecord, sigma: Mapping[str, Any],
                   fn: Callable[[Mapping[str, Any]], Any]) -> bool | None:
    """None when any ``pred.reads`` key is missing from ``sigma``; else ``bool(fn(sigma))``."""
    if any(k not in sigma for k in pred.reads):
        return None
    return tri(fn(sigma))


# ------------------------------------------------------------------ records

@dataclass(frozen=True)
class Audit:
    n: int
    tp: int
    fp: int
    tn: int
    fn: int
    seed_block: str
    store: str

    @property
    def sensitivity(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def specificity(self) -> float:
        return self.tn / (self.tn + self.fp) if self.tn + self.fp else 0.0

    @property
    def base_rate(self) -> float:
        return (self.tp + self.fn) / self.n if self.n else 0.0

    def passes(self, th_s: float, th_p: float, eps: float) -> bool:
        return (self.sensitivity >= th_s and self.specificity >= th_p
                and eps <= self.base_rate <= 1 - eps)


@dataclass(frozen=True)
class PredicateRecord:
    id: str
    name: str
    args: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    bindings: dict[str, str] = field(default_factory=dict)      # embodiment -> "module:attr"
    audit: dict[str, Audit] = field(default_factory=dict)       # embodiment -> Audit

    @classmethod
    def from_dict(cls, d: Mapping) -> "PredicateRecord":
        d = dict(d)
        d["args"] = tuple(d.get("args", ()))
        d["reads"] = tuple(d.get("reads", ()))
        d["audit"] = {k: v if isinstance(v, Audit) else Audit(**v)
                      for k, v in d.get("audit", {}).items()}
        return cls(**d)


@dataclass(frozen=True)
class Evidence:
    n: int
    k: int
    seed_blocks: tuple[str, ...] = ()
    heldout: bool = False
    store: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillRecordV0:
    """Symbolic contract (requires/ensures/clobbers) is embodiment-independent;
    ``bindings``/``evidence`` are keyed by embodiment. ``args`` maps arg name ->
    a ``TYPES`` name. Distinct from ``harness.skill_record`` (capability rows
    keyed by ``module:factory`` refs); not a fork of it."""
    id: str
    name: str
    kind: str = "segment"
    lineage: dict[str, Any] = field(default_factory=dict)       # {parent, round}
    args: dict[str, str] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    ensures: tuple[str, ...] = ()
    clobbers: tuple[str, ...] = ()
    limits: dict[str, Any] = field(default_factory=dict)
    failure_modes: tuple[str, ...] = ()
    bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, d: Mapping) -> "SkillRecordV0":
        d = dict(d)
        for k in ("requires", "ensures", "clobbers", "failure_modes"):
            d[k] = tuple(pred_ref_str(r) if k != "failure_modes" else r
                         for r in d.get(k, ()))
        d["evidence"] = {k: v if isinstance(v, Evidence) else Evidence(**v)
                         for k, v in d.get("evidence", {}).items()}
        return cls(**d)


# -------------------------------------------------------------------- graph

@dataclass(frozen=True)
class Task:
    id: str
    goal: tuple[str, ...]


@dataclass(frozen=True)
class Node:
    id: str
    task: str
    skill: str                      # record id or name
    args: dict[str, Any] = field(default_factory=dict)
    after: tuple[str, ...] = ()
    on_fail: dict[str, Any] = field(default_factory=dict)   # {policy, budget?, rule?}


@dataclass(frozen=True)
class ExecutionGraph:
    mission: str
    seed: int
    tasks: tuple[Task, ...]
    nodes: tuple[Node, ...]
    rationale: str = ""
    planner: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping) -> "ExecutionGraph":
        return cls(
            mission=str(d["mission"]), seed=int(d.get("seed", 0)),
            tasks=tuple(Task(id=t["id"], goal=tuple(pred_ref_str(g) for g in t["goal"]))
                        for t in d.get("tasks", ())),
            nodes=tuple(Node(id=n["id"], task=n["task"], skill=n["skill"],
                             args=dict(n.get("args", {})), after=tuple(n.get("after", ())),
                             on_fail=dict(n.get("on_fail", {})))
                        for n in d.get("nodes", ())),
            rationale=str(d.get("rationale", "")), planner=dict(d.get("planner", {})))


@dataclass(frozen=True)
class VerifyEvent:
    node: str
    results: dict[str, bool | None]     # pred_str -> True | False | None


@dataclass(frozen=True)
class Fault:
    node: str
    failed: tuple[str, ...]             # pred_strs that were not True
    signature: str | None = None


def fault_from_verify(ev: VerifyEvent) -> Fault | None:
    failed = tuple(p for p, r in ev.results.items() if r is not True)
    return Fault(node=ev.node, failed=failed) if failed else None


@dataclass(frozen=True)
class Trajectory:
    """Pure projection of chain rows. ``id`` = hash(x, y)."""
    x: dict[str, Any]   # mission, sigma0 (sensed), skill_ids, show_evidence, done, fault
    y: dict[str, Any]   # graph, rationale
    o: dict[str, Any]   # legal, verify, L, success, replans, seed, block, role

    @property
    def id(self) -> str:
        return content_id({"x": self.x, "y": self.y})


# ------------------------------------------------------------------- hashing

def to_plain(obj: Any) -> Any:
    """JSON-able form: dataclasses -> dicts, tuples -> lists, sets -> sorted lists."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(to_plain(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    return obj


def content_id(obj: Any) -> str:
    """Content address: sha256 of the canonical JSON of ``obj``."""
    return sha_json(to_plain(obj))


# ---------------------------------------------------------------- Legal(G)

def _ancestors(nodes: Mapping[str, Node]) -> tuple[dict[str, set[str]], list[str]]:
    """Transitive predecessors per node; problems for cycles / unknown ids."""
    anc: dict[str, set[str]] = {}
    problems: list[str] = []

    def walk(nid: str, path: tuple[str, ...]) -> set[str]:
        if nid in anc:
            return anc[nid]
        if nid in path:
            problems.append(f"cycle through {nid!r}")
            return set()
        out: set[str] = set()
        for a in nodes[nid].after:
            if a not in nodes:
                problems.append(f"node {nid!r}.after names unknown node {a!r}")
                continue
            out.add(a)
            out |= walk(a, path + (nid,))
        anc[nid] = out
        return out

    for nid in nodes:
        walk(nid, ())
    return anc, problems


def _resolve(graph: Any, records: Mapping[str, SkillRecordV0]
             ) -> tuple[ExecutionGraph, dict[str, Node], dict[str, SkillRecordV0], list[str]]:
    g = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_dict(graph)
    problems: list[str] = []
    nodes: dict[str, Node] = {}
    for n in g.nodes:
        if n.id in nodes:
            problems.append(f"duplicate node id {n.id!r}")
        nodes[n.id] = n
    recs: dict[str, SkillRecordV0] = {}
    for n in g.nodes:
        rec = records.get(n.skill)
        if rec is None:
            problems.append(f"node {n.id!r} names unknown skill {n.skill!r}")
        else:
            recs[n.id] = rec
    return g, nodes, recs, problems


def validate_graph(graph: Any, records: Mapping[str, SkillRecordV0],
                   sigma0_facts: Collection[Any], sigma0_objects: Collection[str]
                   ) -> tuple[bool, list[str]]:
    """Legal(G) = Typed and Grounded and Supported and Covered. ``records`` is
    keyed by record id and/or name; ``sigma0_facts`` are pred refs (any form)
    true at start. Returns every problem found, not just the first."""
    g, nodes, recs, problems = _resolve(graph, records)
    facts = {pred_ref_str(f) for f in sigma0_facts}
    objects = set(sigma0_objects)
    anc, more = _ancestors(nodes)
    problems += more
    if problems:
        return False, problems
    task_ids = {t.id for t in g.tasks}

    # Typed
    for n in g.nodes:
        schema = recs[n.id].args
        if n.task not in task_ids:
            problems.append(f"node {n.id!r} names unknown task {n.task!r}")
        missing, unknown = sorted(set(schema) - set(n.args)), sorted(set(n.args) - set(schema))
        if missing or unknown:
            problems.append(f"typed: node {n.id!r} args missing {missing} unknown {unknown}")
        for k, v in n.args.items():
            t = TYPES.get(schema.get(k, ""))
            if t is None:
                if k in schema:
                    problems.append(f"typed: record {recs[n.id].id!r} arg {k!r} has "
                                    f"unknown type {schema[k]!r}")
            elif not isinstance(v, t) or (t is not bool and isinstance(v, bool)):
                problems.append(f"typed: node {n.id!r} arg {k!r} must be "
                                f"{schema[k]}, got {type(v).__name__}")

    # Instantiated contracts per node.
    req = {n.id: [instantiate(p, n.args) for p in recs[n.id].requires] for n in g.nodes}
    ens = {n.id: [instantiate(p, n.args) for p in recs[n.id].ensures] for n in g.nodes}
    clob = {n.id: {instantiate(p, n.args) for p in recs[n.id].clobbers} for n in g.nodes}

    # Grounded: entity args in sigma0.objects or in an ancestor's ensures.
    for n in g.nodes:
        produced = {a for m in anc[n.id] for p in ens[m] for a in parse_pred_ref(p)[1]}
        for k, v in n.args.items():
            if recs[n.id].args.get(k) == "entity" and str(v) not in objects | produced:
                problems.append(f"grounded: node {n.id!r} arg {k}={v!r} is not in "
                                f"sigma0.objects nor produced by a predecessor")

    def before(a: str, b: str) -> bool:      # a strictly precedes b
        return a in anc[b]

    def unthreatened(p: str, m: str | None, n: str | None, ok: Callable[[str], bool]) -> bool:
        # m: supporter node (None = sigma0); n: consumer (None = end).
        # c threatens iff it clobbers p, is not before m, not after n, not m/n itself.
        for c in nodes:
            if p not in clob[c] or c == m or c == n or ok(c):
                continue
            if m is not None and before(c, m):
                continue
            if n is not None and before(n, c):
                continue
            return False
        return True

    # Supported: every require has an unthreatened supporter (sigma0 or an ancestor).
    for n in g.nodes:
        for p in req[n.id]:
            supporters = ([None] if p in facts else []) + [m for m in anc[n.id] if p in ens[m]]
            if not supporters:
                problems.append(f"supported: node {n.id!r} requires {p} which nothing provides")
            elif not any(unthreatened(p, m, n.id, lambda c: False) for m in supporters):
                problems.append(f"supported: node {n.id!r} requires {p} but every "
                                f"supporter is threatened by a clobber")

    # Covered: each task goal in the union of its nodes' ensures, unthreatened at task end.
    for t in g.tasks:
        members = [n.id for n in g.nodes if n.task == t.id]
        after_task = lambda c: all(before(m, c) for m in members)  # noqa: E731
        for p in t.goal:
            supporters = [m for m in members if p in ens[m]]
            if not supporters:
                problems.append(f"covered: task {t.id!r} goal {p} is ensured by none of its nodes")
            elif not any(unthreatened(p, m, None, after_task) for m in supporters):
                problems.append(f"covered: task {t.id!r} goal {p} is clobbered before task end")
    return not problems, problems


def replan_monotone(old_graph: Any, new_graph: Any, done_ids: Collection[str]
                    ) -> tuple[bool, list[str]]:
    """D subset of nodes(G') with identical (skill, args) per done node.
    Legality of G' against current facts is ``validate_graph``'s job."""
    old = old_graph if isinstance(old_graph, ExecutionGraph) else ExecutionGraph.from_dict(old_graph)
    new = new_graph if isinstance(new_graph, ExecutionGraph) else ExecutionGraph.from_dict(new_graph)
    o = {n.id: n for n in old.nodes}
    nw = {n.id: n for n in new.nodes}
    problems = []
    for d in done_ids:
        if d not in o:
            problems.append(f"done node {d!r} is not in the old graph")
        elif d not in nw:
            problems.append(f"replan dropped done node {d!r}")
        elif (nw[d].skill, dict(nw[d].args)) != (o[d].skill, dict(o[d].args)):
            problems.append(f"replan rewrote done node {d!r}: {nw[d].skill}{dict(nw[d].args)} "
                            f"!= {o[d].skill}{dict(o[d].args)}")
    return not problems, problems


# ------------------------------------------------------------ VLM projection

#: The exact reply shape a VLM planner must emit (the validate_plan dialect:
#: node keys are exactly id/skill/args/after, ``after`` names EARLIER ids).
VLM_OUTPUT_SCHEMA: dict[str, Any] = {
    "goal": "<string>",
    "nodes": [{"id": "<unique string>", "skill": "<a skill name from skills>",
               "args": {"<arg>": "<value of the declared type>"},
               "after": ["<id of an EARLIER node>"]}],
    "verify": [{"after": "<node id>", "predicate": "<an oracle name>"}],
    "rationale": "<string: why this graph is legal and sufficient>",
}


def evidence_interval(ev: Evidence, z: float = 1.96) -> list[float]:
    """Wilson 95% interval on k/n; ``[0, 1]`` when n == 0."""
    if ev.n <= 0:
        return [0.0, 1.0]
    p, n = ev.k / ev.n, ev.n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


# ------------------------------------------------------------ PlanRecord

#: Keys a planner stamps on its returned graph that are NOT the graph: strip
#: them before hashing so the same graph from two providers has one id.
_GRAPH_META = ("planner", "rationale")


def graph_sha(graph: Mapping) -> str:
    """Content id of a planner-format graph dict minus ``planner``/``rationale``."""
    return content_id({k: v for k, v in graph.items() if k not in _GRAPH_META})


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (NR ``betacf``)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)),
                   -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 3e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def plan_lower_bound(n: int, k: int, alpha: float = 0.05) -> float:
    """Jeffreys ``1 - alpha`` lower bound on k/n: the ``alpha/2`` quantile of
    Beta(k + 1/2, n - k + 1/2). ``0.0`` when n == 0. Stdlib only (bisection on
    the regularized incomplete beta), so ``harness/`` stays scipy-free."""
    if n <= 0:
        return 0.0
    a, b, lo, hi = k + 0.5, n - k + 0.5, 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _betainc(a, b, mid) < alpha / 2.0:
            lo = mid
        else:
            hi = mid
    return round(lo, 4)


@dataclass(frozen=True)
class PlanRecord:
    """A whole-task graph promoted into the skills root (``kind == "plan"``),
    next to the SkillRecords, through the same publish door, evolution mode
    only. ``id`` is ``graph_sha(graph)``; ``graph`` is the planner-format dict
    the ``task.plan`` row sealed (goal/nodes/verify, meta stripped) -- what the
    library planner hands back verbatim. ``rule`` records the selection rule
    the record passed (theta/n_min are config, never a hidden constant)."""
    id: str
    task: str
    goal: tuple[str, ...]
    graph: dict[str, Any]
    embodiment: str
    arm: str
    evidence: dict[str, Any]        # {n, k, L_mean, seed_blocks, sessions}
    rule: dict[str, Any]            # {theta, n_min, lower}
    published_from: list[Any] = field(default_factory=list)   # chain refs
    kind: str = "plan"

    @classmethod
    def from_dict(cls, d: Mapping) -> "PlanRecord":
        d = dict(d)
        d["goal"] = tuple(pred_ref_str(g) for g in d.get("goal", ()))
        d["published_from"] = list(d.get("published_from", ()))
        return cls(**d)

    def execution_graph(self, seed: int = 0) -> dict[str, Any]:
        """The ExecutionGraph dict ``validate_graph`` reads: one task named
        after ``task`` carrying the record's real goal, nodes labelled by it."""
        return {"mission": self.graph.get("goal", ""), "seed": seed,
                "tasks": [{"id": self.task, "goal": list(self.goal)}],
                "nodes": [{"id": n["id"], "task": self.task, "skill": n["skill"],
                           "args": dict(n.get("args", {})), "after": list(n.get("after", ()))}
                          for n in self.graph.get("nodes") or []]}


def vlm_projection(records: Mapping[str, Any], sigma0_facts: Collection[Any],
                   sigma0_objects: Collection[str], done: Collection[Any],
                   fault: Any, *, show_evidence: bool = False) -> dict[str, Any]:
    """The planner-facing view of the library + start state: compact skill
    cards, facts, objects, done ids, last fault and the output schema. Pure and
    deterministic (sorted, plain JSON types) so ``content_id`` of it is stable."""
    cards = []
    for name in sorted(records):
        r = records[name]
        rec = r if isinstance(r, SkillRecordV0) else SkillRecordV0.from_dict(r)
        card: dict[str, Any] = {"name": name, "kind": rec.kind,
                                "args": dict(sorted(rec.args.items())),
                                "requires": list(rec.requires),
                                "ensures": list(rec.ensures)}
        if rec.clobbers:
            card["clobbers"] = list(rec.clobbers)
        if rec.description:
            card["description"] = rec.description
        if show_evidence:
            card["evidence"] = {emb: evidence_interval(ev)
                                for emb, ev in sorted(rec.evidence.items())}
        cards.append(card)
    return {"skills": cards,
            "facts": sorted(pred_ref_str(f) for f in sigma0_facts),
            "objects": sorted(str(o) for o in sigma0_objects),
            "done": to_plain(list(done)), "fault": to_plain(fault),
            "output_schema": VLM_OUTPUT_SCHEMA}


# ---------------------------------------------------------- mission projection

#: The exact reply shape a mission decomposer must emit: known-task references
#: plus grounded goal preds drawn from the predicate catalogue.
MISSION_DECOMPOSE_SCHEMA: dict[str, Any] = {
    "tasks": [{"id": "<unique string>",
               "task": "<a task name from known_tasks (omit only if none fits)>",
               "goal": ["<grounded pred ref over objects, e.g. on(cubeA, cubeB); "
                        "predicate name from predicates>"]}],
    "rationale": "<string: why these tasks in this order achieve the mission>",
}


def mission_projection(known_tasks: Collection[Mapping[str, Any]],
                       predicate_catalogue: Mapping[str, Any],
                       objects: Collection[str]) -> dict[str, Any]:
    """The decomposer-facing view: known tasks ({task, goal, description}), the
    predicate catalogue (name -> arg names; values may be PredicateRecords or
    arg sequences), objects and the output schema. Sorted and plain, so
    ``content_id`` of it is stable."""
    tasks = sorted(({"task": str(t["task"]),
                     "goal": sorted(pred_ref_str(g) for g in t.get("goal", ())),
                     "description": str(t.get("description", ""))}
                    for t in known_tasks), key=lambda t: t["task"])
    preds = {str(name): list(getattr(rec, "args", rec))
             for name, rec in predicate_catalogue.items()}
    return {"known_tasks": tasks, "predicates": dict(sorted(preds.items())),
            "objects": sorted(str(o) for o in objects),
            "output_schema": MISSION_DECOMPOSE_SCHEMA}
