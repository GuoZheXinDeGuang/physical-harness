"""Backbone harness kernel: capability seams, config layering, chained events.

The kernel imports no plugin and no legacy governor code; that boundary is
enforced by a test, not by convention.
"""
from harness.capability import (CapabilityError, ContractViolation, Definition,
                                DuplicateProvider, MissingProvider,
                                PrivilegeViolation, UnknownCapability)
from harness.config import Bundle, Mount, MountPlan, Patch, Profile, resolve_plan
from harness.events import SessionLog
from harness.kernel import Kernel, Resolution

__all__ = ["Bundle", "CapabilityError", "ContractViolation", "Definition",
           "DuplicateProvider", "Kernel", "MissingProvider", "Mount",
           "MountPlan", "Patch", "PrivilegeViolation", "Profile", "Resolution",
           "SessionLog", "UnknownCapability", "resolve_plan"]
