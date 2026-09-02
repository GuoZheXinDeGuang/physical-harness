"""The executor contract: what a SkillRecord's ``policies[key]`` resolves to.

Two shapes drive a node. A :class:`StepExecutor` is polled ``act(obs)`` by the
harness loop (in-process stage drivers, the pi0.5 chunk driver over a socket);
a :class:`SegmentExecutor` owns the whole sub-goal behind ``run(spec, deadline_s)``
(an MCP service). Either way the harness verifies ``ensures`` itself -- an
executor's ``ok`` is a claim, the predicate is the evidence.

``normalize_handshake`` is the ONE shape ``task.verify.driver.handshake`` seals,
whatever the transport: ``{transport, ref, checkpoint_sha|None, unverified, ok,
meta}`` (``meta`` is the raw record -- reconcile output, MCP serverInfo -- kept
for evidence, never read by the harness).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

TRANSPORTS = ("inproc", "ssp", "mcp")


@runtime_checkable
class StepExecutor(Protocol):
    def handshake(self) -> dict: ...
    def reset(self) -> None: ...
    def act(self, obs: Any) -> Any: ...
    def done(self) -> bool: ...
    def diagnostics(self) -> dict: ...


@runtime_checkable
class SegmentExecutor(Protocol):
    def handshake(self) -> dict: ...
    def run(self, spec: Mapping, deadline_s: float) -> dict: ...   # -> {ok, diagnostics}


def is_segment(executor: Any) -> bool:
    """A segment executor runs the sub-goal itself; a step executor is polled."""
    return callable(getattr(executor, "run", None)) and not callable(getattr(executor, "act", None))


def normalize_handshake(transport: str, ref: str | None, meta: Mapping | None = None) -> dict:
    """The sealed handshake shape. ``checkpoint_sha`` is read off ``meta`` either
    flat or under ``metadata`` (the policy server's echo); unknown transport raises
    (fail loud at mount, never a mystery row in the chain)."""
    if transport not in TRANSPORTS:
        raise ValueError(f"unknown executor transport {transport!r}; known: {TRANSPORTS}")
    meta = dict(meta or {})
    sha = meta.get("checkpoint_sha") or (meta.get("metadata") or {}).get("checkpoint_sha")
    return {"transport": transport, "ref": ref, "checkpoint_sha": sha or None,
            "unverified": list(meta.get("unverified") or []),
            "ok": bool(meta.get("ok", True)), "meta": meta}


class InprocExecutor:
    """Defaults that make an in-process driver a :class:`StepExecutor` with one
    base class: ``done`` reads the driver's existing ``exhausted`` property."""

    def handshake(self) -> dict:
        return normalize_handshake("inproc", f"{type(self).__module__}:{type(self).__qualname__}")

    def reset(self) -> None:
        pass

    def done(self) -> bool:
        return bool(getattr(self, "exhausted", False))

    def diagnostics(self) -> dict:
        return {}
