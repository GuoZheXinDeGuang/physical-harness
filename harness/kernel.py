"""The kernel: mount providers, resolve capabilities, account every resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from harness.capability import (ContractViolation, Definition, DuplicateProvider,
                                MissingProvider, PrivilegeViolation,
                                UnknownCapability)
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
        self._providers: dict[str, tuple[Any, str]] = {}
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

    # -- mounting -----------------------------------------------------------
    def provide(self, name: str, provider: Any, *, ref: str) -> None:
        definition = self._defs.get(name)
        if definition is None:
            raise UnknownCapability(name)
        if name in self._providers:
            raise DuplicateProvider(name)
        if not isinstance(provider, definition.contract):
            raise ContractViolation(
                f"{ref} does not satisfy {definition.contract.__name__} for {name}")
        self._providers[name] = (provider, ref)
        if self._log is not None:
            self._log.append("capability.provide",
                             {"capability": name, "ref": ref,
                              "privileged": definition.privileged})

    def mount(self, plan: MountPlan) -> None:
        for m in plan.mounts:
            self.provide(m.capability, load_provider(m.provider, m.params),
                         ref=m.provider)
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
        provider, ref = self._providers[name]
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

    def resolutions(self) -> tuple[Resolution, ...]:
        return tuple(self._resolutions)

    def privileged_used(self) -> frozenset[str]:
        return frozenset(self._privileged_used)
