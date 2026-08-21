"""Search reasoner: the reference Reasoner for capability `reasoner.proposer`.

Thin adapter over `governor.proposer.SearchProposer`, the deterministic,
zero-external-API search path (see governor/proposer.py's module docstring:
"zero external API is the reference path, not a fallback"). A real VLM-backed
Reasoner is a later provider behind the same `propose(brief) -> Mapping` seam;
this one just wraps the search that already exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from governor.proposer import SearchProposer


class SearchReasoner:
    """Layer 1 `harness.contracts.Reasoner`.

    `propose` takes a `brief` mapping with four required keys:

    - ``traces``: the per-episode feature traces the search reads (same shape
      `governor.proposer.SearchProposer.propose` already takes).
    - ``labels``: the matching pass/fail label per trace.
    - ``generation``: the generation index stamped into the resulting rule_id.
    - ``prereg``: the campaign's `plugins.rsi.campaign.Preregistration`, supplying
      `critic_budget` (the privilege budget the search may spend) and
      `recovery_sensor_sd` (the recovery's percept noise).

    Returns ``{"rule": <Rule.canonical() dict>}`` when the search finds a
    candidate, or ``{"rule": None}`` when it finds nothing -- never a bare
    `Rule` instance, so the seam's return value is always a plain Mapping per
    `harness.contracts.Reasoner`.
    """

    def __init__(self, top_k: int = 3) -> None:
        self._inner = SearchProposer(top_k=top_k)

    def propose(self, brief: Mapping) -> Mapping:
        prereg = brief["prereg"]
        rule = self._inner.propose(
            brief["traces"], brief["labels"],
            generation=brief["generation"],
            privilege_budget=prereg.critic_budget,
            recovery_sensor_sd=prereg.recovery_sensor_sd,
        )
        return {"rule": rule.canonical() if rule is not None else None}


def provider(**params: Any) -> SearchReasoner:
    return SearchReasoner(**params)
