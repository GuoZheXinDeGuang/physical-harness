"""The frozen-policy seam, and the recovery hand-back contract.

Two providers
-------------
``ScriptedDriver`` is the four-phase open-loop policy the project has used since
round 1: one noisy percept at t=0, fixed phase durations, no retry.

``ClonedDriver`` is a behaviour-cloned MLP (governor/bc.py) running closed loop.
Its errors come from the fit rather than from a schedule anyone wrote, which is
what makes it the honest test of whether this harness only finds failures its
author designed.

Hand-back
---------
Both are black boxes, so a recovery cannot re-target them from the inside. A
fired critic hands control to a scripted recovery actor for the length of its
program and then hands it back -- Zetta's VLA re-entry contract
(Zetta-Embodiment/zetta/evolution/models.py, ``RecoveryRule``). Recovery steps do
not consume the policy's own clock: the scripted driver resumes at the phase it
was interrupted in, and the cloned driver resumes from a reset clock, because a
clock beyond its training horizon is out of distribution and would be a second,
uncontrolled failure source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol

import numpy as np

from harness.registry import load_provider
from harness.skill_executor import InprocExecutor
from harness.spec import PHASE_HEIGHT, STACK_PHASE_HEIGHT, STACK_SCHEDULE, EpisodeSpec


def _object_key(spec):
    """Which observation key holds the target: contract-first, legacy fallback.

    Same shape as the rsi rollout's embodiment access: a spec with a provider
    ref answers through the EnvProvider contract; the no-ref legacy path goes
    through the governor shim, the one documented default-binding point.
    """
    if spec.env_provider:
        from harness.registry import load_provider

        return load_provider(spec.env_provider).object_key(spec)
    import governor.env as legacy

    return legacy.object_key(spec)

def _percept_rng(spec: EpisodeSpec) -> np.random.RandomState:
    """The seed-derived percept-noise stream, shared by every t=0 percept.

    RandomState only accepts seeds in [0, 2**32); the derivation is wrapped
    because any spec.seed >= 542479 otherwise overflows it (M6 round 107:
    inventory_smoke's own --seed 990000 default crashed the first manipulate
    node). Wrapping is value-identical for every seed below that, so sealed
    runs replay bit-for-bit.
    """
    return np.random.RandomState((spec.seed * 7919 + 11) % (2**32))


@dataclass(slots=True)
class FrozenPolicy:
    """The black-box policy under governance. Never learns, never retries."""

    spec: EpisodeSpec
    target: np.ndarray = field(default=None, repr=False)

    def observe_once(self, obs: Mapping[str, np.ndarray]) -> np.ndarray:
        """Take the single noisy percept the policy will act on for the whole episode."""
        rng = _percept_rng(self.spec)
        sd = self.spec.percept_noise
        self.target = np.asarray(obs[_object_key(self.spec)]).copy() + np.array(
            [rng.normal(0, sd), rng.normal(0, sd), 0.0]
        )
        return self.target

    def act(self, obs: Mapping[str, np.ndarray], phase: str) -> np.ndarray:
        """One 7-dof OSC_POSE action toward the phase goal, from the stale percept."""
        height = PHASE_HEIGHT[phase]
        if phase in ("descend", "close"):
            height += self.spec.grasp_height_offset
        goal = np.array([self.target[0], self.target[1], self.target[2] + height])
        delta = np.clip((goal - np.asarray(obs["robot0_eef_pos"])) * self.spec.kp, -1, 1)
        grip = 1.0 if phase in ("close", "lift") else -1.0
        return np.array([*delta, 0.0, 0.0, 0.0, grip])


def phase_at(schedule: tuple[tuple[str, int], ...], t: int) -> str | None:
    """Phase owning control step `t`, or None once the schedule is exhausted."""
    acc = 0
    for name, dur in schedule:
        if t < acc + dur:
            return name
        acc += dur
    return None


class PolicyDriver(Protocol):
    """A frozen policy. `act` is called only on steps the policy owns."""

    def observe_once(self, obs) -> np.ndarray: ...
    def act(self, obs) -> np.ndarray: ...
    def retarget(self, target: np.ndarray) -> None: ...
    def on_handback(self) -> None: ...
    @property
    def exhausted(self) -> bool: ...
    @property
    def identity(self) -> str: ...


class ScriptedDriver(InprocExecutor):
    """The four-phase open-loop policy, driven by its own phase counter."""

    def __init__(self, spec: EpisodeSpec) -> None:
        self.spec = spec
        self.inner = FrozenPolicy(spec)
        self.k = 0                      # policy-owned steps; recovery does not advance it

    def observe_once(self, obs) -> np.ndarray:
        return self.inner.observe_once(obs)

    def act(self, obs) -> np.ndarray:
        phase = phase_at(self.spec.schedule, self.k) or self.spec.schedule[-1][0]
        action = self.inner.act(obs, phase)
        self.k += 1
        return action

    def retarget(self, target: np.ndarray) -> None:
        self.inner.target = target

    def on_handback(self) -> None:
        """Skip the remainder of the interrupted phase.

        A recovery is a complete regrasp ending in close-and-lift, so the phase
        it interrupted has been superseded -- resuming the tail of a `close`
        that the repair already performed re-closes on an object now held.
        Measured on 200 held-out seeds: superseding scores +27.5pp, resuming
        scores +14.0pp with four regressions. The earlier splice implementation
        had this behaviour implicitly, by dropping the rest of the phase it
        broke out of; making it explicit is what let it be compared at all.
        """
        acc = 0
        for _name, dur in self.spec.schedule:
            acc += dur
            if self.k < acc:
                self.k = acc
                return

    @property
    def exhausted(self) -> bool:
        return self.k >= sum(d for _, d in self.spec.schedule)

    @property
    def identity(self) -> str:
        return "scripted@v1"


@dataclass(frozen=True, slots=True)
class GraspPose:
    """One geometric grasp, as the grasp backend returns it.

    ``position`` is the world point the fingers close on -- the only field the
    open-loop phase vocabulary consumes. ``yaw`` (closing-axis heading, radians)
    and ``width`` (jaw opening, metres) are carried for logging and the frame
    cross-check: the scripted OSC action leaves the three rotation channels at
    zero, so on a 4-fold-symmetric cube the yaw is never applied to the wrist.
    """

    position: np.ndarray
    yaw: float = 0.0
    width: float = 0.0


class GraspPoseDriver(ScriptedDriver):
    """:class:`ScriptedDriver` retargeted onto a geometric grasp pose.

    The four-phase open-loop mechanics are inherited verbatim -- same
    ``FrozenPolicy.act``, same phase clock, same hand-back. The ONLY change is
    the target SOURCE: :meth:`observe_once` locks onto a grasp position computed
    once at t=0 from the camera cloud (``propose`` in grasp_geometric.py),
    ignoring the privileged object percept obs would otherwise hand the scripted
    driver. This is the first perception-driven grasp: no privileged object pose
    in the control loop, only observed geometry.
    """

    def __init__(self, spec: EpisodeSpec, grasp: GraspPose) -> None:
        super().__init__(spec)
        self.grasp = grasp

    def observe_once(self, obs) -> np.ndarray:
        """Lock the target onto the geometric grasp position, not obs's percept."""
        self.inner.target = np.asarray(self.grasp.position, dtype=float).copy()
        return self.inner.target

    @property
    def identity(self) -> str:
        return "grasp_pose@v1"


