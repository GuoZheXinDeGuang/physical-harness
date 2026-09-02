"""Predicates as plugin capabilities: fold ``provides(kind=predicate)`` into
``PredicateRecord``s, evaluate them three-valued on sigma, gate them by audit.

A predicate ref is ``"module:factory"``; ``factory(**args)`` returns
``pred(**reads)``, the same zero-arg-factory convention the robocasa card's
PREDICATES table already uses, resolved through ``harness.registry.load_provider``.
Plugins are imported only inside ``evaluate`` (harness stays plugin-free).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from harness.manifest import Registry, discover
from harness.protocol import Audit, PredicateRecord, content_id, parse_pred_ref, tri
from harness.registry import load_provider


def records(reg: Registry | None = None) -> dict[str, PredicateRecord]:
    """name -> PredicateRecord; bindings keyed by declaring card. The symbolic
    part (args, reads) is embodiment-independent, so two cards binding one name
    must agree on it (loud otherwise)."""
    out: dict[str, PredicateRecord] = {}
    for p in (reg or discover()).provides:
        if p["kind"] != "predicate":
            continue
        prev = out.get(p["name"])
        if prev and (prev.args, prev.reads) != (p["args"], p["reads"]):
            raise ValueError(f"predicate {p['name']!r}: {p['plugin']!r} declares "
                             f"args/reads {p['args']}/{p['reads']} but another card "
                             f"declared {prev.args}/{prev.reads}")
        bindings = {**(prev.bindings if prev else {}), p["plugin"]: p["ref"]}
        sym = {"name": p["name"], "args": p["args"], "reads": p["reads"]}
        out[p["name"]] = PredicateRecord(id=content_id(sym), bindings=bindings, **sym)
    return out


def evaluate(ref: Any, sigma: Mapping[str, Any], embodiment: str | None = None,
             recs: Mapping[str, PredicateRecord] | None = None) -> bool | None:
    """True/False, or None when any ``reads`` key is missing from sigma.
    ``embodiment`` picks the binding (optional when the record has exactly one)."""
    name, args = parse_pred_ref(ref)
    rec = (recs if recs is not None else records())[name]
    if len(args) != len(rec.args):
        raise ValueError(f"{name} takes args {rec.args}, got {args}")
    if any(k not in sigma for k in rec.reads):
        return None
    if embodiment is None:
        if len(rec.bindings) != 1:
            raise ValueError(f"{name}: pick an embodiment from {sorted(rec.bindings)}")
        embodiment = next(iter(rec.bindings))
    fn = load_provider(rec.bindings[embodiment], dict(zip(rec.args, args)))
    return tri(fn(**{k: sigma[k] for k in rec.reads}))


def audit_gate(counts: Mapping[str, int], th_sens: float, th_spec: float,
               eps: float) -> tuple[bool, list[str]]:
    """``counts`` = {n, tp, fp, tn, fn}. Thresholds are parameters, never baked in."""
    a = Audit(**{k: int(counts[k]) for k in ("n", "tp", "fp", "tn", "fn")},
              seed_block=str(counts.get("seed_block", "")), store=str(counts.get("store", "")))
    reasons = []
    if a.sensitivity < th_sens:
        reasons.append(f"sensitivity {a.sensitivity:.3f} < {th_sens}")
    if a.specificity < th_spec:
        reasons.append(f"specificity {a.specificity:.3f} < {th_spec}")
    if not eps <= a.base_rate <= 1 - eps:
        reasons.append(f"base_rate {a.base_rate:.3f} outside [{eps}, {1 - eps}]")
    return not reasons, reasons
