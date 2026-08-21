"""The kernel: mount providers, resolve capabilities, account every resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from harness.capability import (
    ContractViolation,
    Definition,
    DuplicateProvider,
    MissingProvider,
    PrivilegeViolation,
    UnknownCapability,
)
from harness.config import MountPlan
from harness.events import SessionLog
from harness.registry import load_provider


@dataclass(frozen=True, slots=True)
class Resolution:
    """One accounted act of dependency: who resolved what, through which provider."""

    seq: int
    capability: str
    provider_ref: str
    consumer: str
    privileged: bool


class Kernel:
    def __init__(self, definitions: Iterable[Definition], *,
                 log: SessionLog | None = None,
                 privilege_budget: int | None = None) -> None:
        self._defs: dict[str, Definition] = {}
        for d in definitions:
            if d.name in self._defs:
                raise ValueError(f"duplicate capability definition: {d.name}")
            self._defs[d.name] = d
        self._providers: dict[str, tuple[Any, str, dict]] = {}
        self._resolutions: list[Resolution] = []
        self._privileged_used: set[str] = set()
        self._log = log
        self._budget = privilege_budget

    # -- catalog ------------------------------------------------------------
    def definitions(self) -> tuple[Definition, ...]:
        return tuple(self._defs.values())

    def provider_ref(self, name: str) -> str:
        if name not in self._providers:
            raise MissingProvider(f"{name} has no provider mounted")
        return self._providers[name][1]

    def provider_params(self, name: str) -> dict:
        """The Mount params the provider was constructed with.

        Workers reconstruct providers from the bare ref string, which cannot
        carry params; callers that stamp refs onto specs must check this is
        empty and fail loudly instead of letting params silently vanish.
        """
        if name not in self._providers:
            raise MissingProvider(f"{name} has no provider mounted")
        return dict(self._providers[name][2])

    # -- mounting -----------------------------------------------------------
    def provide(self, name: str, provider: Any, *, ref: str,
                params: dict | None = None) -> None:
        definition = self._defs.get(name)
        if definition is None:
            raise UnknownCapability(name)
        if name in self._providers:
            raise DuplicateProvider(name)
        if not isinstance(provider, definition.contract):
            raise ContractViolation(
                f"{ref} does not satisfy {definition.contract.__name__} for {name}")
        self._providers[name] = (provider, ref, dict(params or {}))
        if self._log is not None:
            self._log.append("capability.provide",
                             {"capability": name, "ref": ref,
                              "params": dict(sorted((params or {}).items())),
                              "privileged": definition.privileged})

    def mount(self, plan: MountPlan) -> None:
        for m in plan.mounts:
            self.provide(m.capability, load_provider(m.provider, m.params),
                         ref=m.provider, params=dict(m.params))
        if self._log is not None:
            self._log.append("kernel.mount",
                             {"plan_sha": plan.sha(),
                              "capabilities": [m.capability for m in plan.mounts]})

    # -- resolution (always accounted) --------------------------------------
    def resolve(self, name: str, *, consumer: str) -> Any:
        definition = self._defs.get(name)
        if definition is None:
            raise UnknownCapability(name)
        if name not in self._providers:
            raise MissingProvider(f"{name} has no provider mounted")
        provider, ref, _params = self._providers[name]
        if definition.privileged:
            would_use = self._privileged_used | {name}
            if self._budget is not None and len(would_use) > self._budget:
                raise PrivilegeViolation(
                    f"resolving {name} would use {len(would_use)} privileged "
                    f"capabilities against a budget of {self._budget}")
            self._privileged_used.add(name)
        record = Resolution(len(self._resolutions), name, ref, consumer,
                            definition.privileged)
        self._resolutions.append(record)
        if self._log is not None:
            self._log.append("capability.resolve",
                             {"capability": name, "ref": ref, "consumer": consumer,
                              "privileged": definition.privileged})
        return provider

    def note(self, kind: str, data: dict) -> None:
        """Let a workload write into the SAME chained ledger kernel events use.

        This is what makes the session log a system ledger rather than a kernel
        diary: a campaign's completion, its preregistration sha and the skills
        it published sit in one verifiable chain with the mounts and
        resolutions that produced them. No log mounted means silently no-op --
        note() is evidence, never control flow.
        """
        if self._log is not None:
            self._log.append(kind, data)

    def resolutions(self) -> tuple[Resolution, ...]:
        return tuple(self._resolutions)

    def privileged_used(self) -> frozenset[str]:
        return frozenset(self._privileged_used)
