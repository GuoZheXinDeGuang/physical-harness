"""Governor policies: the reference PolicyFactory for capability `policy.driver`.

Thin adapter, on purpose. The scripted/cloned dispatch stays exactly where it
was -- `plugins.policies.drivers._default_make_driver` -- so mounting this provider
through the kernel and calling `plugins.policies.drivers.make_driver` with no ref must
resolve the identical driver class as before this seam existed. See
governor/policy.py's `make_driver` docstring for the dispatch this plugin is
mounted behind.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

import plugins.policies.drivers as _policy
from harness.registry import load_provider

#: Sawyer adaptation of the scripted policy, measured in round 60: the arm
#: converges slower than Panda's under the same OSC gains (double the
#: above/descend dwell, raise kp), and its eef site sits ~1cm differently from
#: its fingertips (probe: height offset -0.010 took 1/12 transient grasps to 9/12; -0.020
#: overshot back to 4/12). Under the gate's END-of-episode criterion the best
#: variant so far (longer close, gentler kp) reaches ~21% -- the residual
#: failure is grip retention during lift, which is layer-3 policy work, not
#: harness work. A lateral-offset hypothesis was probed and rejected (y shifts
#: of -7/-14/-21mm all scored worse than 0). Constants live HERE, in the provider whose ref
#: is preregistered, not in a module global nobody hashes.
SAWYER_SCHEDULE: tuple[tuple[str, int], ...] = (
    ("above", 50), ("descend", 50), ("close", 24), ("lift", 50),
)
SAWYER_KP = 8.0
SAWYER_GRASP_HEIGHT_OFFSET = -0.010


class GovernorPolicies:
    """Layer 3 `harness.contracts.PolicyFactory`, backed by plugins.policies.drivers verbatim.

    `overrides=None` is the byte-identical legacy path. A variant factory bakes
    spec overrides in (schedule, gains, grasp height); the overrides are code in
    a preregistered provider ref, never mount params, because params cannot
    reach spawn workers.
    """

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self.overrides = dict(overrides or {})

    def make_driver(self, spec: Any) -> Any:
        if self.overrides:
            spec = dataclasses.replace(spec, **self.overrides)
        return _policy._default_make_driver(spec)


def provider() -> GovernorPolicies:
    return GovernorPolicies()


def sawyer_scripted_provider() -> GovernorPolicies:
    """The scripted policy re-tuned for the Sawyer embodiment."""
    return GovernorPolicies({"schedule": SAWYER_SCHEDULE, "kp": SAWYER_KP,
                             "grasp_height_offset": SAWYER_GRASP_HEIGHT_OFFSET})


class StackPolicies:
    """`harness.contracts.PolicyFactory` for the R2 stack baseline.

    A 3-line factory, not a GovernorPolicies variant: the driver class itself
    carries STACK_SCHEDULE, so no spec override machinery is needed.
    """

    def make_driver(self, spec: Any) -> Any:
        return _policy.StackScriptedDriver(spec)


def stack_scripted_provider() -> StackPolicies:
    """The eight-phase scripted stack policy (grasp cubeA, seat it on cubeB)."""
    return StackPolicies()


#: Where a geometric grasp pose comes from: a ref, not an import. The grasp math
#: (camera cloud -> top-down PCA pose) belongs to the embodiment card; reaching it
#: by string keeps this card from importing a sibling (tests/test_boundaries.py),
#: the same sanctioned crossing episode specs use for env/policy provider refs.
GEOMETRIC_GRASP_REF = "plugins.embodiment_robosuite.grasp_geometric:geometric_grasp_provider"


class LiftGeometricPolicies:
    """`harness.contracts.PolicyFactory`: the first perception-driven grasp, carded.

    ``make_driver`` computes one geometric grasp for the spec (resolved by ref, so
    no sibling import), then returns a :class:`GraspPoseDriver` locked onto it --
    the four-phase open-loop vocabulary driven toward an OBSERVED grasp, with no
    privileged object pose ever in the control loop (round 93). The grasp source
    is a ref, not a mount param, because refs are the only thing that survives the
    spawn to a rollout worker (same rule as env/policy provider refs).
    """

    def __init__(self, grasp_ref: str = GEOMETRIC_GRASP_REF) -> None:
        self.grasp_ref = grasp_ref

    def make_driver(self, spec: Any) -> Any:
        pose = load_provider(self.grasp_ref).grasp_pose(spec)
        grasp = _policy.GraspPose(position=np.asarray(pose["position"], dtype=float),
                                  yaw=float(pose["yaw"]), width=float(pose["width"]))
        return _policy.GraspPoseDriver(spec, grasp)


def lift_geometric_provider() -> LiftGeometricPolicies:
    """The geometric-grasp policy for the ``lift_geometric`` task binding."""
    return LiftGeometricPolicies()
