"""Static skill library: one ``SkillRecordV0`` JSON per skill under
``skill-library/records/<name>.json`` (harness/protocol.py is the schema).

The symbolic contract (args / requires / ensures / clobbers) is embodiment-
neutral; ``bindings[embodiment]`` carries the execution half: ``task_template``
+ ``backend`` for a persistent segment, ``episode`` for a one-rollout node
(the EpisodeSpec kwargs ``plugins.task.workload`` dispatches). A binding with
``implemented: false`` is declared but not planner-visible.

A binding may split its execution half by ARM: ``policies.scripted`` is the
stage-driver binding above, and another arm (``pi05``) names the provider ref
that executes the segment instead (``{"transport", "ref", "checkpoint_sha",
"params"}``; ``transport`` is one of harness.skill_executor.TRANSPORTS, default
``inproc``). A skill with no binding for the requested arm keeps the scripted
one -- the handover shape.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from harness.manifest import discover
from harness.protocol import TYPES, SkillRecordV0
from harness.skill_executor import TRANSPORTS

ROOT = Path(__file__).resolve().parent.parent / "skill-library" / "records"


def load_records(root: Path = ROOT) -> dict[str, SkillRecordV0]:
    out: dict[str, SkillRecordV0] = {}
    for path in sorted(Path(root).glob("*.json")):
        rec = SkillRecordV0.from_dict(json.loads(path.read_text()))
        if rec.name in out:
            raise ValueError(f"duplicate skill record {rec.name!r}")
        bad = {k: t for k, t in rec.args.items() if t not in TYPES}
        if bad:
            raise ValueError(f"skill {rec.name!r} args have unknown types {bad}")
        for b in rec.bindings.values():
            for key, p in (b.get("policies") or {}).items():
                if p.get("transport", "inproc") not in TRANSPORTS:
                    raise ValueError(f"skill {rec.name!r} policies[{key!r}] transport "
                                     f"{p.get('transport')!r} not in {TRANSPORTS}")
        out[rec.name] = rec
    if not out:
        raise ValueError(f"no skill records under {root}")
    bind_executors(out, discover().executors)
    return out


def bind_executors(records: dict[str, SkillRecordV0], executors: Iterable[Mapping]) -> None:
    """Fold each mounted card's ``[executors.<key>]`` (harness.manifest) into
    ``bindings.<emb>.policies.<key> = {transport, ref}`` -- a candidate card binds
    its executor onto a skill only while mounted (PH_PLUGINS_EXTRA); the record
    files stay untouched until evolve publishes a measured row. Loud on a skill the
    library lacks (a mis-declared candidate must not silently bind nothing)."""
    for e in executors:
        if e["skill"] not in records:
            raise ValueError(f"plugin {e['plugin']!r} binds executor {e['key']!r} to unknown "
                             f"skill {e['skill']!r}")
        if e["transport"] not in TRANSPORTS:
            raise ValueError(f"plugin {e['plugin']!r} executor {e['key']!r} transport "
                             f"{e['transport']!r} not in {TRANSPORTS}")
        b = records[e["skill"]].bindings.setdefault(e["embodiment"], {})
        pols = b.setdefault("policies", {})
        pols.setdefault("scripted", {})  # the record's own task stays the scripted base
        pols[e["key"]] = {"transport": e["transport"], "ref": e["ref"]}


def _binding(rec: SkillRecordV0, embodiment: str) -> dict[str, Any] | None:
    b = rec.bindings.get(embodiment)
    if b is None or not b.get("implemented", True):
        return None
    if "policies" in b:  # scripted is the base spec; the other arms ride on it
        b = {**b, **b["policies"]["scripted"],
             "policies": {a: p for a, p in b["policies"].items() if a != "scripted"}}
    return b


def executor_key(spec: Mapping[str, Any], arm: str, executor: str | None = None) -> str:
    """The policy key :func:`rearm` resolves for a spec: an explicit ``executor``
    wins (refused when the spec does not bind it); else the arm when bound, else
    ``scripted`` (handover, and the whole of arm ``auto`` with no explicit pick)."""
    if arm != "auto" and arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; known arms: {sorted(ARMS)}")
    policies = spec.get("policies") or {}
    if executor is not None:
        if executor != "scripted" and executor not in policies:
            raise ValueError(f"unknown executor {executor!r}; bound: "
                             f"{sorted({'scripted', *policies})}")
        return executor
    return arm if arm in policies else "scripted"


def rearm(spec: Mapping[str, Any], arm: str, executor: str | None = None) -> dict[str, Any]:
    """Resolve a spec's ``policies`` for ``arm`` (or the node's explicit
    ``executor``, see :func:`executor_key`) into the executor binding the
    workload mounts: ``{key, transport, ref, params, checkpoint_sha, spec}``.
    ``spec`` is the EpisodeSpec kwargs (``policies`` stripped, ``policy_provider``
    set when a ref is bound); ``params`` are the provider-mount params (the
    record's ``params`` plus the pinned ``checkpoint_sha``). Scripted, or an arm
    the record has no binding for, keeps the stage driver: ref None, inproc."""
    key = executor_key(spec, arm, executor)
    spec = dict(spec)
    p = (spec.pop("policies", None) or {}).get(key) or {}
    sha = p.get("checkpoint_sha")
    params = dict(p.get("params") or {})
    if sha:
        params["checkpoint_sha"] = sha
    if p.get("ref"):
        spec["policy_provider"] = p["ref"]
    return {"key": key, "transport": p.get("transport", "inproc"), "ref": p.get("ref"),
            "params": params, "checkpoint_sha": sha, "spec": spec}


def select(records: Mapping[str, SkillRecordV0], embodiment: str,
           names: Iterable[str]) -> dict[str, SkillRecordV0]:
    """The planner-visible subset: every name must carry an implemented binding."""
    out = {}
    for name in names:
        if name not in records:
            raise KeyError(f"unknown skill record {name!r}")
        if _binding(records[name], embodiment) is None:
            raise ValueError(f"skill {name!r} has no implemented {embodiment!r} binding")
        out[name] = records[name]
    return out


def catalogue_of(records: Mapping[str, SkillRecordV0]) -> dict[str, dict[str, type]]:
    """The ``{skill: {arg: python type}}`` shape validate_plan / planner briefs use."""
    return {name: {k: TYPES[t] for k, t in rec.args.items()}
            for name, rec in records.items()}


def planner_docs(records: Mapping[str, SkillRecordV0]) -> dict[str, dict[str, Any]]:
    return {name: {"description": rec.description, "kind": rec.kind,
                   "arguments": dict(rec.args), "requires": list(rec.requires),
                   "ensures": list(rec.ensures), "clobbers": list(rec.clobbers)}
            for name, rec in records.items()}


def segment_specs(records: Mapping[str, SkillRecordV0], embodiment: str,
                  arm: str | None = None) -> dict[str, dict[str, Any]]:
    """``{skill: {task | task_template}}`` for the persistent-segment bindings (fresh dicts:
    mission cards add their ``allowed_args`` grounding on top). ``arm=None`` carries
    every non-scripted arm's binding along under ``policies`` for the runtime to
    :func:`rearm` per brief; a named arm resolves it here."""
    out = {}
    for name, rec in records.items():
        b = _binding(rec, embodiment)
        if b and ("task" in b or "task_template" in b):
            spec = {k: b[k] for k in ("task", "task_template", "policies")
                    if b.get(k)}
            out[name] = spec if arm is None else rearm(spec, arm)["spec"]
    return out


def skill_specs(records: Mapping[str, SkillRecordV0], embodiment: str,
                arm: str = "scripted") -> dict[str, dict[str, Any]]:
    """``{skill: EpisodeSpec kwargs}`` for the one-rollout bindings."""
    return {name: rearm({**b["episode"], "policies": b.get("policies")}, arm)["spec"]
            for name, rec in records.items()
            if (b := _binding(rec, embodiment)) and "episode" in b}


RECORDS = load_records()
#: Every executor arm a record binds, scripted always: a brief naming another is refused.
ARMS = frozenset(("scripted", *(a for r in RECORDS.values() for b in r.bindings.values()
                                for a in (b.get("policies") or ()))))
