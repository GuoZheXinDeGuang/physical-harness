"""EpisodeSpec: the system's common currency.

One frozen, picklable value describes an episode end to end -- seed, task,
policy, noise, and the provider refs that carry mount identity into spawn
workers. It lives in the kernel because every layer passes it and none may
own it. Field order and defaults are load-bearing: phase 1 archives and the
parity protocol both depend on them, and the field-order guard tests pin the
provider refs to the tail.

NOMINAL_SCHEDULE rides along as the spec's default schedule; it is Panda
vocabulary and moves to the embodiment plugin when the default becomes a
mount-supplied value (L2 rung H).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

NOMINAL_SCHEDULE: tuple[tuple[str, int], ...] = (
    ("above", 25), ("descend", 25), ("close", 12), ("lift", 38),
)


@dataclass(frozen=True, slots=True)
class EpisodeSpec:
    """Everything that makes one episode reproducible."""

    seed: int
    task: str = "lift"
    robot: str = "Panda"
    #: robosuite terminates at the horizon and refuses further steps. A governed
    #: episode can splice several recovery programs into its schedule, so this
    #: must cover base + max_generations * program length, not just the nominal 100.
    horizon: int = 900
    #: Std-dev of the frozen policy's one-shot cube-pose percept. The difficulty knob.
    percept_noise: float = 0.020
    #: Std-dev of joint-space reset noise; robosuite's own initialization randomness.
    arm_noise: float = 0.02
    kp: float = 8.0
    #: Frozen policy: "scripted", or a path to cloned MLP weights. The policy is a
    #: black box either way; only the harness knows which one it is.
    policy: str = "scripted"
    schedule: tuple[tuple[str, int], ...] = NOMINAL_SCHEDULE
    #: Added for the second embodiment: robots differ in where the eef site
    #: sits relative to the fingertips (Sawyer's is ~1cm off Panda's, measured
    #: round 60), so descend/close goals take a per-spec vertical correction.
    #: 0.0 reproduces the Panda behaviour bit for bit.
    grasp_height_offset: float = 0.0
    #: L0 capability-seam dispatch: "module:factory" refs for embodiment.env and
    #: policy.driver (see harness/registry.py). None keeps the pre-kernel path
    #: byte-identical. These travel as strings rather than module-global hooks
    #: because a hook does not survive multiprocessing spawn -- measured in
    #: phase 1, see docs/design/observability.md -- while a string pickles,
    #: content-hashes, and audits cleanly. Appended at the end: this codebase's
    #: dataclasses require new fields last, defaulted (see ARCHITECTURE.md).
    env_provider: str | None = None
    policy_provider: str | None = None
    percept_provider: str | None = None

    def child(self, **kw) -> EpisodeSpec:
        return replace(self, **kw)