class ClonedDriver:
    """A behaviour-cloned MLP running closed loop."""

    #: Clock horizon the clone was trained over.
    HORIZON = 100

    def __init__(self, spec: EpisodeSpec, weights: Path) -> None:
        from plugins.policies.bc import MLPPolicy

        self.spec = spec
        self.net = MLPPolicy.load(Path(weights))
        self.percept = FrozenPolicy(spec)
        self.k = 0

    def observe_once(self, obs) -> np.ndarray:
        return self.percept.observe_once(obs)

    def act(self, obs) -> np.ndarray:
        from plugins.policies.bc import encode

        action = self.net.act(encode(obs, self.percept.target, self.k / self.HORIZON))
        self.k += 1
        return action

    def retarget(self, target: np.ndarray) -> None:
        self.percept.target = target

    def on_handback(self) -> None:
        """Resume in the regime the recovery advanced the task to, not at zero.

        The recovery ends holding the object aloft. Restarting the clock puts the
        clone back in its approach regime with the gripper opening, which undoes
        the repair -- measured at -2.0pp with 3 fixed and 7 broken. This is the
        same error the scripted driver's hand-back already avoids by superseding
        the interrupted phase rather than resuming it.

        The clock is set to where the demonstrator's own schedule enters `lift`,
        which is the phase the recovery program terminates in. That mapping comes
        from the demonstrations the clone was trained on, not from tuning.
        """
        acc = 0
        for name, dur in self.spec.schedule:
            if name == "lift":
                self.k = acc
                return
            acc += dur
        self.k = acc

    @property
    def exhausted(self) -> bool:
        return self.k >= self.HORIZON

    @property
    def identity(self) -> str:
        return f"cloned@{self.net.sha()[:12]}"


