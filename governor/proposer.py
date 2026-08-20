"""The candidate-proposer seam: deterministic by default, LLM as a provider.

Two things this module insists on.

**Zero external API is the reference path, not a fallback.** ``SearchProposer``
is a real program synthesiser over the declared feature catalog, and every
result in this project was produced by it. A loop that cannot run without a
network and a key is a loop that cannot be reproduced.

**A proposer is untrusted input, whoever wrote it.** An LLM emits text; that text
becomes a Rule only by passing the same validation the deterministic proposer's
output passes -- the feature must be in the published catalog, its privilege must
fit the budget, the operator and reducer must be known, and the numbers must be
finite and in range. Nothing about "the model said so" shortens that path. This
is the same stance dsh takes toward tool arguments and Zetta toward Stage-2
candidates: the schema is the boundary, not the author.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from governor.features import REGISTRY, Privilege
from governor.governed import RecoverySpec, Rule
from governor.repertoire import names as strategy_names
from governor.search import REDUCERS, Trigger, search_triggers


class ProposerError(ValueError):
    """A proposal could not be turned into a Rule. Never silently dropped."""


class Proposer(Protocol):
    def propose(self, traces, labels, *, generation: int, privilege_budget: int,
                recovery_sensor_sd: float) -> Rule | None: ...
    @property
    def identity(self) -> str: ...


# --- the reference implementation ------------------------------------------

@dataclass
class SearchProposer:
    """Deterministic search over the catalog. No network, no key, no variance."""

    top_k: int = 3

    def propose(self, traces, labels, *, generation: int, privilege_budget: int,
                recovery_sensor_sd: float) -> Rule | None:
        ranked = search_triggers(traces, labels, privilege_budget=privilege_budget,
                                 top_k=self.top_k)
        if not ranked:
            return None
        return Rule(f"g{generation}", ranked[0].trigger,
                    RecoverySpec(sensor_sd=recovery_sensor_sd))

    @property
    def identity(self) -> str:
        return f"search@v1(top_k={self.top_k})"


# --- what an LLM is shown, and what it is allowed to return -----------------

def catalog_for_prompt(privilege_budget: int, present: set[str] | None = None) -> list[dict]:
    """The features a proposal may name, with their cost and meaning.

    `present` narrows the catalog to what the episodes actually recorded, so the
    proposer is never offered a name it could not have seen evidence for.
    """
    out = []
    for name, f in sorted(REGISTRY.items()):
        cost = 0 if f.privilege is Privilege.OBSERVABLE else 1
        if cost <= privilege_budget and (present is None or name in present):
            out.append({"name": name, "privilege_cost": cost, "doc": f.doc})
    return out


def build_brief(traces, labels, *, generation: int, privilege_budget: int) -> dict:
    """Everything the proposer is told. Statistics only -- never raw episodes.

    Keeping this a small JSON object rather than a transcript is deliberate: it
    is the same information the deterministic proposer works from, so the two
    providers are comparable, and it cannot smuggle in a privileged reading.
    """
    import numpy as np
    from governor.search import divergence_profile, reduce_series

    # Only what the traces actually carry: a trace holds exactly the features the
    # view held, which is itself budget-limited. Iterating the whole registry
    # would reach for a column that containment already kept out.
    present = set(traces[0]) if traces else set()
    stats = []
    for name in sorted(present & set(REGISTRY)):
        cost = 0 if REGISTRY[name].privilege is Privilege.OBSERVABLE else 1
        if cost > privilege_budget:
            continue
        for reducer in REDUCERS:
            red = [{name: reduce_series(t[name], reducer)} for t in traces]
            prof = np.abs(np.nan_to_num(divergence_profile(red, labels, name)))
            if prof.max() >= 1.0:
                stats.append({"feature": name, "reducer": reducer,
                              "peak_sigma": round(float(prof.max()), 2),
                              "peak_step": int(prof.argmax())})
    stats.sort(key=lambda s: -s["peak_sigma"])
    return {
        "generation": generation,
        "episodes": len(labels),
        "failures": int(len(labels) - sum(labels)),
        "privilege_budget": privilege_budget,
        "catalog": catalog_for_prompt(privilege_budget, present),
        "separations": stats[:12],
        "operators": ["lt", "gt"],
        "reducers": list(REDUCERS),
        "recovery_strategies": strategy_names(),
        "response_schema": {
            "feature": "str, from catalog", "op": "lt|gt", "threshold": "float",
            "dwell": "int >= 1", "arm_after": "int >= 0",
            "reducer": "value|min|max|range", "recovery": "str, from recovery_strategies",
        },
    }


def parse_proposal(payload: str | dict, *, generation: int, privilege_budget: int,
                   recovery_sensor_sd: float, n_steps: int) -> Rule:
    """Turn a proposer's response into a Rule, or refuse it.

    Every branch here is a refusal a real proposer can trigger. The privilege
    check is the load-bearing one: a proposal naming a feature outside the
    budget is rejected here, before it can reach a FeatureView that would not
    contain it anyway -- defence at both layers, on purpose.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProposerError(f"proposal is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProposerError(f"proposal must be an object, got {type(payload).__name__}")

    feature = payload.get("feature")
    if feature not in REGISTRY:
        raise ProposerError(
            f"unknown feature {feature!r}; catalog is {sorted(REGISTRY)}"
        )
    cost = 0 if REGISTRY[feature].privilege is Privilege.OBSERVABLE else 1
    if cost > privilege_budget:
        raise ProposerError(
            f"feature {feature!r} costs {cost} privilege, budget is {privilege_budget}"
        )
    op = payload.get("op")
    if op not in ("lt", "gt"):
        raise ProposerError(f"operator must be 'lt' or 'gt', got {op!r}")
    reducer = payload.get("reducer", "value")
    if reducer not in REDUCERS:
        raise ProposerError(f"unknown reducer {reducer!r}; known: {list(REDUCERS)}")
    try:
        threshold = float(payload["threshold"])
        dwell = int(payload["dwell"])
        arm_after = int(payload["arm_after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProposerError(f"threshold/dwell/arm_after missing or non-numeric: {exc}") from exc
    if not math.isfinite(threshold):
        raise ProposerError(f"threshold must be finite, got {threshold}")
    if dwell < 1:
        raise ProposerError(f"dwell must be at least 1, got {dwell}")
    if not 0 <= arm_after < n_steps:
        raise ProposerError(f"arm_after must lie in [0, {n_steps}), got {arm_after}")
    strategy = payload.get("recovery", "regrasp")
    if strategy not in strategy_names():
        raise ProposerError(f"unknown recovery {strategy!r}; known: {strategy_names()}")

    return Rule(f"g{generation}", Trigger(feature, op, threshold, dwell, arm_after, reducer),
                RecoverySpec(name=strategy, sensor_sd=recovery_sensor_sd))


@dataclass
class LlmProposer:
    """Ask a language model for a candidate; accept only what validates.

    `transport` takes the brief and returns the model's raw response. It is a
    plain callable so the whole path can be exercised against a scripted stand-in
    with no network and no key -- the same trick dsh uses to test its agent loop
    against a mock provider (packages/test-support/llm-mock-server).
    """

    transport: Callable[[dict], str]
    name: str = "llm"
    attempts: int = 2
    #: Refusals encountered, kept for the campaign artifact.
    rejections: list[str] = None

    def __post_init__(self) -> None:
        if self.rejections is None:
            self.rejections = []

    def propose(self, traces, labels, *, generation: int, privilege_budget: int,
                recovery_sensor_sd: float) -> Rule | None:
        brief = build_brief(traces, labels, generation=generation,
                            privilege_budget=privilege_budget)
        n_steps = max(len(next(iter(t.values()))) for t in traces)
        for attempt in range(self.attempts):
            raw = self.transport({**brief, "attempt": attempt,
                                  "previous_rejections": list(self.rejections)})
            try:
                return parse_proposal(raw, generation=generation,
                                      privilege_budget=privilege_budget,
                                      recovery_sensor_sd=recovery_sensor_sd, n_steps=n_steps)
            except ProposerError as exc:
                self.rejections.append(str(exc))
        return None

    @property
    def identity(self) -> str:
        return f"{self.name}@{self.attempts}attempts"


def scripted_transport(responses: Sequence[str | dict]) -> Callable[[dict], str]:
    """A stand-in that replays canned responses in order, then repeats the last.

    Lets the whole LLM path -- brief construction, transport, parsing, validation,
    retry -- be tested end to end at zero cost and with no variance.
    """
    queue = list(responses)

    def transport(brief: dict):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return transport
