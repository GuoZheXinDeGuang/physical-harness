"""Qwen3-8B reasoner card: the layer-1 proposer seam, backed by a real model.

Wraps ``governor.proposer.LlmProposer`` + ``qwen38_transport`` (the local sglang
endpoint, round 81) as a ``harness.contracts.Reasoner``, so the exact same
"a proposer is untrusted input" validation the deterministic search path passes
gates whatever the model emits -- nothing about "the model said so" shortens the
schema check (governor/proposer.py's stance).

Two things this card exists to prove.

**The model identity enters the content hash.** ``qwen38_transport`` resolves the
model id and base_url from ``QWEN38_MODEL`` / ``QWEN38_BASE_URL`` at call time, so
until now which model proposed a campaign's rules was smuggled in via the
environment and never entered any sha -- two campaigns run against different
models were indistinguishable. ``identity`` reads that identity at construction
and ``run_campaign`` stamps it onto the preregistration, closing the hole.

**It degrades gracefully when the GPU is held.** ``available()`` probes GET
/models (one short request, exactly like ``scripts/round25_rerun.qwen38_reachable``);
``propose`` skips loudly and returns ``{"rule": None}`` when the endpoint is down,
so the campaign loop never crashes on a missing model. ``plugin_doctor`` Tier-A's
the shape regardless and SKIPs the Tier-B smoke when the probe fails.

Installed but INACTIVE (``enabled = false`` in the manifest): it claims the same
``reasoner.proposer`` seam plugins/reasoner already owns, so it sits in the cage
doctorable but unfolded until an operator flips it on and disables the incumbent.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from collections.abc import Mapping
from typing import Any

from governor.proposer import LlmProposer, qwen38_transport

_DEFAULT_BASE = "http://localhost:30000/v1"


class QwenReasoner:
    """``harness.contracts.Reasoner`` over the qwen38 sglang endpoint."""

    def __init__(self, *, base_url: str | None = None, model: str | None = None,
                 temperature: float = 0.0, seed: int = 0, attempts: int = 2,
                 timeout: float = 30.0) -> None:
        self._base = (base_url or os.environ.get("QWEN38_BASE_URL", _DEFAULT_BASE)).rstrip("/")
        self._model = model or os.environ.get("QWEN38_MODEL")
        self._temperature = temperature
        self._seed = seed
        self._attempts = attempts
        self._timeout = timeout
        self._llm = LlmProposer(
            transport=qwen38_transport(base_url=self._base, model=self._model,
                                       temperature=temperature, seed=seed,
                                       timeout=timeout),
            name="qwen38", attempts=attempts)

    @property
    def identity(self) -> str:
        """The transport/model identity that MUST enter the prereg content hash:
        which model, at which endpoint, under which decode. Read from params/env
        HERE (fixed before the campaign hashes), never smuggled at call time."""
        return (f"qwen38(model={self._model},base={self._base},"
                f"temp={self._temperature},seed={self._seed},attempts={self._attempts})")

    def available(self, timeout: float = 3.0) -> bool:
        """One GET /models decides whether the model can be reached at all."""
        try:
            with urllib.request.urlopen(f"{self._base}/models", timeout=timeout):
                return True
        except OSError:
            return False

    def propose(self, brief: Mapping) -> dict:
        # No campaign evidence in the brief (e.g. the doctor's shape brief):
        # nothing to propose from. Shape-valid, no network touched.
        if "traces" not in brief:
            return {"rule": None}
        if not self.available():
            print(f"qwen reasoner: {self._base} unreachable (GPU held?); "
                  "skipping this proposal", file=sys.stderr)
            return {"rule": None}
        prereg = brief["prereg"]
        # The recovery vocabulary rides the brief -- the rsi loop owns the
        # repertoire, a model card must not import a sibling plugin to read it
        # (tests/test_boundaries). parse_proposal rejects any recovery the brief
        # did not offer, so an empty vocabulary means every proposal is refused.
        rule = self._llm.propose(
            brief["traces"], brief["labels"], generation=brief["generation"],
            privilege_budget=prereg.critic_budget,
            recovery_sensor_sd=prereg.recovery_sensor_sd,
            strategies=tuple(brief.get("strategies", ())))
        return {"rule": rule}


def provider(**params: Any) -> QwenReasoner:
    return QwenReasoner(**params)