class StackScriptedDriver:
    """Eight-phase open-loop stack policy: grasp cubeA, seat it on cubeB.

    Same difficulty knob as ScriptedDriver -- act on a wrong percept,
    recoverable but uncorrected -- extended to TWO targets, both read ONCE at
    t=0 (cubeB is static, so a t=0 read is as good as any later one; cubeA
    after grasp is known by proprioception). Reachable only via the
    `plugins.policies:stack_scripted_provider` ref; the lift path never sees
    this class.
    """

    #: The grasp half reuses the lift vocabulary verbatim; the place half
    #: rides the stack extension (harness/spec_tabletop.py).
    _HEIGHT: ClassVar[dict[str, float]] = {**PHASE_HEIGHT, **STACK_PHASE_HEIGHT}
    _GRASP_PHASES = ("above", "descend", "close", "lift")
    _GRIP_CLOSED = ("close", "lift", "over_b", "place")

    def __init__(self, spec: EpisodeSpec) -> None:
        self.spec = spec
        self.k = 0                      # policy-owned steps; recovery does not advance it
        self.target_a: np.ndarray | None = None
        self.target_b: np.ndarray | None = None
        self._place_recovery = False    # armed by retarget_place, consumed by on_handback

    def observe_once(self, obs) -> np.ndarray:
        """One noisy percept of BOTH cubes: xy noised, z exact, two consecutive
        draws from the one seed-derived stream so the cube errors are independent."""
        rng = _percept_rng(self.spec)
        sd = self.spec.percept_noise
        self.target_a = np.asarray(obs[_object_key(self.spec)]).copy() + np.array(
            [rng.normal(0, sd), rng.normal(0, sd), 0.0]
        )
        self.target_b = np.asarray(obs["cubeB_pos"]).copy() + np.array(
            [rng.normal(0, sd), rng.normal(0, sd), 0.0]
        )
        return self.target_a

    def act(self, obs) -> np.ndarray:
        phase = phase_at(STACK_SCHEDULE, self.k) or STACK_SCHEDULE[-1][0]
        target = self.target_a if phase in self._GRASP_PHASES else self.target_b
        height = self._HEIGHT[phase]
        if phase in ("descend", "close"):
            height += self.spec.grasp_height_offset
        goal = np.array([target[0], target[1], target[2] + height])
        delta = np.clip((goal - np.asarray(obs["robot0_eef_pos"])) * self.spec.kp, -1, 1)
        grip = 1.0 if phase in self._GRIP_CLOSED else -1.0
        self.k += 1
        return np.array([*delta, 0.0, 0.0, 0.0, grip])

    def retarget(self, target: np.ndarray) -> None:
        # Grasp-stage regrasp: retargets cubeA, resumes into the place half.
        self.target_a = target

    def retarget_place(self, target: np.ndarray) -> None:
        """A place-shaped recovery re-estimated cubeB (the rung-3 target_b
        retarget deferred at :meth:`retarget`) and will perform the whole place
        half itself. Point the resumed phases at the same fresh estimate, and arm
        on_handback to resume PAST place so it does not re-place a seated cube."""
        self.target_b = target
        self._place_recovery = True

    def on_handback(self) -> None:
        """Resume after a recovery hands control back.

        A grasp recovery re-grasped: supersede only the interrupted phase, as
        ScriptedDriver does. A place recovery (replace) already ran
        lift->over_b->place->release->retreat itself, so resuming into the
        interrupted place phase would re-place an already-seated cube. Resume at
        `retreat` instead -- past place, aimed at the fresh target_b -- mirroring
        ClonedDriver's "resume where the terminal phase begins" mapping."""
        if self._place_recovery:
            self._place_recovery = False
            acc = 0
            for name, dur in STACK_SCHEDULE:
                if name == "retreat":
                    self.k = acc
                    return
                acc += dur
            self.k = acc
            return
        acc = 0
        for _name, dur in STACK_SCHEDULE:
            acc += dur
            if self.k < acc:
                self.k = acc
                return

    @property
    def exhausted(self) -> bool:
        return self.k >= sum(d for _, d in STACK_SCHEDULE)

    @property
    def identity(self) -> str:
        return "stack_scripted@v1"


