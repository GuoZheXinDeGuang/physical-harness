"""Capability seams: the kernel's contract layer.

A Definition names a seam and the Protocol a provider must satisfy. Consumers
resolve by name and every resolution is accounted, so "which code depended on
what" is a logged fact rather than an import graph. Privileged capabilities
(simulator ground truth, hardware admin) draw on an explicit budget at resolve
time: the accounting idea the feature contract proved at the observation level,
lifted to the system level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class CapabilityError(RuntimeError):
    """Base class for kernel capability failures."""


class UnknownCapability(CapabilityError):
    """The name is not in the capability catalog."""


class DuplicateProvider(CapabilityError):
    """A provider is already mounted for this capability."""


class MissingProvider(CapabilityError):
    """Resolved a capability nothing has provided."""


class ContractViolation(CapabilityError):
    """The provider does not satisfy the capability's Protocol."""


class PrivilegeViolation(CapabilityError):
    """A privileged resolution would exceed the kernel's budget."""


_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


@dataclass(frozen=True, slots=True)
class Definition:
    """One seam: dotted name, required Protocol, and whether resolving it is privileged."""

    name: str
    contract: type
    doc: str
    privileged: bool = False

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"capability name must be dotted lowercase: {self.name!r}")
        if not getattr(self.contract, "_is_runtime_protocol", False):
            raise ValueError(
                f"{self.name}: contract must be a @runtime_checkable Protocol, "
                f"got {self.contract!r}"
            )