class StackSkillDriver:
    """Expose the atomic Stack controller as persistent ``grasp`` / ``place_on``.

    The two graph nodes share one :class:`StackScriptedDriver`, one environment,
    and one observation stream.  Splitting only changes where execution pauses
    and which predicate is reported; it does not introduce a second controller
    or reset the world between grasp and placement.
    """

    _GRASP_END = sum(
        duration for name, duration in STACK_SCHEDULE
        if name in StackScriptedDriver._GRASP_PHASES
    )
    _TOTAL_END = sum(duration for _, duration in STACK_SCHEDULE)

    def __init__(self, spec: EpisodeSpec) -> None:
        self.spec = spec
        self.inner = StackScriptedDriver(spec)
        self._segment: str | None = None
        self._segment_end = 0
        self._start_z: float | None = None
        self._latest_obs = None

    def observe_once(self, obs) -> np.ndarray:
        self._latest_obs = obs
        self._start_z = float(np.asarray(obs["cubeA_pos"])[2])
        return self.inner.observe_once(obs)

    def enter_segment(self, env, spec: EpisodeSpec) -> None:
        del env
        if spec.task == "grasp_cubeA":
            self._segment = "grasp"
            self.inner.k = 0
            self._segment_end = self._GRASP_END
            return
        if spec.task == "place_cubeA_on_cubeB":
            self._segment = "place_on"
            self.inner.k = self._GRASP_END
            self._segment_end = self._TOTAL_END
            return
        raise ValueError(
            f"stack skill driver cannot execute {spec.task!r}; expected "
            "'grasp_cubeA' or 'place_cubeA_on_cubeB'"
        )

    def act(self, obs) -> np.ndarray:
        self._latest_obs = obs
        return self.inner.act(obs)

    def retarget(self, target: np.ndarray) -> None:
        self.inner.retarget(target)

    def on_handback(self) -> None:
        self.inner.on_handback()

    @property
    def exhausted(self) -> bool:
        return self.inner.k >= self._segment_end

    def segment_success(self, env) -> bool:
        if self._segment == "grasp":
            if self._latest_obs is None or self._start_z is None:
                return False
            z = float(np.asarray(self._latest_obs["cubeA_pos"])[2])
            q = np.asarray(self._latest_obs["robot0_gripper_qpos"])
            return bool(z > self._start_z + 0.04 and abs(q[0] - q[1]) > 0.01)
        if self._segment == "place_on":
            # The simulator's contact-aware Stack predicate: cubeA is lifted,
            # touches cubeB, and is no longer held by the gripper.
            return bool(env._check_success())
        return False

    def segment_diagnostics(self, env) -> dict:
        return {
            "segment": self._segment,
            "policy_step": self.inner.k,
            "stack_success": bool(env._check_success()),
        }

    @property
    def identity(self) -> str:
        return "stack_skill_graph@v1"


def _default_make_driver(spec: EpisodeSpec) -> PolicyDriver:
    """Resolve the spec's frozen policy."""
    if spec.policy in (None, "", "scripted"):
        return ScriptedDriver(spec)
    return ClonedDriver(spec, Path(spec.policy))

def make_driver(spec: EpisodeSpec) -> PolicyDriver:
    """Resolve the spec's frozen policy driver.

    Dispatch point for the policy.driver capability seam: when `spec.policy_provider`
    names a provider ("module:factory"), it is loaded via
    `harness.registry.load_provider` and asked to build the driver. With no ref,
    this falls back to `_default_make_driver`, the original scripted/cloned
    dispatch -- so a spec with no ref behaves byte-identically to before this
    seam existed. Same string-not-hook rationale as `governor.env.make_env`.
    """
    ref = spec.policy_provider
    if ref is not None:
        provider = load_provider(ref)
        return provider.make_driver(spec)
    return _default_make_driver(spec)
