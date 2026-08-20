======================================================================
# VERSION 1
======================================================================

## QUESTION
What is the capability seam set for the Governor physical harness (Python), and what is the concrete Python mechanism for provider registration, reversal, resolution, and composition configuration?

## DECISION
TEN seams, each a `Service` subclass owning one `Context` slot, plus one non-seam driver (`EpisodeRunner`) that consumes six of them.

1. `env` — **Environment**: seeded world, raw grouped observations, physical-reset identity. Providers: `RobosuiteEnvironment` (day one) / `ReplayEnvironment` (proves the seam; replays a stored episode's actions+states with no simulator). Consumer: `EpisodeRunner`.
2. `features` — **FeatureProvider**: the privilege boundary. Raw obs -> flat scalar `FeatureFrame` where every name carries a declared `PrivilegeTier`. Providers: `RobosuiteFeatures` (day one) / `MaskedFeatures(inner, max_tier)` (proves the seam AND is the mechanism behind the privilege-ablation gate; it *drops* over-tier names rather than zeroing them) / `ReplayFeatures`. Consumers: `EpisodeRunner`, `CompiledCritic`, `PrivilegeAblationGate`.
3. `policy` — **BasePolicy**: the frozen black box, identified by a content hash. Providers: `OpenLoopScriptedPolicy` (day one, the noisy-single-read four-phase policy from docs/difficulty-calibration.md) / `BCCheckpointPolicy` (frontier #2) / `ReplayPolicy` (proves the seam; makes shadow replay and determinism tests run with no policy at all).
4. `critic_runtime` — **CriticRuntime**: compiles a `CriticBundle`, evaluates it per control step, and **measures which features it actually read**. Providers: `RuleCriticRuntime` (day one, Zetta-style feature/op/threshold/dwell/cooldown, pure Python) / `SandboxedPythonCriticRuntime` (persistent seatbelt-confined worker, precompiled code object per tick — frontier #1). Consumers: `EpisodeRunner`, `ShadowReplayValidator`.
5. `recovery` — **RecoveryExecutor**: turns a verdict into a bounded action sequence. Providers: `ScriptedRecoveryExecutor` (day one: open → re-read current cube pose → re-approach) / `ResidualRecoveryExecutor` (non-preempting delta on the base action — proves the seam by forcing the arbitration contract into the Definition) / `NoopRecoveryExecutor` (detection-only ablation arm).
6. `store` — **EpisodeStore**: append-only event log (`seq == len`) + content-addressed blob store. Providers: `JsonlBlobStore` (day one) / `ZstdShardedStore` (proves the seam at 50 seeds × 4 gates × 100 steps; `zstandard` is already a dependency) / `InMemoryStore`. Consumers: everything.
7. `rollout` — **RolloutExecutor**: batch dispatch of episode jobs, and the *only* place the valid/infra-invalid distinction is made. Providers: `InlineRolloutExecutor` (day one, serial, debuggable) / `ProcessPoolRolloutExecutor` (10 workers, 212 eps/min measured — proves the seam by forcing every provider to be constructible from pure-data config in a fresh interpreter).
8. `analyzer` — **FailureAnalyzer**: segment + cluster failed episodes. Providers: `DeterministicAnalyzer` (hard partition on `(failure_class, stage, tool)` + complete-link agglomeration) / `LlmClusterReviewer(inner)` (proves the seam; must carry `deterministic_source_sha256` of the report it replaces).
9. `proposer` — **CandidateProposer**: one delta critic rule + recovery binding + declared privilege budget, per target cluster. Providers: `SearchProposer` (day one, zero external API — this is what makes GOAL.md's "演化循环必须能在零外部 API 下跑完" a structural property, not a promise) / `LlmProposer` (Anthropic structured output) / `FixtureProposer`.
10. `gates` — **Gate** (the one *registry-shaped* seam: many providers mounted at once, keyed by `kind`). Providers: `SameSeedGate`, `RegressionGate`, `HeldoutMcNemarGate`, and Governor's own `PrivilegeAblationGate`.

**Mechanism.** A `Context` dataclass with one typed `Optional` slot per seam plus eager registries (`gates`, `settings`, `invariants`). Registration is `ctx.provide(service)`, which asserts the slot is empty, sets it, and returns a `Disposer`; inside a mount the disposer is auto-pushed onto that mount's `contextlib.ExitStack`. **Reversal** = `Harness.unmount(row_id)` closing that row's ExitStack (LIFO by ExitStack contract) — dsh's "registrations are effects" with `ExitStack` standing in for the Cordis fiber. **Resolution** is `ctx.require("features")` — an explicit accessor that raises `SeamUnavailable`, never a `None` deref, never a dependency-graph solver. **Composition** is an ordered flat list of pure-data rows `{id, factory: "pkg.mod:setup", config, disabled}` folded over layers `bundle → profile → patch files → CLI --set`, where an id-targeted patch does **whole-field replace** (`row[k] = v`, never deep merge) and an explicit `insert: true` row appends; a patch naming an unknown id is a hard error. `governor config --dump` and `governor run` call the identical `compose()`. The composed tree is canonical-JSON hashed into `config_sha256`, written into every episode header and the campaign manifest, and is the only thing that crosses a worker-process boundary.

## RATIONALE
**Why these ten, and why not more.** dsh's rule is that a seam is complete only as Definition/Provider/Consumer and is split only when the roles evolve independently (deepseek-harness docs/capability-seams.md, CLAUDE.md). Each of the ten has at least one provider that differs from the day-one one in *kind*, not detail — replay vs simulate, masked vs full, interpreted vs confined-process, inline vs pool, search vs LLM. Anything I could not name such a pair for, I folded in.

**The privilege budget is a seam property, not a lint.** GOAL.md's stated divergence from Zetta is that Zetta reads `privileged.*.residual_to_success` and forbids hidden-control only in prose — "没有机制". The mechanism is three-layered: `FeatureProvider.schema()` tags every scalar with a `PrivilegeTier` (robosuite already groups observations exactly along this line per docs/verified-environment.md — `robot0_proprio-state` is real-robot-measurable, `object-state` is sim-only); `CriticRuntime` hands the compiled critic a *recording view* so `CriticEval.features_read` is measured rather than declared; `PrivilegeAblationGate` re-runs the same-seed plan with `MaskedFeatures(max_tier=PROPRIO)` substituted, and the gate is the authority. `MaskedFeatures` drops names instead of zeroing them, mirroring Zetta shadow_replay.py's asymmetric KeyError policy: a critic that needed a masked feature fails loudly as `missing_feature`, it does not silently read 0.0 and "pass" the ablation.

**Failures are fields; misuse raises.** Every per-step critic outcome is a `CriticEval.error` field on a returned value, never an exception — dsh packages/code-runtime/code-runtime/src/types.ts:79-124 ("error is a FIELD on a resolved result, never a rejection"). `CriticRuntime.compile()` and `CandidateProposer.propose()` raise only for contract misuse. This keeps the control-loop body a branch on `eval.error.kind` rather than exception-driven flow, and it is what lets the confined-process provider (whose failure modes are timeout/worker-exit, absent from the rule provider) share one Definition with the interpreted one from day one.

**Containment is reported, never a boolean.** `CriticRuntime.enforcement` is `"interpreted" | "in-process" | "confined-process"`, following dsh's `ConfinedArgv.enforcement` `full|partial` honesty (packages/sandbox/sandbox-local/src/index.ts:177-190). A `RuleCriticRuntime` claiming `sandboxed: true` would be a lie, and the honest three-value answer is what tells the reader of a promoted bundle how much to trust it.

**Two timestamps, blobs by reference.** `Event` carries `mono_ns` and `wall_ns` — dsh's single `Date.now()` is explicitly called out as a robotics antipattern — and large payloads go through `put_blob() -> sha256` with only the digest in the event, because dsh's `snapshotJsonValue` + `deepFreeze` (session/src/index.ts:604-655) is right for KB of text and catastrophic for arrays.

**Environment carries seed binding and reset identity in the Definition.** `reset(*, seed)`'s docstring pins the non-obvious semantics — the seed must bind where the world's own RNG is constructed (`suite.make(seed=N)`) — because `np.random.seed()` silently degrades every paired gate to a coin flip (verified, docs/verified-environment.md; commit d405186). `reset_identity() -> sha256` is Zetta gating._same_physical_reset's `state_sha256`, hoisted into the seam so the gate never reaches into robosuite.

**Why `rollout` is a seam though it was not required.** The process-pool provider is what forces `RolloutJob` to carry the composed config rows as pure data and return ids, never live objects. That constraint propagates back into every other seam (no closures, no open handles in config) and is checked at mount time. A seam whose second provider imposes a global design constraint has earned its keep.

**Why `EpisodeRunner` is deliberately NOT a seam.** It is dsh's agent-loop: one implementation, owner of the episode/step boundary discipline (open marker before any input is claimed, close marker in a `finally` with a structured reason, degraded reasons sticky per agent.ts:290) and of the "policy-visible IFF logged" invariant. Making it swappable would make those invariants unenforceable.

**Why the Cordis mechanism does not port.** Cordis buys reactive service-appearance, hot module reload of a plugin graph, and a dependency solver — payoffs for an open third-party ecosystem. Governor has ten first-party seams whose mount order is the config-row order. `ExitStack` + one `Optional` slot per seam is ~70 lines and delivers the one property that actually gets used: swap one provider under a fixed rest-of-the-world. That is used for real by the ablation gate and by every test, which is why disposers survived the cut and the fiber tree did not.

**Why the patch layer survives even though I cut most config ceremony.** `PrivilegeAblationGate` dispatches its arm by emitting one runtime patch row (`{"id": "features", "config": {"max_tier": 0}}`) onto the composed tree before submitting jobs. The composition layer is therefore not boot-only decoration; it is the runtime substitution mechanism, and whole-field replace (dsh vendor/include/src/index.ts:58-124) is what makes that substitution unambiguous. I made an unknown-id patch a hard error rather than dsh's warning: a typo'd id that silently no-ops would invalidate a whole campaign's ablation arm.

## REJECTED
- Cordis's fiber tree, ctx.reflect proxies, reactive ctx.inject([...], cb), and runtime dependency-graph solving (vendor/cordis/src/fiber.ts). Rejected: thousands of lines buying dynamic remount of an open plugin ecosystem. Ten statically-known seams mount in config-row order; a linear loop over rows plus ctx.require(slot) raising SeamUnavailable covers it. Kept only the derived property that gets used: registration returns a disposer.
- A generic typed event bus (waterfall/serial hook maps, dsh's agent/pre-step, tools/pre-execute). Rejected: it exists so third parties can veto product-owned lifecycle points. Governor owns every seam. Direct method calls between explicitly resolved services, plus exactly one opt-in InvariantRegistry of assertion callbacks (dsh's registrable invariants companion, on in sim/CI, off on a long campaign) - no generic bus.
- A separate `sandbox` seam (ctx.sandbox.confine(argv, policy) -> ConfinedArgv) split from the critic runtime, as dsh splits sandbox from code-runtime. Rejected as ceremony HERE: dsh's split is justified by many confinement consumers (bash, fs, code-mode) across four platforms. Governor has exactly one consumer (critic code) on exactly one platform (Darwin/seatbelt). Confinement folds into SandboxedPythonCriticRuntime. What I did NOT drop is the honesty the split protected: `enforcement` stays a three-value string on the Definition, and an unavailable seatbelt makes compile() raise rather than falling back to unconfined exec (fail-closed).
- Schemastery, or pydantic, for config schemas. Rejected: pydantic is not a dependency and the pin set is deliberately minimal. Frozen dataclasses plus a validate(config) per provider setup, with a canonical-JSON round-trip assert at mount time (which also enforces the process-boundary purity constraint pydantic would not).
- typing.Protocol as the default for seam Definitions. Rejected: no third-party objects are being adapted, and abc.ABC gives shared concrete helpers (schema_sha256(), GateDecision.__post_init__ refusing `passed and not conclusive`) plus a runtime isinstance guard in ctx.provide. Protocol stays available for the day a provider is an object we do not control.
- entry_points / plugin auto-discovery for factory resolution. Rejected: import-time scanning, slow, and invisible in a config dump. Rows name a dotted path pkg.mod:setup resolved with importlib. Also rejected a short-name alias table alongside it - two resolution mechanisms for one job.
- A DI container (injector, punq, dependency-injector). Rejected: ~70 lines of ExitStack plus typed slots is less machinery than the container's own configuration would be.
- Deep-merge patch semantics. Rejected for dsh's reason: deep merge makes 'which layer set this key' unanswerable. Whole-field replace means a later layer restates the whole config object it wants - reads worse, debugs better.
- YAML !!js-style inline code expressions in config (dsh has them; its own analysis flags them as not worth porting). Rejected outright: config rows must survive a fork to a worker interpreter, and a code-eval escape hatch in config is a liability with no upside here. Env interpolation is ${VAR:-default}, resolved by the loader.
- A `Tool` / tool-registry seam mirroring dsh's ctx.tools. Rejected: Governor has no model-issued tool calls in the control loop. Recovery skills are the analogue and belong to RecoveryExecutor.skills(), keyed by name and bound by the critic's verdict.
- Hot-reloadable settings read at control-step frequency (dsh reads maxParallelToolCalls fresh per dispatch group, tool-calls.ts:199). Deliberately diverged: settings are read once per EPISODE and frozen into the episode header. A value that changed mid-episode would make the episode non-reconstructable from its own header, breaking the paired-gate premise. The getter-thunk indirection is kept; the read cadence is coarsened to the episode boundary.
- Folding `features` into `env` (one seam returning tagged features directly). Rejected: ReplayEnvironment and MaskedFeatures vary independently - shadow replay uses recorded env + live features, the ablation gate uses live env + masked features. Two seams, four combinations, all four used.
- Splitting `analyzer` into separate segmenter and clusterer seams. Rejected for now: their providers do not evolve independently yet (the LLM reviewer re-partitions, it does not re-segment). Split when a second segmenter appears, per the 'split only when roles evolve independently' rule.
- Putting control-frequency observations on the log SURFACE (dsh's 'model-visible means logged' taken literally). Rejected per dsh's own robotics antipattern: log every frame as a non-surface event, admit only keyframes, verdicts, recovery begin/result and operator messages to the surface, and verify the invariant by content hash rather than a full JSON string compare.

## RISKS
- Measured features_read catches direct reads, not derived leakage. A sandboxed critic could read a permitted feature and reconstruct a forbidden one, or cache an over-tier value across steps. The recording view is defense in depth; PrivilegeAblationGate is the actual authority, and the promoted-bundle report must say so rather than implying the declaration was proven.
- Tier-map drift between EnvSpec.group_tiers and FeatureProvider.schema(). A robosuite version that regroups observations would silently reclassify a feature. Mitigation: EnvSpec.group_tiers is the single authority, the provider derives from it, and a boot-time assertion rejects any emitted feature whose tier is not derivable from the env grouping. Needs a regression test pinned to robosuite 1.5.2's exact group names.
- The pure-data config constraint is only checked at mount time by a canonical-JSON round trip. A provider that stashes a live handle in config works under InlineRolloutExecutor and explodes (or worse, silently shares state) under ProcessPoolRolloutExecutor. The round-trip assert must run in both, and CI must exercise the pool path for every shipped provider.
- `gates` breaks the one-owner-per-slot rule (a keyed registry, many providers mounted at once), so gate ORDERING becomes config-dependent. Mitigation: the campaign requires an exact set of gate kinds by name and refuses to promote on 'whatever happened to be registered' - mirroring Zetta store.promote's required-kind set rather than trusting registry contents.
- Unmount is only safe between episodes. Python refcounting means an in-flight EpisodeRunner holding a disposed provider keeps using it silently. Mitigation: the runner resolves every seam once at episode start and writes config_sha256 into the header; a header hash disagreeing with the live composed tree is an invariant violation. This is a convention the invariant enforces, not something the type system prevents.
- CompiledCritic.evaluate is synchronous and per-step. At 20 Hz (50 ms budget) a confined-process IPC round trip (sub-ms) is comfortable, but the Definition has no pipelined or async variant, so a future 200+ Hz control rate would require changing the interface, not just the provider. Accepting this is a deliberate bet on the measured 20 Hz robosuite regime.
- CPython cannot forcefully interrupt arbitrary running code from another thread, so the sandboxed runtime's per-step budget is enforced by recycling the worker process on overrun, not by a clean terminate() the way dsh's worker_threads backend can. A budget overrun therefore costs a worker restart mid-episode and must be logged as a degraded episode reason, not swallowed.
- Ten seams is at the top of the requested range, and `analyzer` plus `rollout` are the two most likely to prove premature if the campaign stays single-machine with one clusterer. Both are cheap to collapse back into the lifecycle (their Definitions have no other consumers), so reversal cost is bounded - but the completeness lint should flag a seam that never gains a second live provider.
- SearchProposer guarantees zero-external-API operation, but a pure (feature, op, threshold, dwell) enumeration may plateau below what an LLM proposer reaches. If the day-one provider cannot clear the held-out gate on its own, GOAL.md acceptance #2 becomes dependent on the optional provider, weakening the 'zero external API' claim from structural to nominal. Measure the search proposer's ceiling early.
- Registration reversal is exercised mainly by tests and the ablation gate. If in practice both paths end up going through config patching plus a fresh boot (which the process-pool path does anyway), the in-process disposer machinery becomes dead weight and should be cut down to a plain close() on the Harness.

## SPEC
## 0. Package layout

```
governor/
  seams/          # Definitions ONLY. No provider may be imported here.
    base.py       # Service, Context, Registry, Harness, compose(), Settings
    env.py  features.py  policy.py  critic.py  recovery.py
    store.py  rollout.py  analyzer.py  proposer.py  gate.py
  providers/      # one subpackage per seam; each module exports setup(ctx, config) -> Disposer | None
  loop/           # EpisodeRunner + invariants   (NOT a seam)
  campaign/       # lifecycle state machine, gate runner  (NOT a seam)
  bundles/base.yaml
```

CI lint (`scripts/check_seams.py`): for every module in `governor/seams/`, assert >=1 concrete subclass under `providers/` and >=1 importer outside `seams/`. Fails on a Definition with no consumer or an implementation with no Definition — dsh's docs/capability-seams.md generator guard, as ~40 lines of `ast` walking instead of runtime reflection.

---

## 1. `seams/base.py` — the whole mechanism

```python
from __future__ import annotations
import abc, contextlib, enum, hashlib, importlib, json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

Disposer = Callable[[], None]
T = TypeVar("T")


class SeamError(RuntimeError): ...
class SeamUnavailable(SeamError):
    """A required seam is not mounted. NEVER degrade silently: fail closed."""
class SeamMisuse(SeamError):
    """The caller broke the Definition contract. Distinct from any failure the
    workload itself produced, which is always a returned field."""


class Service(abc.ABC):
    """Base of every Service Definition. `slot` names the Context attribute it owns."""
    slot: str

    def dispose(self) -> None:          # optional; called when the mount unwinds
        return None


class Registry(Generic[T]):
    """For the one seam that is many-providers-at-once (gates), and for companions."""
    def __init__(self, what: str) -> None:
        self._what = what
        self._items: dict[str, T] = {}

    def register(self, key: str, item: T) -> Disposer:
        if key in self._items:
            raise SeamMisuse(f"{self._what} {key!r} already registered")
        self._items[key] = item
        def undo() -> None:
            if self._items.get(key) is item:
                del self._items[key]
        return undo

    def get(self, key: str) -> T:
        try:
            return self._items[key]
        except KeyError:
            raise SeamUnavailable(f"{self._what} {key!r} not registered") from None

    def keys(self) -> tuple[str, ...]:
        return tuple(self._items)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class Settings:
    """dsh's getter-not-value discipline, minus base/user layering, revisions,
    optimistic concurrency and redaction. Read cadence is the EPISODE boundary."""
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def register(self, ns: str, default: Any) -> Callable[[], Any]:
        self._values.setdefault(ns, default)
        return lambda: self._values[ns]

    def set(self, ns: str, value: Any) -> None:
        self._values[ns] = value

    def snapshot(self) -> Mapping[str, Any]:
        return dict(self._values)


@dataclass
class Context:
    # one Optional slot per seam; declaration order IS the documented mount order
    store:          "EpisodeStore | None"      = None
    env:            "Environment | None"       = None
    features:       "FeatureProvider | None"   = None
    policy:         "BasePolicy | None"        = None
    critic_runtime: "CriticRuntime | None"     = None
    recovery:       "RecoveryExecutor | None"  = None
    rollout:        "RolloutExecutor | None"   = None
    analyzer:       "FailureAnalyzer | None"   = None
    proposer:       "CandidateProposer | None" = None
    # registry-shaped seam and companions are eager, never None
    gates:      "Registry[Gate]"     = field(default_factory=lambda: Registry("gate"))
    settings:   Settings             = field(default_factory=Settings)
    invariants: "Registry[Callable]" = field(default_factory=lambda: Registry("invariant"))
    config_sha256: str = ""
    _mount: contextlib.ExitStack | None = field(default=None, repr=False)

    # ---- registration --------------------------------------------------
    def provide(self, service: Service) -> Disposer:
        slot = service.slot
        held = getattr(self, slot)
        if held is not None:
            raise SeamMisuse(
                f"seam {slot!r} already provided by {type(held).__name__}; "
                f"disable that config row instead of mounting a second provider"
            )
        setattr(self, slot, service)
        def undo() -> None:
            if getattr(self, slot) is service:
                setattr(self, slot, None)
            service.dispose()
        if self._mount is not None:
            self._mount.callback(undo)     # lifetime := the mounting row's lifetime
        return undo

    def effect(self, make: Callable[[], Disposer]) -> Disposer:
        """Any other registry mutation. dsh 'registrations are effects', with
        ExitStack standing in for the Cordis fiber."""
        undo = make()
        if self._mount is not None:
            self._mount.callback(undo)
        return undo

    # ---- resolution ----------------------------------------------------
    def require(self, slot: str) -> Any:
        got = getattr(self, slot, None)
        if got is None:
            raise SeamUnavailable(
                f"seam {slot!r} is not mounted; run `governor config --dump` "
                f"to see the composed tree"
            )
        return got
```

### Rows, composition, mount / unmount

```python
@dataclass(frozen=True)
class Row:
    id: str
    factory: str                                     # "governor.providers.env.robosuite:setup"
    config: Mapping[str, Any] = field(default_factory=dict)
    disabled: bool = False

Setup = Callable[[Context, Mapping[str, Any]], Disposer | None]


def compose(layers: Sequence[Sequence[Mapping[str, Any]]]) -> list[Row]:
    """bundle -> profile -> patch files -> CLI overlay.
    Whole-field replace, never deep merge. Port of dsh vendor/include
    applyEntryPatches, minus the JS-eval escape hatch."""
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for raw in layer:
            entry = dict(raw)
            rid = entry.get("id")
            if not rid:
                raise SeamMisuse(f"config row without an id: {entry!r}")
            if entry.pop("insert", False):
                if rid in index:
                    raise SeamMisuse(f"insert of duplicate row id {rid!r}")
                row = {"config": {}, "disabled": False, **entry}
                rows.append(row)
                index[rid] = row
                continue
            target = index.get(rid)
            if target is None:                   # DIVERGENCE from dsh: hard error, not a warn.
                raise SeamMisuse(                # a typo'd id that silently no-ops would
                    f"patch targets unknown row id {rid!r}"   # invalidate a campaign arm
                )
            for key, value in entry.items():
                if key != "id":
                    target[key] = value          # field-level replace
    return [Row(**r) for r in rows]


def resolve_setup(factory: str) -> Setup:
    mod, _, attr = factory.partition(":")
    if not attr:
        raise SeamMisuse(f"factory {factory!r} must be 'module:callable'")
    return getattr(importlib.import_module(mod), attr)


class Harness:
    def __init__(self, rows: Sequence[Row]) -> None:
        self.rows = [r for r in rows if not r.disabled]
        self.ctx = Context(config_sha256=sha256_of([vars(r) for r in self.rows]))
        self._mounts: dict[str, contextlib.ExitStack] = {}

    def boot(self) -> Context:
        try:
            for row in self.rows:
                self.mount(row)
        except Exception:
            self.close()
            raise
        return self.ctx

    def mount(self, row: Row) -> None:
        if row.id in self._mounts:
            raise SeamMisuse(f"row {row.id!r} already mounted")
        try:                                     # process-boundary purity: config must
            json.loads(canonical_json(row.config))   # survive a fresh interpreter
        except (TypeError, ValueError) as exc:
            raise SeamMisuse(f"row {row.id!r} config is not pure data: {exc}") from exc
        setup = resolve_setup(row.factory)
        stack = contextlib.ExitStack()
        prev, self.ctx._mount = self.ctx._mount, stack
        try:
            extra = setup(self.ctx, row.config)
            if extra is not None:
                stack.callback(extra)
        except Exception:
            stack.close()
            raise
        finally:
            self.ctx._mount = prev
        self._mounts[row.id] = stack

    def unmount(self, row_id: str) -> None:
        """Reversal. ExitStack unwinds LIFO, so a setup that provided a service and
        registered three gates undoes all four in reverse with no bookkeeping."""
        self._mounts.pop(row_id).close()

    def remount(self, row: Row) -> None:         # hot-swap = unmount + mount
        if row.id in self._mounts:
            self.unmount(row.id)
        self.mount(row)

    def close(self) -> None:
        for rid in reversed(list(self._mounts)):
            self.unmount(rid)
```

Providers store settings thunks, never values. `EpisodeRunner` calls `ctx.settings.snapshot()` once at `open_episode` and reads only that snapshot inside the loop, so the episode stays reconstructable from its own header.

---

## 2. Seam definitions

### 2.1 `seams/env.py`

```python
class PrivilegeTier(enum.IntEnum):
    PROPRIO    = 0   # measurable on the real robot
    EXTERO     = 1   # onboard sensing (camera, F/T): real, not proprioceptive
    PRIVILEGED = 2   # simulator-internal only

@dataclass(frozen=True)
class RawObservation:
    step: int
    sim_time: float
    mono_ns: int
    groups: Mapping[str, Mapping[str, "np.ndarray"]]   # "robot0_proprio-state" | "object-state"

@dataclass(frozen=True)
class StepInfo:
    done: bool
    success: bool
    safety_events: tuple[str, ...] = ()

@dataclass(frozen=True)
class EnvSpec:
    env_id: str
    action_dim: int
    control_freq: float
    horizon: int
    group_tiers: Mapping[str, PrivilegeTier]   # SINGLE AUTHORITY for the privilege boundary

class Environment(Service):
    slot = "env"

    @abc.abstractmethod
    def spec(self) -> EnvSpec: ...

    @abc.abstractmethod
    def reset(self, *, seed: int) -> RawObservation:
        """Bind `seed` where the world's own RNG is constructed
        (robosuite: suite.make(..., seed=seed)). A global numpy seed does NOT
        satisfy this contract and silently degrades every paired gate to a coin
        flip - verified, docs/verified-environment.md. Regression-tested."""

    @abc.abstractmethod
    def step(self, action) -> tuple[RawObservation, StepInfo]: ...

    @abc.abstractmethod
    def reset_identity(self) -> str:
        """sha256 over the canonical post-reset physical state. Two episodes may be
        declared the same physical reset IFF this is equal
        (Zetta gating._same_physical_reset / state_sha256)."""
```
Providers: `providers/env/robosuite.py` (day one) · `providers/env/replay.py` (proves the seam) · future `providers/env/panda_real.py`. Consumer: `loop/runner.py`.

### 2.2 `seams/features.py` — the privilege boundary

```python
@dataclass(frozen=True)
class FeatureSpec:
    name: str
    tier: PrivilegeTier
    unit: str
    source: str          # "robot0_proprio-state.robot0_gripper_qpos[0]-[1]"

@dataclass(frozen=True)
class FeatureFrame:
    step: int
    values: Mapping[str, float]        # finite scalars only
    schema_sha256: str
    max_tier_present: PrivilegeTier

class MissingFeature(KeyError): ...

class RecordingFrame(Mapping[str, float]):
    """Handed to a compiled critic instead of the raw mapping. Records every read
    so the DECLARED privilege budget can be checked against MEASURED dependence -
    the mechanism Zetta replaces with prose (GOAL.md)."""
    def __init__(self, frame: FeatureFrame) -> None:
        self._f = frame
        self.read: list[str] = []
    def __getitem__(self, key: str) -> float:
        try:
            value = self._f.values[key]
        except KeyError:
            raise MissingFeature(key) from None    # masked features are ABSENT, never 0.0
        self.read.append(key)
        return value
    def __iter__(self): return iter(self._f.values)
    def __len__(self):  return len(self._f.values)

class FeatureProvider(Service):
    slot = "features"

    @abc.abstractmethod
    def schema(self) -> tuple[FeatureSpec, ...]: ...

    @abc.abstractmethod
    def extract(self, obs: RawObservation) -> FeatureFrame:
        """MUST NOT emit a name outside schema(); MUST NOT emit non-finite values.
        Enforced by the harness at mount time and by the runner - never trusted."""

    def schema_sha256(self) -> str:
        return sha256_of([[s.name, int(s.tier), s.unit, s.source]
                          for s in sorted(self.schema(), key=lambda s: s.name)])
```
Providers: `RobosuiteFeatures` · **`MaskedFeatures(inner, max_tier)`** (`schema()` filters, `extract()` *drops* over-tier names) · `ReplayFeatures(states_blob)`.
Consumers: `EpisodeRunner`, `CompiledCritic`, `PrivilegeAblationGate`.

### 2.3 `seams/policy.py`

```python
@dataclass(frozen=True)
class PolicyIdentity:
    kind: str
    checkpoint_sha256: str | None
    config_sha256: str

class BasePolicy(Service):
    slot = "policy"
    @abc.abstractmethod
    def identity(self) -> PolicyIdentity: ...
    @abc.abstractmethod
    def reset(self, obs: RawObservation, *, policy_rng: int) -> None:
        """policy_rng is preregistered per seed in the campaign manifest; both arms
        of a paired gate MUST receive the identical value."""
    @abc.abstractmethod
    def act(self, obs: RawObservation) -> "np.ndarray": ...
```
FROZEN contract: the harness never calls anything that mutates the policy; `identity()` goes into the episode header, and a header whose policy identity differs across a gate's two arms is an invariant violation.
Providers: `OpenLoopScriptedPolicy` · `BCCheckpointPolicy` · `ReplayPolicy`.

### 2.4 `seams/critic.py`

```python
@dataclass(frozen=True)
class PrivilegeBudget:
    max_tier: PrivilegeTier
    features: tuple[str, ...]
    justification: str

@dataclass(frozen=True)
class CriticRule:
    rule_id: str
    body: str                  # DSL expression for the rule runtime; python source for the sandbox one
    recovery: str              # a key of RecoveryExecutor.skills()
    params: Mapping[str, float]
    dwell: int = 1
    cooldown: int = 0

@dataclass(frozen=True)
class CriticBundle:
    bundle_sha256: str
    parent_sha256: str | None
    rules: tuple[CriticRule, ...]        # frozen parents + exactly ONE delta rule
    declared_budget: PrivilegeBudget

@dataclass(frozen=True)
class CriticVerdict:
    rule_id: str; step: int; recovery: str; params: Mapping[str, float]

class CriticFailureKind(enum.StrEnum):
    EXCEPTION = "exception"; TIMEOUT = "timeout"; MISSING_FEATURE = "missing_feature"
    INVALID_OUTPUT = "invalid_output"; WORKER_EXIT = "worker_exit"
    BUDGET_VIOLATION = "budget_violation"

@dataclass(frozen=True)
class CriticFailure:
    kind: CriticFailureKind; message: str; rule_id: str | None = None

@dataclass(frozen=True)
class CriticEval:
    verdict: CriticVerdict | None
    error: CriticFailure | None          # a FIELD on a returned value, never raised
    latency_us: int
    features_read: tuple[str, ...]       # MEASURED via RecordingFrame

class CompiledCritic(abc.ABC):
    required_features: tuple[str, ...]
    @abc.abstractmethod
    def reset(self) -> None: ...                       # per-episode dwell/cooldown state
    @abc.abstractmethod
    def evaluate(self, frame: FeatureFrame) -> CriticEval: ...

class CriticRuntime(Service):
    slot = "critic_runtime"

    @abc.abstractmethod
    def compile(self, bundle: CriticBundle) -> CompiledCritic:
        """Raises SeamMisuse for a malformed bundle (contract misuse) or when the
        containment substrate is unavailable - FAIL CLOSED, never fall back to
        unconfined exec. Every per-step failure is CriticEval.error instead."""

    @property
    @abc.abstractmethod
    def enforcement(self) -> str:
        """'interpreted' | 'in-process' | 'confined-process'. Truthful containment
        level, never a bool (dsh ConfinedArgv.enforcement)."""

    @property
    @abc.abstractmethod
    def budgets(self) -> Mapping[str, int]:   # {"per_step_us": ..., "per_episode_ms": ...}
```
Providers: `RuleCriticRuntime` (`enforcement="interpreted"`) · `SandboxedPythonCriticRuntime` (`"confined-process"`; one seatbelt-confined worker per runner process, SBPL profile probed and cached **once per process lifetime**, never per tick; `compile()` ships a precompiled code object, `evaluate()` is one pipe round trip; a budget overrun recycles the worker and surfaces `WORKER_EXIT`, never a silent continue).

### 2.5 `seams/recovery.py`

```python
@dataclass(frozen=True)
class RecoverySpec:
    name: str; max_steps: int; preempts: bool; params: Mapping[str, str]

class RecoveryHandle(abc.ABC):
    preempts: bool
    @abc.abstractmethod
    def next_action(self, obs: RawObservation, base_action) -> "np.ndarray | None":
        """None = finished. If preempts, the return REPLACES the base action;
        otherwise it is ADDED to it. Must return within one control period."""
    @abc.abstractmethod
    def abandon(self, reason: str) -> None: ...

class RecoveryExecutor(Service):
    slot = "recovery"
    @abc.abstractmethod
    def skills(self) -> Mapping[str, RecoverySpec]: ...
    @abc.abstractmethod
    def begin(self, verdict: CriticVerdict, obs: RawObservation) -> RecoveryHandle: ...
```
Runner contract: `begin` / `abandon` / completion are all logged; the runner hard-abandons at `spec.max_steps`; a verdict naming an unknown skill is `INVALID_OUTPUT`, not a crash.
Providers: `ScriptedRecoveryExecutor` · `ResidualRecoveryExecutor` · `NoopRecoveryExecutor`.

### 2.6 `seams/store.py`

```python
@dataclass(frozen=True)
class SurfaceOp:
    op: str                      # "append" | "replace"
    start: int | None = None
    end: int | None = None

@dataclass(frozen=True)
class Event:
    seq: int                     # INVARIANT: seq == len(log) at append
    type: str
    mono_ns: int                 # monotonic - the ordering authority
    wall_ns: int                 # wall clock - for humans only
    sim_time: float | None
    data: Mapping[str, Any]      # JSON-safe and SMALL; arrays go to blobs
    surface_op: SurfaceOp | None = None
    source_seqs: tuple[int, ...] | None = None

@dataclass(frozen=True)
class EpisodeHeader:
    episode_id: str; campaign_id: str; generation: int
    seed: int; policy_rng: int
    bundle_sha256: str | None
    policy_identity: PolicyIdentity
    feature_schema_sha256: str
    config_sha256: str
    settings_snapshot: Mapping[str, Any]

class EpisodeEndReason(enum.StrEnum):
    COMPLETED="completed"; FAILED="failed"; TIMEOUT="timeout"
    ENVELOPE="envelope_violation"; OPERATOR="operator_preempt"
    INFRA="infra_invalid"; INTERRUPTED="interrupted"

class EpisodeWriter(abc.ABC):
    @abc.abstractmethod
    def append(self, type: str, data: Mapping[str, Any], *,
               surface_op: SurfaceOp | None = None,
               source_seqs: tuple[int, ...] | None = None) -> Event:
        """Snapshot-and-validate `data` at the append site, plan the surface
        transition, THEN commit, THEN notify. Rejects non-JSON payloads here,
        not at flush. Large payloads must go through put_blob."""
    @abc.abstractmethod
    def put_blob(self, payload: bytes, *, kind: str) -> str: ...   # -> sha256
    @abc.abstractmethod
    def close(self, reason: EpisodeEndReason) -> "EpisodeRecord": ...

class EpisodeStore(Service):
    slot = "store"
    @abc.abstractmethod
    def open_episode(self, header: EpisodeHeader) -> EpisodeWriter: ...
    @abc.abstractmethod
    def events(self, episode_id: str) -> Sequence[Event]: ...
    @abc.abstractmethod
    def read_blob(self, digest: str) -> bytes: ...
    @abc.abstractmethod
    def records(self, **flt: Any) -> Iterator["EpisodeRecord"]: ...
    @abc.abstractmethod
    def repair(self, episode_id: str) -> list[Event]:
        """Pure function of a torn log -> the deterministic closers that make it
        well-formed (unmatched recovery -> aborted-before-dispatch outcome,
        step/end, episode/end{interrupted}), continuing the seq numbering."""
```
Event types — durable non-surface: `obs/frame`, `action/commanded`, `critic/eval`. Surface-eligible: `episode/goal`, `obs/keyframe`, `critic/verdict`, `recovery/begin`, `recovery/result`, `operator/message`. Only surface-eligible types may carry `surface_op`. That split keeps a 100-step episode's surface at ~10 nodes while the log keeps every frame — dsh's explicit robotics antipattern is putting control-frequency observations on the surface.

### 2.7 `seams/rollout.py`

```python
@dataclass(frozen=True)
class RolloutJob:
    job_id: str; seed: int; policy_rng: int
    bundle_sha256: str | None
    config_rows: tuple[Mapping[str, Any], ...]   # composed tree; ONLY thing crossing a process
    attempt: int = 0

@dataclass(frozen=True)
class RolloutOutcome:
    job_id: str
    episode_id: str | None
    infra_failure: str | None      # infra_invalid -> attempts ledger only, never the episodes ledger

class RolloutExecutor(Service):
    slot = "rollout"
    @abc.abstractmethod
    def run(self, jobs: Sequence[RolloutJob]) -> Iterator[RolloutOutcome]:
        """Yields as jobs complete. NEVER raises for an episode-level failure -
        that is an outcome. Raises only for scheduler-internal failure."""
```
Providers: `InlineRolloutExecutor` · `ProcessPoolRolloutExecutor(workers=10)`, whose worker entrypoint is `Harness(compose([list(job.config_rows)])).boot()`, run one episode, return the id.

### 2.8 `seams/analyzer.py`

```python
@dataclass(frozen=True)
class FailureSegment:
    segment_id: str                        # content-addressed over artifact digests
    episode_id: str
    failure_class: str; stage: str; tool: str
    earliest_divergence_step: int | None   # None == 'not localizable'; NEVER encode as 0
    start_step: int; end_step: int
    severity: float; summary: str
    embedding: tuple[float, ...] = ()      # deterministic hashing trick, not a learned model

@dataclass(frozen=True)
class ClusterReport:
    report_sha256: str
    clusters: tuple["FailureCluster", ...]
    deterministic_source_sha256: str | None = None   # set when a reviewer re-partitioned

class FailureAnalyzer(Service):
    slot = "analyzer"
    @abc.abstractmethod
    def segment(self, record, events: Sequence[Event]) -> tuple[FailureSegment, ...]: ...
    @abc.abstractmethod
    def cluster(self, segments: Sequence[FailureSegment]) -> ClusterReport: ...
```
Providers: `DeterministicAnalyzer` (hard partition on `(failure_class, stage, tool)`, complete-link agglomeration at 0.72, medoid representative) · `LlmClusterReviewer(inner)` (must set `deterministic_source_sha256`; segment ids blinded through aliases). Target *ranking* stays harness-owned, not analyzer-owned: `(-unique_episode_count, -prevalence, -mean_severity, cluster_id)`, frozen as `target_sha256`.

### 2.9 `seams/proposer.py`

```python
@dataclass(frozen=True)
class ProposalRequest:
    target: "ClusterTarget"
    parent: CriticBundle
    feature_schema: tuple[FeatureSpec, ...]
    budget_cap: PrivilegeTier                 # campaign-wide privilege ceiling
    recovery_skills: Mapping[str, RecoverySpec]
    evidence: tuple["SegmentEvidence", ...]   # BLINDED: no seeds, no episode ids
    round_index: int

class CandidateProposer(Service):
    slot = "proposer"
    @abc.abstractmethod
    def propose(self, request: ProposalRequest) -> CriticBundle:
        """Returns parent.rules + exactly ONE new rule, with declared_budget.
        Raises SeamMisuse if it cannot produce a well-formed single-delta bundle."""
    @abc.abstractmethod
    def identity(self) -> Mapping[str, str]:  # provider/model/prompt_sha256, or search config
```
Providers: `SearchProposer` (ranks `(feature, op, threshold, dwell)` candidates by separation between the target cluster's frames and the success controls, filtered to `tier <= budget_cap`; no network — the 40x finger-gap separation in docs/difficulty-calibration.md is exactly the signal it searches over) · `LlmProposer` · `FixtureProposer`.

### 2.10 `seams/gate.py`

```python
@dataclass(frozen=True)
class GateDecision:
    kind: str
    passed: bool
    conclusive: bool
    p_value: float | None
    alpha: float | None
    counts: Mapping[str, int]
    rationale: str
    def __post_init__(self) -> None:
        if self.passed and not self.conclusive:
            raise SeamMisuse("a gate may not pass on inconclusive evidence")

@dataclass(frozen=True)
class GatePlan:
    kind: str
    plan_sha256: str
    seeds: tuple[int, ...]              # preregistered; bound into the plan hash
    arms: tuple["ArmSpec", ...]         # each arm = a RolloutJob template + a config patch row
    reuse_parent_evidence: bool

class Gate(abc.ABC):
    kind: str
    @abc.abstractmethod
    def plan(self, candidate: CriticBundle, campaign) -> GatePlan: ...
    @abc.abstractmethod
    def decide(self, evidence: "PairedEvidence") -> GateDecision: ...
```
Registered into `ctx.gates` — the one many-provider seam. Providers:
- `SameSeedGate` — deterministic threshold, `p_value=None`, `conclusive=True`; per seed requires identical `policy_rng`, equal `reset_identity()`, action-trajectory divergence, and an attested recovery intervention on every rescued seed.
- `RegressionGate` — `candidate_successes >= parent_successes and candidate_safety <= parent_safety` over the 50 dev seeds; parent arms adopted from the frozen rollout ledger, so only candidate arms execute.
- `HeldoutMcNemarGate` — one-sided exact McNemar over discordant pairs, `alpha=0.025`, `min_gain=1`; `heldout_mode in {"test","validation"}` decides whether it may reject or is report-only.
- **`PrivilegeAblationGate`** (Governor's own) — replans the same-seed arms with one runtime patch row `{"id": "features", "config": {"max_tier": 0}}`, and requires that the measured `features_read` union is a subset of `declared_budget.features`. If `declared_budget.max_tier == PROPRIO` it further requires the ablated arm to reproduce the full arm's rescues; above PROPRIO it does not block, it *reports* the ablated success rate — GOAL.md acceptance #3.

---

## 3. Provider setup shape (the only pattern a provider author learns)

```python
# governor/providers/features/robosuite.py
from governor.seams.base import Context, Disposer, SeamMisuse
from governor.seams.features import FeatureProvider, PrivilegeTier

def setup(ctx: Context, config) -> Disposer | None:
    env = ctx.require("env")                          # explicit dependency, no solver
    cap = PrivilegeTier(config.get("max_tier", PrivilegeTier.PRIVILEGED))
    provider = RobosuiteFeatures(env.spec(), max_tier=cap)
    for spec in provider.schema():                    # boot-time tier-drift assertion
        if spec.tier > cap:
            raise SeamMisuse(f"{spec.name} exceeds configured cap {cap!r}")
    ctx.provide(provider)                             # disposer auto-owned by this row's mount
    return None
```

```yaml
# governor/bundles/base.yaml  (every row an insert; profiles/patches only override)
- {id: store,    insert: true, factory: governor.providers.store.jsonl:setup,            config: {root: runs}}
- {id: env,      insert: true, factory: governor.providers.env.robosuite:setup,          config: {env_id: Lift, robot: Panda, control_freq: 20, horizon: 100}}
- {id: features, insert: true, factory: governor.providers.features.robosuite:setup,     config: {max_tier: 2}}
- {id: policy,   insert: true, factory: governor.providers.policy.openloop:setup,        config: {perception_noise_sd: 0.022}}
- {id: critic,   insert: true, factory: governor.providers.critic.rules:setup,           config: {}}
- {id: recovery, insert: true, factory: governor.providers.recovery.scripted:setup,      config: {regrasp_max_steps: 30}}
- {id: rollout,  insert: true, factory: governor.providers.rollout.pool:setup,           config: {workers: 10}}
- {id: analyzer, insert: true, factory: governor.providers.analyzer.deterministic:setup, config: {similarity_threshold: 0.72}}
- {id: proposer, insert: true, factory: governor.providers.proposer.search:setup,        config: {budget_cap: 0}}
- {id: gates,    insert: true, factory: governor.providers.gates.standard:setup,         config: {heldout_mode: test, alpha: 0.025}}
```

```yaml
# profiles/ablation.patch.yaml - whole-field replace, NOT a merge
- {id: features, config: {max_tier: 0}}
```

CLI: `governor run --profile ablation --set proposer.config.budget_cap=0`.
`governor config --dump` prints `compose(...)` plus `config_sha256` through the identical code path as `boot()`, so a dump can never drift from what mounts.

---

## 4. The three properties the mechanism must keep

1. **Reversal is real, and used.** `Harness.remount(Row(id="features", factory=..., config={"max_tier": 0}))` is how `PrivilegeAblationGate` runs in-process and how every test swaps one provider under a fixed rest-of-the-world. Out of process it is the same data as a patch row on `RolloutJob.config_rows`. One representation, two paths.
2. **Config is pure data all the way to the worker.** Asserted at mount by a canonical-JSON round trip. No `!!js` analogue, no closures, no live handles — the process-pool provider would silently mis-share them.
3. **The composed tree is hashed into every episode header.** `config_sha256` + `feature_schema_sha256` + `PolicyIdentity` + `bundle_sha256` make an episode reconstructable from its own header plus the log, the precondition for every paired gate. `EpisodeRunner` resolves each seam once at episode start, and an invariant registered into `ctx.invariants` (on in sim/CI, off on a long campaign — dsh's opt-in invariants companion) re-derives the critic's input frame from the log at each step and compares it by content hash against what was actually handed to `evaluate()`. That is dsh's "model-visible IFF logged" assertion, and it is the test GOAL.md acceptance #4 requires to fail loudly on purpose.

======================================================================
# VERSION 2
======================================================================

## QUESTION
Design the evolution loop for `governor` (physical-harness): adopt Zetta's validation rigor (dev/held-out seed isolation, paired same-seed gate with significance, one atomic change per generation, frozen parent, content-hash artifacts, preregistration, infra failures excluded from scoring) while fixing three named weaknesses — (1) Zetta's "code-based critics" are a single-predicate rule DSL with the real code path (`tool_plugin`) disabled: ship real sandboxed critic code, a richer expression language, or both behind one seam, justified by the latency budget at control frequency; (2) Zetta's proposer requires an LLM API every generation: make the loop fully runnable with zero external API calls, with an LLM proposer as a drop-in provider, as a first-class decision; (3) specify the concrete failure-clustering algorithm and features. Deliver the loop state machine, the proposer interface, the gate specification with exact statistics, and the artifact layout.

## DECISION
Six decisions.

**D1 — Critic execution: real sandboxed Python code from day one, on exactly ONE execution path. The expression language is a compiler front end that emits into that same path, never a second runtime.**
The runtime is a persistent Seatbelt-confined CPython child, one per rollout worker, spawned once at worker start; the critic bundle is AST-validated and `compile()`d once at session open; each control tick is a synchronous request/response over a `socketpair` carrying a fixed-order `float64` feature vector (schema sent once, hashed). Measured budget: robosuite Lift/Panda costs 10.4 ms/step (96 steps/s single-process, `docs/verified-environment.md`); the IPC round trip plus predicate eval is ~100-200 µs typical with a 500 µs p99 hard budget = <5% of a sim step. Sandbox profile install + interpreter start (~40 ms) is amortized over ~10^4 steps per worker lifetime (<5 µs/step), the same "probe/select once per provider lifetime" amortization dsh uses in `sandbox-local/src/index.ts:487-506` (`chainVerdict`). Spawning a confined interpreter per tick (20-60 ms) is 2-6× the sim step and is rejected. In-process evaluation (~10 µs) is rejected as the scoring path and exists only as a dev/CI provider that is **refused** when `campaign.mode == 'scoring'` (fail-closed, per `packages/sandbox/sandbox/src/index.ts:131` `SandboxUnavailableError` — never silently degrade). The DSL survives as `governor.critic.dsl.compile_rule(spec) -> str` producing the same Python source that any other provider produces; there is no branch in the executor. That is the direct fix for Zetta's failure mode: two runtimes, one maintained, the other (`tool_plugin`) permanently `tool_plugin_runtime_available: False` (`stages.py:1863`) and hard-nulled at admission (`stages.py:2021`).

**D2 — Two threats, two mechanisms, and the load-bearing one is not the OS sandbox.**
Threat A (host integrity: file writes, network, exfiltration) → kernel confinement of the whole rollout worker via `sandbox-exec` SBPL built exactly like dsh `sandbox-local/src/profiles.ts:40-55` (`(allow default)(deny file-write*)` + subpath allow over **realpath-canonicalized** roots — `/tmp` is `/private/tmp` on Darwin, per `sandbox/src/roots.ts:20-33`). Cost amortized to zero. Threat B (evaluation integrity: env mutation, undeclared privileged-feature access, nondeterminism, hangs) → **cannot be solved by an OS sandbox at all**, because MuJoCo lives in the same address space as the policy. It is solved by address-space separation from the simulator: the critic process holds no handle to MuJoCo and receives only the declared, projected feature vector. That separation — not the SBPL profile — is why the per-tick IPC cost is paid, and it is what makes the project's privilege budget mechanically enforceable rather than prose (Zetta enforces "don't use privileged info as hidden control" only in prose).

**D3 — Zero-API deterministic proposer is the default and the reference implementation; LLM is a drop-in provider behind an identical `ProposalBrief -> CandidateDraft` contract.**
The default `SearchProposer@v1` is a real program synthesizer, not a stub: rank features by phase-wise Mann-Whitney AUC between target-cluster failures and success controls, **lexicographically preferring privilege tier P0** (so the privilege budget is a search objective, not just a report); fit `(θ, dwell, cooldown)` offline against the *exact* shadow-replay admission objective (recall=1 at-or-before divergence, success-control FP rate = 0), tie-broken by **maximum median lead time** (Zetta computes lead time and never optimizes it); pick recovery by a static `failure_class -> RecoverySpec` table plus a small parameter grid. `Exhausted` is a first-class return value that drives the state machine, so the loop terminates honestly without an API. The novelty constraint Zetta ships as prose to the LLM (`must_materially_change_rejected_mechanism`, `stages.py:1866`) becomes a mechanical filter on the search frontier.

**D4 — Significance lives where sampling lives: exactly one gate carries a p-value, and it is not the same-seed gate. This is a deliberate divergence from the brief's "paired same-seed gate with significance."**
Under bitwise determinism (verified: `suite.make(seed=N)` reproduces trajectories bit-for-bit, 5/5 seeds, `docs/verified-environment.md`) with a frozen parent and identical reset, the null "the appended pair has no causal effect" predicts *b = 0 rescues with probability 1*. Any p-value computed on it is theater: one rescue already falsifies it exactly. Worse, the same-seed seeds are the seeds the proposer fitted its threshold on, so a p-value there would dress up a training-set measurement. So: same-seed and regression gates are **deterministic decision rules** (breakage budgets and coverage intervals, not tests); the held-out gate carries the only exact test. To recover the *generalization* content the brief was reaching for, I add what Zetta lacks: a **fit/gate seed split inside the target cluster** (shadow replay may only touch fit seeds; the same-seed gate scores only gate seeds), plus a **Clopper-Pearson one-sided lower bound on the within-cluster rescue rate**, plus a **determinism canary** (3 preregistered parent episodes re-run each generation; `actions_sha256` mismatch aborts the campaign rather than scoring anything) — promoting `docs/verified-environment.md`'s reproducibility requirement from a doc note into a runtime gate.

**D5 — Three-way seed split (dev 150 / held-out 40 / confirm 60), exploiting measured throughput.** Zetta's `rollout_count=50` and 20 held-out seeds are GPU-budget artifacts (35.1 episodes/min on 8×A100). We measure **212 episodes/min on one laptop**, so 150 dev episodes cost ~42 s. Larger dev blocks are what make clusters big enough to survive the fit/gate split; 40 held-out pairs give ~80% power at rescue-rate r≥0.4 where 20 pairs only reach that at r≥0.6. Held-out stays `mode=test` (report-only, per Zetta's anti-overfitting rule) and additionally acts as a **stall detector** (≤0 gain for two consecutive promoted generations → `STALLED`). A third, campaign-terminal `confirm` block of 60 seeds, run exactly once against final-vs-generation-0, is the unbiased headline number that satisfies GOAL.md acceptance #2 without turning held-out into a second training set.

**D6 — "Critic-visible IFF logged" is a runtime invariant, transplanted from dsh, and it is the precondition for shadow replay being sound.** The harness logs the exact projected `FeatureVector` (ordered values + `feature_vector_sha256`) that went over the wire at step t. Shadow replay consumes those same bytes and asserts the hash. This is dsh `agent-loop/src/invariant.ts:19-55` (`JSON.stringify(options.messages) === JSON.stringify(session.deriveMessages())`, prepended so it cannot be short-circuited) adapted to feature vectors and hashes instead of JSON strings. It closes a concrete Zetta hole: `shadow_replay.py` re-reads `states.jsonl` and hopes the features are present, and its own failure reasons (`required_critic_features_never_logged`, `required_critic_features_changed_during_replay`) are evidence that they often are not.

## RATIONALE
**Why one execution path rather than "both behind a seam."** "Both behind one seam" is the comfortable answer and the numbers do not support it. The only reason to keep a fast in-process expression evaluator would be latency, and the latency argument is empty: the confined-worker path costs <5% of a 10.4 ms sim step, so at 20 Hz control the critic is never the bottleneck — `docs/verified-environment.md` already concludes rollout budget is not the constraint on this machine. Meanwhile Zetta demonstrates the cost of two paths empirically: `tool_plugin` is threaded through `models.py:598`, `stages.py:434/528`, `lifecycle.py:1621/1654/1879` and is dead everywhere, permanently advertised to the proposer as unavailable and hard-rejected at admission. A seam with a disabled provider is not optionality, it is rot. So: one executor, and the DSL becomes a *front end* — which is strictly more useful than a second runtime, because it lets the deterministic proposer emit rules through a typed grammar while an LLM proposer emits free-form code, and both land in the same AST validator, the same feature contract, the same shadow replay, the same gates.

**Why the process boundary is worth 200 µs when a pruned-namespace `exec` costs 10 µs.** CPython in-process restriction is not a boundary (`__subclasses__`, ctypes, frame walking), which dsh states about a *stronger* mechanism than anything Python offers — a real V8 isolate with empty env and hard `terminate()` is still labeled "containment, not a security boundary" (`code-runtime-worker-thread/README.md`). But the decisive argument here is not escape-to-shell, it is that the project's central claim is the privilege budget. A critic sharing an address space with the MuJoCo `MjData` can read `cube_pos` regardless of what it declared, and no static analysis of generated code can be trusted to prove it did not. Process separation makes the undeclared feature *physically absent*: `features['cube_pos']` raises, the harness records `contract_violation`, and the budget is enforced by construction rather than by review. That is the difference between GOAL.md's "用类型系统 + 门禁强制" and Zetta's prose rule.

**Why the deterministic proposer can actually be good, not a fallback.** Because shadow replay already exists as a read-only, zero-simulator-cost evaluator over stored trajectories (`shadow_replay.py:39-187`), the detector-fitting problem is an offline supervised problem with a *stated objective the gate will later apply verbatim* (`passed_detection_preflight`: recall == target count, FP == 0). A search that optimizes exactly the preflight objective is not a heuristic approximation of what an LLM would do — it is the closed-form solution to the part of the problem that is actually decidable offline. What is genuinely undecidable offline is whether the *recovery* works, because new actions produce a different future (`shadow_replay.py:184-186` says so honestly), and that is precisely what the same-seed gate is for. So the division of labor is: search fits the detector (decidable), a table plus small grid picks the recovery (undecidable, arbitrated by the gate), and refinement rounds explore the recovery grid. An LLM adds value only in generating *novel mechanism templates*, which is exactly why it belongs as a provider and not as a dependency. And `docs/difficulty-calibration.md` confirms the task's dominant failure mode is trivially separable by a P0 feature (`finger_gap` 0.038-0.046 success vs 0.0010-0.0012 failure — 40× separation), so the default proposer will find the first real critic without any API call. That is the empirical basis for making zero-API first-class rather than aspirational.

**Why the same-seed gate must not carry a p-value, and what replaces it.** Zetta gets this right by accident and for the wrong reason (it just sets `p_value = None, conclusive = True`, `gating.py:200-224`). The principled statement: on paired, bitwise-deterministic seeds with a frozen parent, every observation is a *fact*, not a *sample*. Hypothesis testing is a tool for separating signal from sampling noise; where there is no sampling noise it produces vacuous certainty (all-discordant-one-direction → p = 2^-b, "significant" at b=6 no matter how trivial the change). The same logic condemns Zetta's regression gate rule `candidate_successes >= parent_successes` (`gating.py:246-248`): it is a *net* comparison, so a candidate that fixes 4 and breaks 4 passes. On deterministic paired seeds a break is a known fact and the honest object is a **breakage budget**, defaulting to zero. Sampling uncertainty enters at exactly one place — generalization to unseen seeds — so exactly one gate gets the exact test.

**Why held-out stays report-only but a third block exists.** Zetta's `heldout_mode='test'` (`lifecycle.py:4682-4712`, `store.py:345-400`) is the right anti-overfitting control and I keep it: if held-out could reject candidates, iterating 25 candidate rounds against it converts the unseen block into a second training set. But GOAL.md acceptance #2 demands an authoritative held-out significance result, and a report-only block cannot supply one after 25 rounds of loose coupling. The resolution is a third, campaign-terminal block touched exactly once. It costs 120 episodes ≈ 34 s at measured throughput — a rounding error, and it is the only number I would put in a paper.

**Two statistical bugs in the source I am deliberately not inheriting.** (a) `evaluate_two_stage_heldout` applies the same `alpha` at both looks; family-wise error is P(sig at stage 1) + P(not sig at 1 ∧ sig at 2), strictly above the nominal 0.025. I use a single fixed look at n=40; if a two-stage design is ever preregistered it must spend alpha (O'Brien-Fleming one-sided at t=(0.5, 1): α₁=0.005, α₂=0.023). (b) `heldout_min_gain=1` on n=20 is a 5 pp bar; scaled to n=40 I set `min_gain=3` (7.5 pp) so statistical significance alone cannot promote a practically null change — the same intent Zetta states for `min_gain`, applied consistently to the larger block.

**Why clustering is rebuilt rather than adopted.** Zetta's `segment_similarity` (`clustering.py:36-52`) puts weight 0.6 on a token cosine of a *harness-authored prose summary* and 0.3 on a 32-dim hashed embedding **of that same string** — so ~90% of the similarity measures which detector fired, which is already the hard-partition key `(failure_class, stage, tool)`. Only 0.1 touches physics, via `state_signature` = "the first 16 finite numeric scalars from state rows," which is order-dependent, unnormalized, and mixed-unit, so its cosine is dominated by whichever channel has the largest magnitude. And the 0.72 threshold is uncalibrated. I keep the two parts that are load-bearing and correct — the hard partition (mechanisms in different buckets must never merge), complete linkage (documented anti-chaining rationale), deterministic tie-breaking by sorted segment ids, content-addressed `segment_id`, `earliest_divergence_step = None` semantics (`models.py:245`: encoding it as 0 poisons lead-time statistics), and harness-owned target ranking so a proposer cannot steer the campaign (`lifecycle.py:2475-2529`, `ranking_authority`) — and replace the metric with a standardized 34-dim physical feature vector built from P0 signals only, plus a silhouette-based cut instead of a magic threshold. P0-only is not incidental: a cluster whose identity depends on privileged simulator state is not reproducible on a real robot, which would undercut the whole sim-to-real argument.

**Infra-failure handling is split where Zetta merges it.** Zetta retries infra failures uniformly (`max_infrastructure_attempts=2`). But `critic_contract_violation` — an undeclared feature read, an attempted env write, a nondeterministic call — is not transient; retrying cannot fix it. It is a *candidate admission rejection*. Conflating the two turns a broken candidate into a retry sink and, worse, lets flakiness launder into scoring. Hence the split taxonomy plus a per-arm infra-rate guard that voids a gate as `INCONCLUSIVE_INFRA` rather than scoring it.

## REJECTED
- Ship the richer expression language only (Zetta's DSL plus arithmetic, temporal operators, and cross-feature comparison), no sandboxed code. Rejected: it does not remove the need for an isolation boundary, because the privilege budget requires that undeclared features be physically unreachable, and an in-process evaluator sharing the address space with MjData cannot guarantee that. It also caps critic expressiveness at whatever the grammar authors anticipated, and GOAL.md names 'real sandboxed critic code' as frontier #1 precisely because it is the step Zetta stopped at.
- Ship both a fast in-process expression path and a slow sandboxed code path behind one seam, routing by rule type. Rejected on measured latency: the sandboxed path costs <5% of a 10.4 ms sim step, so the fast path buys nothing worth a second executor, and Zetta's dead tool_plugin is the empirical demonstration that the unused branch rots (permanently advertised as unavailable in stages.py:1863 and hard-nulled at admission in stages.py:2021). A second path also doubles the surface on which the feature contract and the privilege budget must be enforced identically.
- Spawn a fresh sandbox-exec'd interpreter per critic evaluation (maximal isolation per call). Rejected on arithmetic: 20-60 ms per spawn against a 10.4 ms sim step is a 2-6x slowdown, collapsing 708 steps/s to roughly 15-40 steps/s and destroying the campaign budget. This is the exact per-call-versus-per-lifetime mistake dsh avoids by caching runner selection for the provider lifetime (sandbox-local/src/index.ts:487-506).
- Sandbox the whole rollout worker and run the critic in-process inside it (isolation cost amortized to literally zero, ~10 us per tick). Rejected because the SBPL profile constrains file effects, not address space: the critic would still hold a live handle to the MuJoCo env and could mutate state, fabricate observations, or read privileged fields it never declared. This is the threat the kernel sandbox structurally cannot address, which is why dsh keeps ctx.sandbox and ctx.codeRuntime as two separate seams rather than merging them.
- RestrictedPython or a pruned-__builtins__ exec as the containment mechanism. Rejected: CPython has well-known escapes (object.__subclasses__, gc.get_objects, frame introspection, ctypes reachable through the object graph). dsh says a real V8 isolate with empty env, capped heap, and hard terminate() is still 'containment, not a security boundary'; a pure language-level restriction is strictly weaker and must never be presented as the boundary. It survives in this design only as defense-in-depth (the AST allow-list) inside an already-separated process.
- Keep the LLM proposer as the primary and the deterministic proposer as a fallback for when the API is down. Rejected because a fallback is never held to the primary's standard and rots the same way tool_plugin did. Making the deterministic proposer the default forces it to be genuinely capable, makes every campaign reproducible end-to-end, and turns proposer identity into a comparable experimental variable (search@v1 vs llm:<model>) recorded in the preregistration and covered by manifest_sha256.
- Put a p-value on the same-seed gate as the brief literally requested. Rejected as statistical theater: under verified bitwise determinism with a frozen parent and identical reset, the no-effect null predicts zero rescues with probability 1, so any rescue count yields p = 2^-b and 'significance' is guaranteed at b>=6 regardless of how trivial the change is. Replaced with the fit/gate seed split (a real within-cluster generalization claim), a Clopper-Pearson coverage interval, and a determinism canary that makes the determinism assumption checkable rather than assumed.
- Inherit Zetta's regression rule candidate_successes >= parent_successes (gating.py:246-248). Rejected: it is a net comparison, so a candidate that fixes four dev seeds and breaks four passes. On deterministic paired seeds a break is a fact, so the correct object is a breakage budget (default 0), not an aggregate inequality.
- Inherit the two-stage held-out design (heldout_10 then heldout_50) with alpha applied unchanged at both looks. Rejected: family-wise error is P(sig at stage 1) + P(not sig at 1 and sig at 2), strictly above the nominal 0.025. Replaced with a single fixed look at n=40; a preregistered two-stage design must spend alpha (O'Brien-Fleming one-sided, alpha1=0.005, alpha2=0.023).
- Inherit Zetta's 50 dev / 20 held-out seed budget. Rejected as a GPU-budget artifact: at 35.1 episodes/min on 8xA100 those numbers are expensive; at 212 episodes/min measured on this laptop, 150 dev seeds cost ~42 s and 40 held-out pairs cost ~23 s. The larger dev block is what makes clusters survive the fit/gate split, and n=40 lifts held-out power from 'detects r>=0.6' to 'detects r>=0.4 at ~80%'.
- Inherit Zetta's similarity metric (0.6 token cosine + 0.3 hashed text embedding + 0.1 raw state signature, threshold 0.72). Rejected: 0.9 of the weight lands on two encodings of the same harness-authored prose string, which is nearly a restatement of the hard-partition key, while the 0.1 physics term is an unnormalized mixed-unit cosine dominated by the largest-magnitude channel, and 0.72 is uncalibrated. The hard partition, complete linkage, deterministic tie-breaks, content-addressed segment ids, and None-divergence semantics are kept; the metric and the cut are replaced.
- Use privileged features (cube_pos, gripper_to_cube_pos) in the clustering feature vector since they are available in sim and would separate mechanisms more cleanly. Rejected: a cluster whose identity depends on simulator-internal state is not reproducible on a real robot, which would undercut the sim-to-real argument the privilege budget exists to make. Privileged features remain available to critics under a declared budget and to the ablation study, but not to the partition.
- Retry every infrastructure failure uniformly, as Zetta does. Rejected: critic_contract_violation is not transient and retrying converts a structurally broken candidate into a retry sink. Split into retryable (transient) and non-retryable (admission rejection), plus a per-arm infra-rate ceiling that voids a gate as INCONCLUSIVE_INFRA rather than scoring through flakiness.
- Enforce the atomic-change constraints by sending them to the proposer, as Zetta does (stages.py:1856-1870: one_causal_change, preserve_parent_rules_byte_for_byte, append_exactly_one_critic_recovery_pair). Rejected: a constraint sent to a model is a request, not enforcement, and it is unenforceable for a deterministic proposer too. Replaced with assert_atomic_delta() validated in the store before register_candidate, comparing source hashes of every parent rule.
- Port Cordis's fiber tree, effect graph, and typed event bus for the seams. Rejected per GOAL.md and the dsh seam analysis: for ~8 statically known seams, an ABC per seam plus one ExitStack of disposers per mounted component gets the whole load-bearing property (clean unmount/remount, registrations as effects) without thousands of lines of dependency-graph machinery.

## RISKS
- The 500 us p99 critic budget is a design target, not yet measured on this machine. macOS scheduler wakeup jitter on a 20-process (10 rollout + 10 critic) configuration over 18 cores could blow past it under load. Mitigation: a CI benchmark asserting median <200 us and p99 <500 us with the full worker fan-out, plus the throughput regression check (212 episodes/min must not drop below ~200). If the budget cannot be held, the fallback is NOT an in-process path but a batched-tick protocol (evaluate every k-th step with the intervening feature rows shipped in one message), which preserves the boundary and trades detection latency for throughput.
- The wall-clock critic timeout is the one nondeterministic element in an otherwise deterministic pipeline. It is contained by routing every timeout to infra_invalid (never to a scored outcome) and by the static op bound making it a backstop that should never fire, but a pathological candidate that times out near-deterministically on a loaded machine could make gates flaky. Mitigation: the per-arm infra-rate ceiling voids the gate rather than scoring it, and a candidate exceeding the ceiling is rejected at admission as non-serviceable.
- The fit/gate seed split halves the usable pairs per cluster, so only clusters with >=12 failing dev episodes are targetable. With dev=150 and ~55% failure this comfortably supports the dominant closed_empty_gripper cluster, but minor clusters (slip_after_grasp, lift_stall) may never become targets, biasing evolution toward the head of the failure distribution. Accepted for now and recorded in the report; the escape hatch is raising rollout_count (cheap at measured throughput), not shrinking the split.
- Held-out is report-only but is used as a two-generation stall detector, which is still selection pressure on the block (one bit per generation rather than one per candidate). Residual bias is small but nonzero and must be disclosed; the campaign-terminal confirm block exists precisely because the held-out number is no longer fully clean after ~25 rounds.
- n=40 held-out pairs give roughly 80% power at rescue rate r>=0.4 and are underpowered below r~0.25. A genuinely useful but modest critic can therefore fail to reach significance. This is a deliberate trade against alpha inflation; if it bites, the honest response is preregistering a larger block in the next campaign, never adding a second look at the same alpha.
- The determinism canary asserts reproducibility of 3 parent episodes per generation. If robosuite or MuJoCo has seed-dependent nondeterminism that only manifests on some seeds (e.g. contact-solver iteration counts), the canary can pass while a gate silently degrades to coin-flipping. Mitigation: canary seeds are drawn from the target cluster, not a fixed triple, and a sampled 5% of gate arms are re-run and hash-compared.
- The deterministic proposer optimizes the shadow-replay preflight objective exactly, so it is structurally prone to overfitting the detector to the fit seeds. The fit/gate split is the primary defense, but a threshold fitted to fit-seed quantiles can still transfer poorly. Watch: same-seed rescue rate on gate seeds materially below the fit-seed shadow recall is the diagnostic signal, and it should be reported per candidate.
- The privilege-tier-preferring search may pick a weaker P0 feature over a much stronger P1/P2 one and stall the campaign. The lexicographic preference is deliberate (docs/difficulty-calibration.md shows the P0 solution exists for this task) but it will not generalize to every failure class. Escalation must be a preregistered budget change, not an in-campaign decision, or the budget stops meaning anything.
- sandbox-exec is deprecated-but-present on macOS and is the only kernel-enforced file-effect boundary on Darwin (no bwrap/Landlock equivalent; App Sandbox needs code signing). If a future macOS removes it, the design fails closed and the loop stops rather than silently running unconfined - correct, but it is a hard platform dependency and should be stated in the README.
- CPython cannot forcefully interrupt arbitrary in-flight code from another thread, so the critic worker's timeout is enforced by killing and recycling the whole child process, not by aborting one evaluation. That makes a timeout expensive (worker respawn ~40 ms) and makes 'the substrate died' and 'it is still running something' indistinguishable - the same honesty dsh encodes in its worker-exit failure kind.

## SPEC
══════════════════════════════════════════════════════════════
1. LOOP STATE MACHINE
══════════════════════════════════════════════════════════════

Adopted from Zetta `store.py:24-56`: an explicit whitelisted adjacency map; `transition()` refuses any edge not in it; `state_updates` may never overwrite `phase` or `manifest_sha256`; `state.json` is the ONLY file written with overwrite=True; everything else is append-only or content-addressed. Adopted from dsh `agent-loop/src/agent.ts:38-46`: the cancellation scope is a FIELD OF THE PHASE, not of the supervisor, so `abort(cause)` is a no-op when the campaign is between phases and a stale abort can never poison the next phase; the cause enum is structured (`operator | budget | canary_failed | infra_ceiling | disposed`) and is written verbatim into the transition row.

```python
class Phase(StrEnum):
    INIT = "init"                    # manifest + preregistration + seed partition + canary
    ROLLOUT = "rollout"              # N_dev episodes with the current (parent) bundle
    SEGMENT = "segment"              # detectors -> FailureSegment[]
    CLUSTER = "cluster"              # partition + cut + rank + freeze targets
    PROPOSE = "propose"              # diagnose + propose -> CandidateDraft
    ADMIT = "admit"                  # atomic-delta + privilege + AST + feature contract
    PREFLIGHT = "preflight"          # shadow replay (read-only, zero sim cost)
    SAME_SEED_GATE = "same_seed_gate"
    REGRESSION_GATE = "regression_gate"
    HELDOUT_REPORT = "heldout_report"
    PROMOTE = "promote"
    CONFIRM = "confirm"              # terminal, once, third seed block
    COMPLETE = "complete"
    STALLED = "stalled"              # held-out gain <= 0 for 2 consecutive promotions
    ABORTED = "aborted"              # canary failure / infra ceiling / operator

ALLOWED_TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    INIT:            {ROLLOUT, ABORTED},
    ROLLOUT:         {SEGMENT, ROLLOUT, ABORTED},          # self-edge: infra retry
    SEGMENT:         {CLUSTER, COMPLETE, ABORTED},         # COMPLETE: zero failures
    CLUSTER:         {PROPOSE, COMPLETE, ABORTED},         # COMPLETE: no eligible target
    PROPOSE:         {ADMIT, CLUSTER, COMPLETE, ABORTED},  # CLUSTER: Exhausted -> next rank
    ADMIT:           {PREFLIGHT, PROPOSE, ABORTED},        # PROPOSE: rejected, refine
    PREFLIGHT:       {SAME_SEED_GATE, PROPOSE, ABORTED},
    SAME_SEED_GATE:  {REGRESSION_GATE, PROPOSE, CLUSTER, COMPLETE, ABORTED},
    REGRESSION_GATE: {HELDOUT_REPORT, PROPOSE, CLUSTER, COMPLETE, ABORTED},
    HELDOUT_REPORT:  {PROMOTE, ABORTED},                   # report-only: never rejects
    PROMOTE:         {ROLLOUT, CONFIRM, STALLED, ABORTED}, # ROLLOUT: next generation
    CONFIRM:         {COMPLETE, ABORTED},
    STALLED:         {CONFIRM, ABORTED},
    COMPLETE:        frozenset(),
    ABORTED:         frozenset(),
}
```

Phase-entry preconditions (checked by the supervisor, failure -> ABORTED{cause=canary_failed}):
- ROLLOUT / SAME_SEED_GATE / REGRESSION_GATE / HELDOUT_REPORT / CONFIRM: run the **determinism canary** — re-execute 3 target-cluster parent episodes, assert `actions_sha256` equals the frozen ledger value. This is `docs/verified-environment.md`'s `suite.make(seed=N)` requirement promoted from doc note to runtime gate.
- Every gate arm: `infra_invalid_rate <= 0.05`, else commit the gate as `INCONCLUSIVE_INFRA` and ABORT the round (never score through flakiness).

Backtracking budget on candidate rejection (Zetta `lifecycle.py:3318` arithmetic, kept verbatim in shape):
```
if len(all_rejected) >= max_total_candidate_rounds:            -> COMPLETE
elif len(rejected_for_this_cluster) < max_rounds_per_cluster:  -> PROPOSE   (refine same target)
elif next_rank exists and next_rank < max_target_clusters:     -> CLUSTER   (retarget rank+1)
else:                                                          -> COMPLETE
```
`PROPOSE -> CLUSTER` on `Exhausted` is our addition: the deterministic proposer can honestly run out of frontier, which an LLM proposer never signals.

Reconstruction invariant (Zetta): `state.json` + the append-only ledgers + the CAS suffice to answer "where am I" after a crash, with no in-memory state. The supervisor's `step()` reads phase from disk every tick — a cold ORIENT, never trusting memory.

Per-generation episode budget at measured 212 episodes/min:
```
ROLLOUT           150 candidate-bundle episodes            ~42 s
SAME_SEED_GATE    <= ~20 candidate arms (parent adopted)   ~6 s   (early-impossible terminates sooner)
REGRESSION_GATE   150 candidate arms (parent adopted)      ~42 s
HELDOUT_REPORT    40 pairs = 80 episodes (no adoption)     ~23 s
                                                           ~1.9 min per accepted generation
CONFIRM           60 pairs = 120 episodes, once            ~34 s
```
A 25-round campaign fits comfortably under an hour on this laptop.

══════════════════════════════════════════════════════════════
2. CRITIC RUNTIME SEAM (Definition / Provider / Consumer)
══════════════════════════════════════════════════════════════

```python
# governor/seams/critic_runtime.py   -- DEFINITION
class CriticRuntime(abc.ABC):
    provider_id: str
    scoring_safe: bool                       # False providers are refused when mode=='scoring'
    @abc.abstractmethod
    def open(self, bundle: CriticBundle, *, episode_seed: int,
             feature_schema: FeatureSchema) -> CriticSession: ...

class CriticSession(abc.ABC):
    @abc.abstractmethod
    def evaluate(self, values: Sequence[float], *, step_index: int) -> CriticOutcome: ...
    @abc.abstractmethod
    def close(self) -> CriticStats: ...      # p50/p99 latency, timeouts, op counts
```

`CriticOutcome` is a RETURNED discriminated union, never a raise — dsh `code-runtime/src/types.ts:79-107` ("error is a FIELD on a resolved result, never a rejection"):
```python
CriticOutcome = Proposals(tuple[Proposal, ...]) | CriticFailure(kind, message, step_index)
CriticFailureKind = Literal["timeout", "exception", "contract_violation",
                            "worker_exit", "output_invalid", "op_budget_exceeded"]
```
`evaluate()` raises ONLY for harness misuse (session closed; `len(values) != schema.arity`) — the caller/callee error-channel split dsh enforces on `CodeRuntime.run`.

**Provider `SubprocessCriticRuntime` (default, `scoring_safe=True`).**
- One confined CPython child per rollout worker, spawned at worker start, reused for the worker's lifetime. Confinement: `sandbox-exec -p '(version 1)(allow default)(deny file-write*)(allow file-write* (literal "/dev/null") (subpath "<realpath run_dir>") (subpath "/private/tmp"))' -- python -S -m governor.critic_worker`. Roots are `realpath`-canonicalized before being written into the profile (dsh `sandbox/src/roots.ts:20-33`: Seatbelt matches resolved paths; `/tmp` IS `/private/tmp`).
- Probe once per process lifetime, cache the verdict (dsh `sandbox-local/src/index.ts:487-506`). Probe = spawn the real read-only profile around a no-op and check exit 0. Probe failure -> refuse to run, never fall back unconfined.
- Wire protocol, per tick: `struct.pack("<IH", step_index, n)` + `n * float64` out; `struct.pack("<BH", kind, m)` + m proposal records in. The feature schema (ordered names + tiers + `schema_sha256`) is sent ONCE at `open`, so no dict serialization per tick.
- Compile once at `open`: AST validate -> `compile()` -> hold the code object. Per-rule dwell/cooldown state lives in the child, reset at `open`.
- Budgets, two orthogonal (dsh's computeMs/maxWallMs split, adapted so the PRIMARY budget is deterministic):
  - **Static op budget (primary, deterministic):** AST allow-list — no `while`, no `import` (a whitelisted `gmath` shim is preloaded), no recursion, no attribute access outside a whitelist, `for` only over declared fixed-length feature arrays, names in {open, eval, exec, `__import__`, getattr, globals, locals, compile, input} rejected. A static op-count bound `<= 2000` is then computable at admission.
  - **Wall-clock backstop (nondeterministic, must never fire):** 500 us soft / 5 ms hard. Hard expiry -> SIGKILL the child, respawn, return `CriticFailure("timeout")` -> episode becomes `infra_invalid`, never a scored outcome. CPython cannot safely interrupt in-flight code from another thread, so recycling the process is the only honest enforcement.
- Determinism enforcement in the child: `time`, `random`, `os`, `socket` unimportable; no wall-clock; no RNG.

**Provider `InProcessCriticRuntime` (`scoring_safe=False`).** Same compiled code object, no process boundary. `CampaignStore` refuses to register a scored episode produced by a provider with `scoring_safe=False`. Fail-closed, never a fallback (dsh `sandbox/src/index.ts:131`).

**Front end (not a runtime).** `governor.critic.dsl.compile_rule(RuleSpec) -> str` emits the same Python that any provider consumes. `RuleSpec` covers Zetta's grammar (feature/op/threshold/dwell/cooldown/activation_conditions) so every Zetta-expressible rule is expressible here, and the executor has no branch on origin.

**Latency budget, stated as a testable contract:**
```
sim step (Lift/Panda, measured, docs/verified-environment.md)     10.4 ms
critic budget                        <= 5% of step  ->  520 us p99, 200 us p50 target
  socketpair round trip + wakeup                          ~80-160 us
  compiled predicate eval                                 ~5-20 us
sandbox profile install + interpreter start (once/worker)  ~40 ms
  amortized over ~10^4 steps/worker lifetime               <5 us/step
REJECTED: fresh confined interpreter per tick   20-60 ms = 2-6x the sim step
REJECTED (for scoring): in-process eval  ~10 us, zero isolation
```
CI asserts p50 < 200 us, p99 < 500 us, and end-to-end throughput >= 200 episodes/min at 10 workers.

**Feature projection + the "critic-visible IFF logged" invariant** (dsh `agent-loop/src/invariant.ts:19-55`):
```python
class FeatureBroker:
    def project(self, obs, declared: tuple[FeatureRef, ...]) -> FeatureVector: ...
    # returns ordered float64 values + feature_vector_sha256
```
Every step appends to `features.jsonl` the exact ordered values and `feature_vector_sha256` that went over the wire. Shadow replay consumes THOSE bytes and asserts the hash before evaluating. A registrable diagnostics companion (enabled in sim/CI, disabled on hardware) re-projects from the log and compares hashes at every dispatch. This is GOAL.md acceptance #4 ("重建不变量真的会炸") and it closes Zetta's `required_critic_features_never_logged` hole.

**Privilege tiers** (from `docs/verified-environment.md`'s observation groups):
```
P0 proprio  : robot0_joint_pos/vel/acc, robot0_eef_pos/quat, robot0_gripper_qpos/qvel   (real-robot measurable)
P1 estimated: *_est features = P2 ground truth + calibrated sensor noise (sd=0.020)      (onboard perception proxy)
P2 privileged: cube_pos, cube_quat, gripper_to_cube_pos, ground-truth success            (sim only)
```
`PrivilegeBudget(max_tier: int, max_p2_features: int)` is preregistered. Admission rejects any declared feature above the budget. Undeclared feature -> absent from the wire -> `KeyError` in the child -> `contract_violation` -> candidate rejected, NOT retried.

══════════════════════════════════════════════════════════════
3. PROPOSER SEAM
══════════════════════════════════════════════════════════════

```python
@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str; dtype: str; shape: tuple[int, ...]
    privilege_tier: int                 # 0 | 1 | 2
    availability: Literal["always", "phase_gated"]

@dataclass(frozen=True, slots=True)
class SegmentEvidence:
    segment_id: str; episode_id: str; seed: int
    failure_class: str; phase: str
    earliest_divergence_step: int | None          # None = "not localized"; NEVER coerced to 0
    window: tuple[int, int]
    feature_window_ref: str                       # CAS sha256 of the projected rows
    severity: float

@dataclass(frozen=True, slots=True)
class ProposalBrief:
    manifest_sha256: str
    generation: int
    parent_bundle: BundleRef                      # hashes only; sources are CAS-addressed
    target: ClusterTarget                         # cluster_id, rank, target_sha256, fit_seeds ONLY
    evidence: tuple[SegmentEvidence, ...]         # fit seeds only; gate seeds are structurally absent
    success_controls: tuple[EpisodeRef, ...]      # valid successful episodes, for FP measurement
    feature_catalog: tuple[FeatureSpec, ...]
    privilege_budget: PrivilegeBudget
    recovery_catalog: tuple[RecoverySpec, ...]
    constraints: AtomicDeltaConstraints
    rejection_history: tuple[RejectedCandidate, ...]   # for THIS target: draft_sha256, reason, params
    brief_sha256: str

@dataclass(frozen=True, slots=True)
class CandidateDraft:
    draft_id: str
    mechanism_change: str
    critic_source: str                            # Python, compiled by the SAME executor
    recovery_source: str
    declared_features: tuple[FeatureRef, ...]
    declared_privilege_tier: int
    rationale: str
    provider_id: str
    provider_meta: Mapping[str, str]              # model, prompt_sha256, raw_output_ref (CAS)
    draft_sha256: str

class Exhausted(NamedTuple):
    reason: str                                   # first-class: drives PROPOSE -> CLUSTER

class Proposer(Protocol):
    provider_id: str
    requires_network: bool                        # recorded in preregistration
    def diagnose(self, brief: ProposalBrief) -> Diagnosis: ...
    def propose(self, brief: ProposalBrief,
                diagnosis: Diagnosis) -> CandidateDraft | Exhausted: ...
```

**Provider `SearchProposer@v1` (default, `requires_network=False`).**

`diagnose()` — feature ranking, deterministic:
1. For every `FeatureSpec` and every normalized phase bin b in {0.0-0.2, ..., 0.8-1.0} of the divergence window, collect the value population over target-cluster **fit** failures (n1) and over success controls (n2).
2. `U = R1 - n1(n1+1)/2` (Mann-Whitney, midranks for ties); `AUC = U / (n1*n2)`; separation score `s(f) = max_b |2*AUC(f,b) - 1|`.
3. Rank **lexicographically by (privilege_tier, -s)**: a tier-0 feature with s >= `admission_bar` (0.60) always outranks any tier-1/2 feature. Tier escalation requires the preregistered budget to allow it.
4. Emit `Diagnosis(ranked_features, phase_of_max_separation, divergence_step_distribution)`, content-hashed and cached by `target_sha256` across refinement rounds.

`propose()` — template + parameter search, deterministic:
- Templates: `T1 threshold_dwell(f, op, θ, dwell, cooldown, guard)`, `T2 stagnation(f, window, ε, guard)`, `T3 divergence(f_a, f_b, op, θ)`, `T4 rate(df/dt, op, θ, window)`, `T5 conjunction(two of the above)`. (T1 alone is the whole of Zetta's grammar.)
- θ scan over the empirical deciles+percentile-refinement of f within the fit-seed divergence windows; `dwell ∈ {1,2,3,5,8}`, `cooldown ∈ {0,5,10}`.
- Objective, verbatim the preflight admission rule so the proposer optimizes what the gate will apply:
  ```
  feasible  <=>  target_recall_at_or_before_divergence == 1.0
             and success_control_false_positive_rate   == 0.0
  score     =  median(divergence_step - first_trigger_step)      # lead time, MAXIMIZE
  tiebreak  =  (-dwell, -|θ - median_feasible_θ|, canonical_sha256(params))
  ```
  Zetta computes lead time (`shadow_replay.py`) and never optimizes it; a detector firing AT divergence gives the recovery nothing to work with.
- Recovery: static `failure_class -> RecoverySpec` table + a small parameter grid. For `closed_empty_gripper` the entry is `regrasp(open_width, retreat_dz, reacquire='current_obs')` — exactly the recovery `docs/difficulty-calibration.md` identifies, and it uses only P0 signals.
- **Novelty filter (mechanical, not prose):** reject any draft whose `(template, feature, operator)` matches a rejected draft for this target unless θ is outside every rejected θ ± one grid step. Zetta ships this as `must_materially_change_rejected_mechanism` in the LLM prompt (`stages.py:1866`) — a request, not a constraint.
- Frontier empty -> `Exhausted`.

**Provider `LlmProposer` (`requires_network=True`).** Consumes the identical `ProposalBrief`, emits the identical `CandidateDraft`, passes through the identical ADMIT/PREFLIGHT/gates. `temperature=0`; the brief, the prompt template hash, and the raw output are archived in the CAS, so the run is reproducible-as-evidence even though it is not reproducible-as-computation. It never returns `Exhausted`; the round cap does that job.

**Provider `EnsembleProposer`.** Search first; call the LLM only on `Exhausted`. Not the default — a campaign must be labelable `proposer=search@v1` to be a clean zero-API experiment.

Provider identity, version, and `requires_network` go into `preregistration.json` and are covered by `manifest_sha256`, making search-vs-LLM a comparable experimental variable rather than an implementation detail.

**ADMIT — mechanical enforcement of what Zetta requests in prose:**
```python
def assert_atomic_delta(parent: Bundle, cand: Bundle) -> None:
    # (a) every parent rule byte-identical by source sha256
    # (b) exactly one added critic rule; at most one added recovery
    # (c) zero removed rules
    # (d) frozen policy sha256 unchanged
    # (e) env_build_sha256 / manifest_sha256 unchanged
```
Plus: AST allow-list, static op bound <= 2000, declared-feature privilege <= budget, and the feature co-occurrence contract (every declared feature and every activation-condition feature must be present in at least one row of every replay trajectory AND in every row from that point onward — Zetta `lifecycle.py:1428-1527`, kept).

**PREFLIGHT — shadow replay.** Read-only over the logged `features.jsonl` (hash-verified, per D6), delta rule only, `len(delta_rules) == 1`. Metrics: `target_recall`, `success_control_false_positive_rate`, `lead_time_steps` distribution, `feature_schema_sha256`, `report_sha256`. `passed_detection_preflight = preflight_conclusive and recall == 1.0 and fp_rate == 0.0`. A `precommit.json` (`candidate_sha256`, `parent_bundle_sha256`, `shadow_report_sha256`, sorted target trajectory hashes) is written with **overwrite=False before any gate episode runs** — Zetta's preregistration-per-candidate, kept.

══════════════════════════════════════════════════════════════
4. GATE SPECIFICATION — EXACT STATISTICS
══════════════════════════════════════════════════════════════

**Seed partition (preregistered, frozen by `manifest_sha256`, disjointness asserted by construction):**
```
dev      = seeds   1..150     rollouts, segmentation, clustering, shadow replay, same-seed + regression gates
heldout  = seeds 1001..1040   per-generation report-only (mode=test)
confirm  = seeds 2001..2060   campaign-terminal, touched exactly once
assert dev & heldout == dev & confirm == heldout & confirm == {}
```
Held-out and confirm artifacts live under a path the brief builder is **structurally forbidden** to read (allow-list check + a test that deliberately violates it and is caught).

**Fit / gate split inside a target cluster (our addition; frozen with the target):**
```python
fit  = {s for s in cluster_seeds if int(sha256(f"{target_sha256}:{s}").hexdigest()[:2],16) <  0x80}
gate = cluster_seeds - fit
```
Target eligibility requires `|gate| >= same_seed_min_pairs = 6`.

**Shared statistics:**
```python
def one_sided_exact_mcnemar(b: int, c: int) -> float:
    """P[X >= b] for X ~ Binomial(b+c, 1/2). Conditional (exact) McNemar.
    b = #(cand success, parent fail); c = #(cand fail, parent success)."""
    d = b + c
    if d == 0: return 1.0
    return sum(math.comb(d, i) for i in range(b, d + 1)) / 2**d

def clopper_pearson_lower(x: int, n: int, alpha: float) -> float:
    """One-sided lower confidence bound on a binomial proportion."""
    return 0.0 if x == 0 else scipy.stats.beta.ppf(alpha, x, n - x + 1)
```
Identical in form to Zetta `gating.py:12-25`, which is correct and is kept.

**G1 SAME-SEED — causal intervention, DETERMINISTIC (no p-value; justified below).**
```
pairs   : gate seeds of the target cluster; N = |pairs| >= 6
parent  : ADOPTED from the frozen generation rollout ledger, never re-run (parent frozen)
required: ceil(N * same_seed_pass_rate),   same_seed_pass_rate = 0.5

pairing integrity (per seed; violation -> INCONCLUSIVE, not FAIL):
    seed equal  AND  policy_rng equal
    AND initial_state_sha256 equal          # bit-identical physical reset
    AND env_build_sha256 equal              # mujoco 3.3.7 + robosuite 1.5.2 + env kwargs
    AND both arms constructed via suite.make(seed=N)     # docs/verified-environment.md trap
mechanism attestation (per RESCUED seed; all must hold):
    actions_sha256(candidate) != actions_sha256(parent)  # trajectory actually diverged
    AND >=1 logged critic proposal AND >=1 logged recovery activation

rescued = {s : candidate[s].success and not parent[s].success}
broken  = {s : parent[s].success and not candidate[s].success}     # == 0 by construction; ASSERT

passed = (len(rescued) >= required)
     and (len(broken) == 0)
     and all(mechanism attestation for s in rescued)
     and (candidate_safety_events <= parent_safety_events)
     and (infra_invalid_rate <= 0.05)
report: rescue_rate = len(rescued)/N
        rescue_rate_lcb95 = clopper_pearson_lower(len(rescued), N, 0.05)
p_value = None ; conclusive = True
early-impossible: commit FAIL as soon as observed_rescues + remaining_pairs < required
```
*Why no p-value:* under verified bitwise determinism with a frozen parent and identical reset, a candidate that never intervenes produces a bit-identical action trajectory (checked directly by `actions_sha256`), so the no-effect null predicts `rescued == 0` with probability 1. Any rescue count then yields `p = 2^-b` — "significant" at b=6 no matter how trivial the change. The informative quantities are the coverage interval and the fact that gate seeds were withheld from the proposer. The determinism canary is what makes this argument checkable rather than assumed.

**G2 REGRESSION — deterministic breakage budget, non-inferiority on all 150 dev seeds.**
```
parent  : ADOPTED frozen; only the 150 candidate arms execute
b = #(cand success, parent fail)   # fixes
c = #(cand fail,    parent success)  # breaks
passed = (c <= regression_max_breaks)          # DEFAULT 0; nonzero requires explicit preregistration
     and (b >= 1)
     and (candidate_safety_events <= parent_safety_events)
     and (infra_invalid_rate <= 0.05)
p_value = None ; conclusive = True
```
Zetta uses `candidate_successes >= parent_successes` (`gating.py:246-248`) — a NET comparison, so fix-4/break-4 passes. On paired deterministic seeds a break is a fact, not a sample; the correct object is a budget.

**G3 HELD-OUT — the only inferential gate. Report-only (mode=test).**
```
seeds : 40 preregistered, disjoint from dev and confirm
arms  : BOTH executed freshly (parent adoption is DISALLOWED here)
single fixed look; NO interim analysis
b = candidate_wins ; c = parent_wins
p       = one_sided_exact_mcnemar(b, c)
alpha   = 0.025 (one-sided)
gain    = candidate_successes - parent_successes
conclusive = (p < alpha)
significant_improvement = conclusive
                      and gain >= heldout_min_gain (= 3)
                      and candidate_success_rate >= parent_success_rate
                      and candidate_safety_events <= parent_safety_events
```
**Authority (Zetta `lifecycle.py:4682-4712`, `store.py:345-400`, kept):** in `mode=test` HELDOUT_REPORT always advances to PROMOTE regardless of the result; promotion requires only G1 and G2 to have passed. The held-out number is a recorded, unbiased-per-generation estimate that is never selected on. `mode=validation` (must be preregistered before any episode runs) makes it a rejecting gate.
**Stall detector (our addition, one bit per generation):** two consecutive promoted generations with `gain <= 0` -> `STALLED` -> `CONFIRM`.

Evidence arithmetic (properties of the exact test at alpha=0.025, strict `<`):
```
(6,0) -> 1/64  = 0.015625  PASS   <- minimum passable evidence; this fixes same_seed_min_pairs = 6
(5,0) -> 1/32  = 0.03125   FAIL
(9,1) -> 10/512= 0.01953   PASS
(8,1) -> 9/256 = 0.03516   FAIL
Power at n=40, base fail rate 0.55, rescue rate r:  r=0.4 -> E[b]~8.8, typical p~0.002, power ~0.8
                                                    r=0.25-> E[b]~5.5, marginal
n=20 (Zetta's block) reaches ~0.8 power only at r >= 0.6.
```
If a two-stage design is ever preregistered, alpha MUST be spent (O'Brien-Fleming one-sided at t=(0.5,1): alpha1=0.005, alpha2=0.023). Zetta's `evaluate_two_stage_heldout` reuses the same alpha at both looks; family-wise error is `P(sig at 1) + P(not sig at 1 and sig at 2) > 0.025`.

**G4 CONFIRM — campaign-terminal, run exactly once.**
```
seeds : 60, never touched by anything else
arms  : final promoted bundle vs GENERATION-0 parent, both executed fresh
same exact test, alpha = 0.025 one-sided, min_gain = 5
ALSO REQUIRED for every promoted critic rule:
    ablation_zero_privilege.json  -- the same bundle re-run with every P1/P2 feature withheld,
    reporting success rate and the delta.  (GOAL.md acceptance #3.)
```
This is the headline number and the only one that survives 25 rounds of loop coupling.

**Infrastructure-failure taxonomy (excluded from scoring; dsh's orthogonal-kind precedent):**
```python
EpisodeOutcome = Scored(success: bool) | InfraInvalid(kind)
InfraKind = Literal["env_construction_failed", "sim_instability",      # retryable, same seed+policy_rng
                    "critic_timeout", "worker_exit",                    # retryable
                    "artifact_write_failed",                            # retryable
                    "critic_contract_violation",                        # NOT retryable -> candidate rejected
                    "determinism_canary_failed"]                        # NOT retryable -> campaign ABORTED
```
`InfraInvalid` rows go to `attempts.jsonl` ONLY, never to `episodes.jsonl` (Zetta `store.record_episode`). `max_infrastructure_attempts = 2`. Per-arm `infra_invalid_rate > 0.05` -> gate committed as `INCONCLUSIVE_INFRA`, round aborted, never scored.

══════════════════════════════════════════════════════════════
5. FAILURE CLUSTERING — CONCRETE ALGORITHM AND FEATURES
══════════════════════════════════════════════════════════════

**Stage 0 — Segmentation** (Zetta `trajectory.py:280-652`, adapted). Priority-ordered detectors over the merged event stream, run ONLY on `success is False` episodes; the first (lowest-priority-number, lowest-step) signal wins per `(failure_class, phase)` key. Failure classes grounded in `docs/difficulty-calibration.md`:
```
p0  safety_violation      out-of-bounds / joint-limit / force-limit event        severity 1.00
p1  closed_empty_gripper  finger_gap < 0.005 at end of the close phase           severity 0.95
      (measured separation: success 0.038-0.046 vs failure 0.0010-0.0012, 40x, PURE P0)
p1  critic_reject         a critic fired and its recovery did not restore progress severity 0.95
p2  missed_approach       eef never within r of the object at descend end         severity 0.90
p2  slip_after_grasp      finger_gap collapses after a valid enclosure            severity 0.90
p3  lift_stall            grasped but eef_z fails to rise over the lift phase      severity 0.85
p3  window_no_progress    fallback only if no structured signal: first window of 8 obs
      whose progress-scalar range <= max(1e-4, |v0|*1e-3), gated on prior progress  severity 0.80
p4  horizon_incomplete    emitted ONLY when nothing else fired                     severity 0.60
      (Zetta's rationale, kept: attaching it universally manufactures a 100%-prevalence pseudo-cluster)
```
`earliest_divergence_step: int | None` — `None` means "failed, but the evidence does not localize the first divergence"; it is NEVER coerced to 0 (Zetta `models.py:245`: doing so poisons shadow-replay lead-time statistics). Window = `[max(0, d-8), min(end, d+8)]`; if `d is None`, the last 16 steps, and the segment is excluded from centroid computation but still assigned. `segment_id = "seg-" + sha256(episode_id, failure_class, phase, d, start, end, artifact_digest_map)[:24]` — content-addressed over immutable artifact hashes.

**Stage 1 — Hard partition (Zetta's, kept verbatim in intent).** Bucket by the exact pair `(failure_class, phase)`. Segments in different buckets can NEVER merge, at any similarity. Different mechanisms are different clusters by construction.

**Stage 2 — Physical feature vector (replaces Zetta's prose similarity).**
Base signals, **P0 only** (a cluster whose identity needs privileged state is not reproducible on real hardware):
```
s1 finger_gap = gripper_qpos[0] - gripper_qpos[1]
s2 d(finger_gap)/dt
s3 eef_z
s4 d(eef_z)/dt
s5 ||eef_linear_velocity||
s6 ||joint_velocity||
s7 commanded gripper action
s8 ||eef_pos(t) - eef_pos(window_start)||
```
Per signal, 4 statistics over the window: `mean`, OLS `slope`, `range`, `value_at_divergence` -> 32 dims. Plus `d / horizon` and `severity` -> **z ∈ R^34**.
Standardization (this is the fix for Zetta's scale-dominated raw cosine): each dimension is z-scored against mean/std computed over ALL segments of the generation (not per bucket — unstable at small n), `std` floored at 1e-6; then the vector is L2-normalized. The z-score parameters are written into the cluster report and covered by `cluster_report_sha256`, so the partition is exactly reproducible.

**Stage 3 — Distance and linkage.** Euclidean on the L2-normalized vector (a proper metric, so the agglomeration is well-defined), **complete linkage** — Zetta's documented rationale is kept: complete link stops an A~B~C chain from merging dissimilar A and C. Greedy agglomeration; ties broken deterministically by the sorted tuple of member `segment_id`s (Zetta's determinism trick, kept).

**Stage 4 — The cut (replaces the uncalibrated 0.72 threshold).** Build the full dendrogram per bucket; choose
```
k* = argmax_{k in [1, min(6, n-1)]} silhouette(k)
if max silhouette < 0.15:      k* = 1        # the data does not support splitting this bucket
merge back any cluster with < 3 members into its nearest sibling by complete-link distance
```
Cluster granularity is thereby tied to gate feasibility rather than to a magic number.

**Stage 5 — Ranking, eligibility, freeze.**
```
rank by (-unique_episode_count, -mean_severity, -prevalence, cluster_id)
eligibility: |gate_seeds(cluster)| >= 6          # derived from the exact test's 6-0 minimum
top max_target_clusters = 2 eligible clusters become ranked targets
target_sha256 = canonical_sha256({manifest_sha256, cluster_report_sha256, cluster_id,
                                  rank, episode_ids, member_segment_ids, fit_seeds, gate_seeds})
written once with overwrite=False; re-running MUST reproduce the identical hash or raise
ranking_authority = "harness_unique_failure_episode_count"     # a proposer can never steer the campaign
```
Representative selection: medoid by summed within-cluster similarity, plus top-3 by `(-severity, -pair_score, segment_id)` (Zetta's, kept).
Optional LLM re-partition (Zetta's multimodal cluster review): permitted only if its output carries `deterministic_source_sha256` equal to the hash of the deterministic report, and segment ids are round-tripped through blinding aliases so the model never sees raw ids or seeds. Disabled by default (zero-API).

══════════════════════════════════════════════════════════════
6. ARTIFACT LAYOUT
══════════════════════════════════════════════════════════════

```
runs/<campaign_id>/
  manifest.json                     # frozen; manifest_sha256 covers every preregistered field
  preregistration.json              # protocol dataclass, seed partition + sha, proposer provider_id/
                                    #   version/requires_network, critic runtime provider_id,
                                    #   harness git sha, env_build_sha256 (mujoco 3.3.7 / robosuite
                                    #   1.5.2 / env kwargs), alpha / min_gain / budgets, privilege budget
                                    #   -- written ONCE with overwrite=False
  state.json                        # the ONLY overwrite=True file: {phase, generation, candidate,
                                    #   manifest_sha256, current_bundle_sha256, abort_cause?}
  ledgers/
    episodes.jsonl                  # append-only; SCORED episodes only
    attempts.jsonl                  # append-only; EVERY dispatch, incl. infra_invalid
    gates.jsonl                     # append-only; one row per committed GateDecision
    transitions.jsonl               # append-only; one row per phase edge {from,to,cause,ts,state_sha}
  cas/<aa>/<sha256>                 # content-addressed blobs, immutable:
                                    #   states.jsonl.zst, actions.jsonl.zst,
                                    #   features.jsonl.zst  <- the EXACT projected vectors sent to the
                                    #                          critic (D6: critic-visible IFF logged)
                                    #   proposals.jsonl, events.jsonl, critic/recovery sources
  generations/g0000/
    bundle.json                     # effective bundle = parent + atomic delta; bundle_sha256
    bundle/critics/<rule_id>.py     # ACTUAL critic source (CAS-addressed, referenced not copied)
    bundle/recoveries/<rec_id>.py
    privilege.json                  # per rule: declared features, tiers, budget consumption
    canary.json                     # 3 parent episodes re-run; actions_sha256 comparison
    analysis/segments.json
    analysis/clusters.json          # partition + z-score params + silhouette curve + cluster_report_sha256
    analysis/cluster_targets.json   # frozen targets incl. fit/gate seed split; overwrite=False
    analysis/diagnosis-<target_sha256>.json      # cached across refinement rounds
    candidates/c0001/
      brief.json                    # the EXACT ProposalBrief handed to the proposer; brief_sha256
      draft.json                    # proposer output verbatim; draft_sha256, provider_meta
      raw_provider_output           # CAS ref (LLM only)
      admit.json                    # assert_atomic_delta + AST + privilege + op-bound results
      contract.json                 # feature co-occurrence contract check
      shadow_replay.json            # recall / FP rate / lead-time distribution / report_sha256
      precommit.json                # overwrite=False BEFORE any gate episode:
                                    #   {candidate_sha256, parent_bundle_sha256, shadow_report_sha256,
                                    #    sorted target trajectory sha256s, gate_seeds}
      gates/same_seed.json
      gates/regression.json
      gates/heldout.json
      critic_latency.json           # p50/p99 per-tick, timeouts, op counts -- the budget contract
  confirm/
    confirm.json                    # terminal exact test, final vs generation-0
    ablation_zero_privilege.json    # GOAL.md acceptance #3
  report.md                         # generated; never hand-edited
```

Every `EpisodeRecord` carries `manifest_sha256`, `bundle_sha256`, `seed`, `policy_rng`, `initial_state_sha256`, `actions_sha256`, `feature_schema_sha256`, `artifact_index: {name -> sha256}`, and `critic_runtime_provider_id`. `record_episode` REJECTS a record whose `bundle_sha256 != state.current_bundle_sha256` or whose seed/policy_rng is not preregistered, or whose provider has `scoring_safe=False` (Zetta's validation, extended).

══════════════════════════════════════════════════════════════
7. SEAM INVENTORY (Definition / Provider / Consumer, plain Python)
══════════════════════════════════════════════════════════════
```
EnvProvider          suite.make(seed=N) only; global np.random.seed is a hard error (regression test)
PolicyProvider       frozen; policy_sha256 is part of the bundle
CriticRuntime        SubprocessCriticRuntime (default) | InProcessCriticRuntime (dev, scoring-refused)
Sandbox              SeatbeltSandbox; probe once per process lifetime, cache; fail-closed, never degrade
FeatureBroker        projection + privilege tier enforcement + feature_vector_sha256
Proposer             SearchProposer@v1 (default) | LlmProposer | EnsembleProposer
GateRunner           G1/G2/G3/G4, parent adoption policy per gate kind
CampaignStore        state.json + ledgers + CAS; the only writer; whitelisted transitions
```
Each is an `abc.ABC`; each provider's registrations return disposers collected on one `contextlib.ExitStack` per mounted component (dsh "registrations are effects", minus the Cordis fiber tree — per the seam analysis's explicit port guidance). No event bus; direct calls between explicitly injected services.


======================================================================
# VERSION 3 (post-critique, authoritative)
======================================================================

## QUESTION
What is the capability seam set for the Governor physical harness (Python), and what is the concrete Python mechanism for provider registration, reversal, resolution, and composition configuration — in a version that actually runs and is verified tonight on macOS arm64, CPU-only MuJoCo?

## DECISION
The original does not survive. Its seam *shape* is mostly right; its **privilege boundary is in the wrong place**, and it reintroduces — structurally — the exact bug this repo already found empirically and recorded as its #1 design constraint. Revised: **SIX seams**, one privilege channel, ~40 lines of mechanism, no plugin framework.

**The fatal flaw first.** In the reviewed design, `RecoveryHandle.next_action(self, obs: RawObservation, base_action)` takes the **raw observation**. The `FeatureProvider`/`MaskedFeatures` privilege boundary therefore sits only in front of the critic. `PrivilegeAblationGate` patches `{"id":"features","config":{"max_tier":0}}`, masks the critic, and leaves recovery free to call `obs["cube_pos"]`. That is verbatim the bug in `docs/headline-finding.md` (commit ddd5575): *"+50% 那一行是假的。它靠的是 recovery 里一句 `target = obs["cube_pos"]`"*, whose stated lesson was *"特权预算必须同时覆盖 critic 和 recovery"*. The design would report a fabricated +50% and label it zero-privilege. This is the whole project's thesis inverted by one type signature.

**Revised seam set (6).** Each is a `Service` owning one `Context` slot.
1. `env` — **Environment**: seeded world, raw obs, `reset_identity()`. Provider: `RobosuiteEnv` (wraps existing `governor/env.py`).
2. `percept` — **Percept**: THE single privilege boundary, and the one seam that changed. Raw obs → `PerceptFrame` carrying both `values` (tier-tagged scalars, for the critic) and `estimates` (tier-tagged vectors, for recovery). Critic and recovery see the world **only** through this. Providers: `FullPercept` / `MaskedPercept(inner, max_tier, estimate_noise_sd, rng)` — which drops over-tier names from **both** maps and injects noise into estimates. This is the ablation mechanism and the only second provider that earns its place.
3. `policy` — **BasePolicy**: frozen black box, content-hashed identity. Provider: `OpenLoopScriptedPolicy` (existing `FrozenPolicy`). Keeps `RawObservation` deliberately — the policy's own bad sensor *is* the difficulty; it is not under governance and not in the budget.
4. `critic` — **CriticRuntime**: compile a bundle, evaluate per step, return `CriticEval` with `error` as a **field**. Provider: `RuleCriticRuntime`, `enforcement="interpreted"` (honest three-value string kept).
5. `recovery` — **RecoveryExecutor**: verdict → bounded action sequence, consuming `PerceptFrame`, never raw obs. Providers: `ScriptedRecovery`, `NoopRecovery` (detection-only arm).
6. `store` — **EpisodeStore**: append-only JSONL, `seq == len`, two timestamps. Provider: `JsonlStore`.

**Demoted from seams to plain functions/data** (they had no second provider that wasn't written solely to prove the seam): `rollout` → `run_jobs(jobs, workers)`; `proposer` → `propose()` over existing `governor/search.py`; `analyzer` → `failures()`, a filter; `gates` → a plain `dict[str, Gate]` of three gate objects.

**Mechanism.** `Context` dataclass, one `Optional` slot per seam. `ctx.provide(service)` asserts the slot is empty. `ctx.require("percept")` raises `SeamUnavailable`. Composition is an ordered flat list of pure-data `Row{id, factory, config}` folded over `base + overlay`, whole-field replace, unknown id = hard error. `Harness(rows).boot()` → `Context`; `close()` unwinds LIFO. **No `Disposer` return, no `effect()`, no `remount()`, no `Registry`, no `Settings`, no patch files, no `--set`, no `config --dump`** — the ablation arm is a **fresh `Harness` per arm**, which is what the process pool does anyway, so in-process reversal has no second caller. The composed tree is canonical-JSON hashed into `config_sha256`, written into every episode header, and is the only thing crossing a process boundary.

**Two anti-deception changes that are the point of the revision.**
- **Seed count, not privilege, is what buys significance.** The repo treats `+13.3%, p=0.057, 不显著` as the honest ceiling. That is an n=60 artifact. I recomputed the exact one-sided McNemar on the recorded discordant counts and scaled the *same effect rate*: `n=60 → p=2.9e-2 (fails alpha=0.025)`, `n=120 → 1.9e-3`, `n=240 → 1.0e-5`. Measured cost on this machine: **457 episodes/min** (10 workers, verified below), so n=240 × 2 arms = **~2.4 minutes**. The design as written leaves the campaign structurally incentivized to promote the privileged critic because only the privileged arm clears the gate. Raising seeds removes that incentive for the price of a coffee.
- **Three-way preregistered seed partition with a once-only test ledger.** The design gates on held-out every generation with `alpha=0.025` and a `heldout_mode` knob that can downgrade a failing gate to report-only. That is N-generation multiple testing on the claim GOAL acceptance #2 rests on. Revised: `dev` (search only) / `val` (gated every generation) / `test` (queried **exactly once**, enforced by a persisted counter that refuses a second read). The `heldout_mode` knob is deleted.

**Tonight's deliverable** is not a framework; it is `governor campaign` emitting the privilege-ablation curve automatically at n=240 across `estimate_noise_sd ∈ {0.00, 0.01, 0.02, 0.03, 0.04}` and `max_tier ∈ {PROPRIO, EXTERO, PRIVILEGED}`, plus the deliberate-violation invariant test. Reuses `env.py`, `features.py`, `search.py` unchanged in substance. ~900-1200 lines including tests.

## RATIONALE
Everything below was measured on this machine tonight, not reasoned about.

**1. Platform — what would actually have failed.**

- **`EnvSpec.group_tiers` is factually wrong about robosuite.** The design asserts the obs dict is grouped and that `robot0_proprio-state` / `object-state` are the tier authority. Verified: the dict is **flat** — 13 individual keys (`cube_pos`, `robot0_gripper_qpos`, …) sitting *alongside* two concatenated vectors `robot0_proprio-state (50,)` and `object-state (10,)`. There is no group→key mapping in the observation. The real per-key authority is `env._observables[name].modality`, verified to return `'robot0_proprio'` / `'object'` for all 13 — a **private** robosuite attribute. So the "single authority" must be `modality`, read once at boot and pinned by a regression test against robosuite 1.5.2, not group names.
- **`multiprocessing` start method is `spawn`** (verified). My throughput probe **forkbombed to 14 processes** because the calling script lacked `if __name__ == "__main__"` — the existing `governor/parallel.py` re-enters `rollout_many` in every spawned child. `ProcessPoolRolloutExecutor` inherits this exactly. With the guard added: **40 eps in 5.2s = 457 eps/min**, deterministic across repeat runs, baseline 22/40 at sd=0.020 — double the 212 eps/min in STATUS.md. Compute is a non-issue; engineering time is the only scarce resource, which is the whole argument for cutting seams.
- **Python version trap.** System `python3` is **3.14.6 with no numpy/mujoco/robosuite**; the venv is 3.12.13 and `pyproject` pins `<3.13`. Any subprocess spawned as `"python3"` rather than `sys.executable` silently gets the wrong interpreter. The design's worker entrypoint must use `sys.executable`.
- **`sandbox-exec` works** (Darwin 26.3.2): confined python ran, `open(...,'w')` → `PermissionError`, `socket.create_connection` → `PermissionError`, `import numpy` fine. So `SandboxedPythonCriticRuntime` is genuinely feasible — it is cut for schedule, not feasibility, and `enforcement` stays a three-value string so the day-one runtime cannot lie.
- **`timeout(1)` is absent** (no coreutils). Any harness script shelling out to `timeout` fails.
- **No CUDA is required anywhere in the day-one path** — correct, and the design's avoidance of torch is right. `BCCheckpointPolicy` would need torch/MPS; correctly deferred.
- **EXTERO earns its place, for a reason the design did not give.** Offscreen camera rendering is off (`use_camera_obs=False`; STATUS forbids `MUJOCO_GL=osmesa`), so there is no real camera. But the headline experiment's honest arm did **not** use proprioception for recovery — it used *ground-truth cube pose degraded by noise* as a surrogate for onboard perception. That surrogate must be named as its own tier rather than smuggled in as either PROPRIO or PRIVILEGED. So: PROPRIO = present in obs, real-robot-measurable; EXTERO = ground-truth-derived but only reachable through `Percept.estimate` with a declared `estimate_noise_sd`, modelling onboard sensing; PRIVILEGED = exact ground truth. The promoted-bundle report must print tier **and** noise sd.

**2. Verifiable tonight — no.** Ten seams × ≥2 providers, plus `Harness`/`compose`/patch layers/CLI/campaign state machine/4 gates/analyzer with agglomerative clustering/sandboxed worker is 3-5k lines against 501 lines of existing code. The smallest thing producing a real observable result is the spine that reproduces the round-1 handwritten number **automatically** — which is literally STATUS.md's own next step (`search → trigger → governed rollout → paired McNemar → 特权消融曲线`). Six seams, one provider each plus `MaskedPercept` and `NoopRecovery`, a straight-line campaign instead of a state machine.

**3. Self-deception — five vectors, ranked.**
- **(a) The recovery privilege hole**, above. Fatal, and already empirically demonstrated in this repo.
- **(b) `PrivilegeAblationGate` cannot fail in the shipped config.** `base.yaml` sets `features.max_tier: 2` but `proposer.budget_cap: 0`. If the candidate only reads tier-0 features, `MaskedFeatures(max_tier=0)` is the **identity** on everything the critic reads; both arms are bit-identical; "the ablated arm reproduces the full arm's rescues" is trivially true; the gate passes every time and certifies nothing. A gate whose pass condition reduces to an identity must return `conclusive=False`/`not_applicable`, never `passed`. Revised gate asserts the two arms materially differ (different `config_sha256` **and** at least one dropped name that appeared in `features_read`, **or** a raised `estimate_noise_sd`) before it is allowed to render a verdict.
- **(c) `features_read` is tautological for the only day-one runtime.** `RuleCriticRuntime` evaluates a rule whose feature list is in the rule itself; `RecordingFrame` "measures" what the AST already declares. It is honest bookkeeping, not independent evidence — it only bites for the sandboxed provider that does not exist tonight. Keep it, report it as bookkeeping, and let the ablation gate be the authority. (The design says this in risk #1 while the rationale implies the opposite; I keep the risk's version.)
- **(d) Repeated held-out testing.** Gating on held-out at `alpha=0.025` every generation and promoting the first pass gives a real false-positive rate near N×0.025 on precisely the claim GOAL #2 makes. Fixed by dev/val/test with a once-only test ledger; `heldout_mode` deleted.
- **(e) `RegressionGate` parent-evidence reuse** without asserting identical `config_sha256`-except-bundle-row and identical per-seed `reset_identity()` silently unpairs the comparison — the same class of failure as commit d405186's `np.random.seed` bug. Assertion added.

**4. Complexity not earned.** The design's own risk #10 concedes the disposer machinery may be dead weight; risk #8 concedes `analyzer` and `rollout` are likely premature. I agree with both and act on them. `analyzer` clusters one failure mode (`finger_gap`) — complete-link agglomeration at 0.72 with hashed embeddings over a single cluster is unfalsifiable ceremony. The seam-completeness rule ("a seam is real only with a second provider differing in kind") was satisfied for most seams by providers written *in order to* satisfy it — `ReplayEnvironment`, `ReplayPolicy`, `ReplayFeatures`, `InMemoryStore`, `FixtureProposer`. A seam proven only by a provider that exists to prove it is not proven. The two second-providers that survive (`MaskedPercept`, `NoopRecovery`) survive because the ablation gate and the detection-only arm actually call them tonight.

**What survives from the original, unchanged and good:** failures as returned fields vs misuse raising; `enforcement` as a three-value string with fail-closed `compile()`; two timestamps (`mono_ns` authoritative); blobs by reference; seed binding pinned in the `reset()` docstring; `reset_identity()` hoisted into the seam; `EpisodeRunner` deliberately not a seam; whole-field replace with unknown-id-is-a-hard-error; pure-data config verified by canonical-JSON round trip at mount; `GateDecision.__post_init__` refusing `passed and not conclusive`; config hashed into every episode header. Those are the load-bearing parts and I kept all of them.

## REJECTED
- The original's `features` seam with recovery taking RawObservation. Rejected as the fatal flaw: it places the privilege boundary in front of the critic only, so the ablation gate masks the critic while recovery reads `obs['cube_pos']` — the exact fabricated +50% documented in docs/headline-finding.md and commit ddd5575. Replaced by a single `percept` seam that both critic and recovery must go through.
- `EnvSpec.group_tiers` keyed on observation groups as the tier authority. Rejected on measurement: robosuite 1.5.2's obs dict is flat; `robot0_proprio-state`/`object-state` are concatenated vectors, not groups. Authority is `env._observables[name].modality` ('robot0_proprio' | 'object'), read at boot and pinned by a regression test.
- The `analyzer` seam (segment + cluster, hashed embeddings, complete-link agglomeration at 0.72, medoid representatives, LlmClusterReviewer, deterministic_source_sha256). Rejected: this task has one failure mode. Clustering a single cluster produces a number that cannot be wrong, which is the definition of unearned machinery. Replaced by a ~20-line `failures()` filter. Promote to a seam when a second failure mode is measured, not before.
- The `rollout` seam. Rejected: its stated justification is that a second provider forces pure-data jobs — but a frozen dataclass of scalars achieves that with zero seam. Collapsed to `run_jobs(jobs, workers)`. Kept the real lesson: `sys.executable`, an explicit spawn context, and a mandatory `__main__` guard, because the unguarded version forkbombed this machine tonight.
- `Disposer` returns, `ctx.effect()`, `Harness.remount()`, `Registry[T]`, `Settings` with getter thunks, patch files, `--set` CLI overlay, `governor config --dump`. Rejected: the ablation arm is a fresh Harness per arm (which the process pool requires anyway), so in-process reversal has exactly zero non-test callers. The design's own risk #10 predicted this. Kept `provide`/`require`/`close`.
- `SandboxedPythonCriticRuntime` tonight. Verified feasible — sandbox-exec on Darwin 26.3.2 blocked write and network while numpy imported fine — but it is a night's work by itself and the rule runtime needs no code path. Deferred, with `enforcement="interpreted"` reported honestly so no bundle can claim containment it lacks.
- `ReplayEnvironment`, `ReplayPolicy`, `ReplayFeatures`, `InMemoryStore`, `ZstdShardedStore`, `ResidualRecoveryExecutor`, `BCCheckpointPolicy`, `LlmProposer`, `FixtureProposer`. Rejected as seam-completeness theater: providers written to satisfy the two-provider rule rather than because something calls them. 60k events of JSONL is not a compression problem.
- `heldout_mode in {test, validation}` controlling whether a gate may reject. Rejected: a knob that converts a failing gate into a report is the most direct self-deception available. The preregistered seed partition decides, and the test split is readable exactly once.
- Accepting `+13.3%, p=0.057, 不显著` as the honest ceiling and letting the privileged arm be the only one that passes. Rejected on arithmetic: exact one-sided McNemar on the recorded discordant counts scaled at constant effect rate gives n=120 → 1.9e-3, n=240 → 1.0e-5, at a measured cost of ~2.4 minutes. The design's gate would have promoted the privileged critic for want of five minutes of CPU.
- `SurfaceOp`/`source_seqs` surface projection and `EpisodeStore.repair()`. Rejected for tonight: surface projection matters for a human-facing agent transcript, and repair matters for a multi-hour campaign; this campaign is five minutes and has no reader but the gate. Event types stay namespaced so the surface split can be added later without a log migration.
- `typing.Protocol` for seam Definitions, entry_points discovery, a DI container, pydantic, deep-merge patches, a generic event bus, a `sandbox` seam split, a `Tool` registry, hot-reloadable settings. All rejected for the original's reasons, which I checked and agree with — these are the parts of the original analysis that hold up unmodified.

## RISKS
- The EXTERO tier is a modelling surrogate, not a sensor. `estimate_noise_sd` on ground-truth cube pose stands in for onboard perception that does not exist in this observation set. It is defensible and it is what the recorded experiment actually did, but every promoted-bundle report must print 'EXTERO estimate, ground truth + N(0, sd)' verbatim. If that phrasing is ever dropped, the harness is back to claiming a real-robot result it cannot support.
- `env._observables[name].modality` is a private robosuite attribute. A robosuite upgrade can rename or remove it and the tier map silently degrades. Mitigation: read it once at boot, assert the exact expected name→modality map for robosuite 1.5.2 in a pinned regression test, and fail closed on any unknown key rather than defaulting a tier.
- Raising to n=240 seeds assumes the effect RATE measured at n=60 (11 fixed / 3 broken) is unbiased. It is a small sample and the true rate could be lower; the honest arm could still miss at n=240. This is a real possibility, not a formality — but it is the correct experiment either way, and if it misses, the finding is 'zero-privilege recovery does not clear the bar at this noise level', which is publishable and honest, rather than a privileged number dressed up.
- The once-only test ledger is enforced by a persisted counter, which any developer can delete. It is a speed bump against accidental p-hacking, not a security control. The counter and its read count must be written into the campaign manifest and printed in the final report so a second read is at least visible.
- `features_read` measured through RecordingFrame remains defense in depth, not proof. A rule runtime's read set is implied by its own AST; derived leakage and cross-step caching are undetectable by it. The ablation gate is the authority and the report must say so rather than implying the declaration was verified.
- Cutting the `analyzer` and `rollout` seams means a second failure mode or a second machine forces real refactoring rather than a provider swap. Accepted deliberately: both are additive (new module, new call site) rather than invasive, and the cost of carrying them unused tonight is higher than the cost of adding them when a second provider actually exists.
- The spawn-guard requirement is a convention, not a type. Any future script that calls `run_jobs` at module top level forkbombs the machine exactly as my probe did tonight. Mitigation: `run_jobs` asserts `multiprocessing.current_process().name == 'MainProcess'` and raises immediately in a child, converting a fork bomb into a stack trace. This must be a test, because it already happened once.
- Merging the critic's scalar features and recovery's vector estimates into one `Percept` seam couples two consumers with different cadences (per-step vs per-recovery-begin). If a future recovery needs a percept the critic must not see, or vice versa, the seam splits. That is the right trigger for a split; splitting now would recreate the privilege hole this revision exists to close.
- Deferring the sandboxed runtime means GOAL's '从第一天就要能跑沙箱代码' is not met on day one. This is a real, named deviation from the stated goal, not an oversight. It is deferred because the rule runtime plus the search proposer are sufficient to produce the evolution result tonight, and seatbelt confinement is verified feasible so the deferral carries no discovery risk — only schedule.
- `config_sha256` covers the composed rows but not the installed package versions. Two campaigns with identical config on different robosuite builds would hash identically and be silently incomparable. Mitigation: fold `robosuite.__version__`, `mujoco.__version__`, `numpy.__version__` and `sys.version` into the episode header alongside config_sha256.

## SPEC
## 0. Measured facts this spec is built on (verified tonight, do not re-verify)

```
venv python           3.12.13 arm64   (system python3 is 3.14.6 with NO deps -> always use sys.executable)
mujoco 3.3.7 / robosuite 1.5.2 / numpy 1.26.4 / zstandard 0.25.0
mp start method       spawn           (unguarded rollout_many forkbombed to 14 procs)
throughput            40 eps / 5.2s with 10 workers = 457 eps/min, deterministic across repeats
single episode        0.78s env construction + 0.23s for 100 control steps
reset identity        sha256(env.sim.get_state().flatten().tobytes()) -- STABLE across fresh envs, same seed
obs dict              FLAT, 13 keys + 2 concatenated vectors (robot0_proprio-state (50,), object-state (10,))
tier authority        env._observables[name].modality in {'robot0_proprio','object'}   (PRIVATE attr)
sandbox-exec          present, blocks write+net, numpy imports fine   (feasible; deferred)
timeout(1)            NOT installed
```

---

## 1. Package layout (additions to the existing repo)

```
governor/
  seams/
    base.py        # Service, Context, Row, compose(), Harness      ~90 lines
    env.py  percept.py  policy.py  critic.py  recovery.py  store.py
  providers/
    env_robosuite.py            # wraps existing governor/env.py
    percept_full.py             # FullPercept, MaskedPercept
    policy_openloop.py          # wraps existing FrozenPolicy
    critic_rules.py             # RuleCriticRuntime
    recovery_scripted.py        # ScriptedRecovery, NoopRecovery
    store_jsonl.py              # JsonlStore
  loop/runner.py                # EpisodeRunner + invariants   (NOT a seam)
  campaign/{jobs.py,gates.py,run.py}
  bundles/base.yaml
tests/
  test_privilege_boundary.py  test_ablation_gate_cannot_pass_trivially.py
  test_invariant_violation.py  test_spawn_guard.py  test_tier_map_pinned.py
```

Existing `features.py` / `search.py` are reused: `features.REGISTRY` becomes the seed of `FullPercept.schema()`, `search.py` becomes `propose()`.

---

## 2. `seams/base.py` — the entire mechanism

```python
from __future__ import annotations
import abc, contextlib, hashlib, importlib, json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

class SeamError(RuntimeError): ...
class SeamUnavailable(SeamError): ...
class SeamMisuse(SeamError): ...

class Service(abc.ABC):
    slot: str
    def dispose(self) -> None: return None

def canonical_json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)

def sha256_of(v: Any) -> str:
    return hashlib.sha256(canonical_json(v).encode()).hexdigest()

@dataclass
class Context:
    store:    "EpisodeStore | None"     = None
    env:      "Environment | None"      = None
    percept:  "Percept | None"          = None
    policy:   "BasePolicy | None"       = None
    critic:   "CriticRuntime | None"    = None
    recovery: "RecoveryExecutor | None" = None
    config_sha256: str = ""

    def provide(self, svc: Service) -> None:
        held = getattr(self, svc.slot)
        if held is not None:
            raise SeamMisuse(f"seam {svc.slot!r} already provided by {type(held).__name__}")
        setattr(self, svc.slot, svc)

    def require(self, slot: str) -> Any:
        got = getattr(self, slot, None)
        if got is None:
            raise SeamUnavailable(f"seam {slot!r} is not mounted")
        return got

@dataclass(frozen=True)
class Row:
    id: str
    factory: str                                  # "governor.providers.percept_full:setup"
    config: Mapping[str, Any] = field(default_factory=dict)

def compose(base: Sequence[Mapping], overlay: Sequence[Mapping] = ()) -> list[Row]:
    """Whole-field replace, never deep merge. Unknown id in overlay = hard error."""
    rows = [dict(r) for r in base]
    index = {r["id"]: r for r in rows}
    for patch in overlay:
        rid = patch.get("id")
        target = index.get(rid)
        if target is None:
            raise SeamMisuse(f"overlay targets unknown row id {rid!r}")
        for k, v in patch.items():
            if k != "id":
                target[k] = v
        return [Row(**r) for r in rows]
    return [Row(**r) for r in rows]

class Harness:
    def __init__(self, rows: Sequence[Row]) -> None:
        self.rows = list(rows)
        self.ctx = Context(config_sha256=sha256_of(
            [{"id": r.id, "factory": r.factory, "config": dict(r.config)} for r in self.rows]))
        self._stack = contextlib.ExitStack()

    def boot(self) -> Context:
        try:
            for row in self.rows:
                json.loads(canonical_json(row.config))      # process-boundary purity
                mod, _, attr = row.factory.partition(":")
                if not attr:
                    raise SeamMisuse(f"factory {row.factory!r} must be 'module:callable'")
                getattr(importlib.import_module(mod), attr)(self.ctx, row.config)
            for slot in ("store", "env", "percept", "policy", "critic", "recovery"):
                svc = self.ctx.require(slot)
                self._stack.callback(svc.dispose)
        except Exception:
            self.close(); raise
        return self.ctx

    def close(self) -> None: self._stack.close()
    def __enter__(self): return self.boot()
    def __exit__(self, *a): self.close()
```

That is the whole mechanism. Registration = `provide`. Resolution = `require`. Reversal = `close()` on a fresh Harness per arm. Composition = `compose`.

---

## 3. `seams/percept.py` — THE privilege boundary (the fix)

```python
import enum
from dataclasses import dataclass
from typing import Mapping
import numpy as np

class Tier(enum.IntEnum):
    PROPRIO    = 0   # present in obs, measurable on a real Panda
    EXTERO     = 1   # ground-truth-derived, reachable ONLY via estimates, degraded by estimate_noise_sd;
                     # models onboard sensing. NOT a real camera - see report wording requirement.
    PRIVILEGED = 2   # exact ground truth

@dataclass(frozen=True)
class PerceptSpec:
    name: str; tier: Tier; kind: str          # "scalar" | "estimate"
    source: str

@dataclass(frozen=True)
class PerceptFrame:
    step: int
    values:    Mapping[str, float]            # critic reads these
    estimates: Mapping[str, np.ndarray]       # recovery reads these
    schema_sha256: str
    max_tier_present: Tier

class MissingPercept(KeyError): ...

class RecordingValues(Mapping):
    """Handed to a compiled critic. Masked names are ABSENT, never 0.0."""
    def __init__(self, f: PerceptFrame): self._f = f; self.read: list[str] = []
    def __getitem__(self, k):
        try: v = self._f.values[k]
        except KeyError: raise MissingPercept(k) from None
        self.read.append(k); return v
    def __iter__(self): return iter(self._f.values)
    def __len__(self):  return len(self._f.values)

class Percept(Service):
    """The ONE channel through which critic AND recovery see the world.
    Neither may take RawObservation. This is the design constraint from
    docs/headline-finding.md: the privilege budget must cover recovery too."""
    slot = "percept"

    @abc.abstractmethod
    def schema(self) -> tuple[PerceptSpec, ...]: ...

    @abc.abstractmethod
    def read(self, obs, step: int) -> PerceptFrame: ...

    def schema_sha256(self) -> str:
        return sha256_of([[s.name, int(s.tier), s.kind, s.source]
                          for s in sorted(self.schema(), key=lambda s: s.name)])
```

**Providers.**

```python
class FullPercept(Percept):
    """Tiers DERIVED from robosuite, never hand-declared.
    boot asserts every emitted name maps to a known modality."""
    MODALITY_TIER = {"robot0_proprio": Tier.PROPRIO, "object": Tier.PRIVILEGED}
    # scalars from governor.features.REGISTRY; estimates: {"cube_pose": Tier.PRIVILEGED}

class MaskedPercept(Percept):
    """The ablation mechanism. DROPS over-tier names from BOTH maps and degrades
    surviving estimates. estimate_noise_sd > 0 demotes an estimate to EXTERO."""
    def __init__(self, inner, max_tier: Tier, estimate_noise_sd: float, seed: int): ...
```

`MaskedPercept` is the only reason the seam exists, and it is called by the ablation gate on every run.

---

## 4. Recovery — signature change that closes the hole

```python
class RecoveryHandle(abc.ABC):
    preempts: bool
    @abc.abstractmethod
    def next_action(self, frame: PerceptFrame, base_action) -> "np.ndarray | None":
        """PerceptFrame, NOT RawObservation. A recovery that needs the cube pose
        must read frame.estimates['cube_pose'] and will raise MissingPercept when
        the ablation arm masks it. That failure is the point."""

class RecoveryExecutor(Service):
    slot = "recovery"
    @abc.abstractmethod
    def skills(self) -> Mapping[str, RecoverySpec]: ...
    @abc.abstractmethod
    def begin(self, verdict, frame: PerceptFrame) -> RecoveryHandle: ...
```

`ScriptedRecovery` = open -> re-read `frame.estimates["cube_pose"]` -> re-approach -> re-close -> re-lift. `NoopRecovery` = detection-only arm.

Enforced by `tests/test_privilege_boundary.py`: an `ast` walk asserting no module under `providers/recovery_*` or `providers/critic_*` subscripts a name bound from `RawObservation`, plus a runtime test that `ScriptedRecovery` under `MaskedPercept(max_tier=PROPRIO)` raises `MissingPercept` rather than silently succeeding.

---

## 5. Critic, store, gates (condensed)

`CriticEval{verdict, error, latency_us, features_read}` — `error` is a field; `compile()` raises only for misuse; `enforcement` returns `"interpreted"`. Store is JSONL with `seq == len`, `mono_ns` + `wall_ns`, header carrying `config_sha256`, `schema_sha256`, `PolicyIdentity`, `bundle_sha256`, **plus `robosuite/mujoco/numpy/python` versions**.

```python
@dataclass(frozen=True)
class GateDecision:
    kind: str; passed: bool; conclusive: bool
    p_value: float | None; alpha: float | None
    counts: Mapping[str, int]; rationale: str
    def __post_init__(self):
        if self.passed and not self.conclusive:
            raise SeamMisuse("a gate may not pass on inconclusive evidence")
```

**`PrivilegeAblationGate` — the trivial-pass fix:**

```python
def decide(self, ev) -> GateDecision:
    materially_different = (
        ev.full_config_sha256 != ev.ablated_config_sha256
        and (set(ev.features_read) - set(ev.ablated_schema_names)
             or ev.ablated_estimate_noise_sd > ev.full_estimate_noise_sd)
    )
    if not materially_different:
        return GateDecision(kind="privilege_ablation", passed=False, conclusive=False,
            p_value=None, alpha=None, counts={},
            rationale="ablation arm is identity on what this candidate reads; "
                      "nothing was ablated, so nothing is certified")
    ...
```

**`PairedMcNemarGate`**: exact one-sided binomial on discordant pairs, `alpha=0.025`. Asserts per seed: identical `policy_rng`, equal `reset_identity()`, and `config_sha256` differing only in the bundle/percept row. Refuses reused parent evidence failing any of these.

---

## 6. Preregistered seeds + once-only test ledger

```python
SEEDS = {"dev": range(0, 240), "val": range(1000, 1240), "test": range(2000, 2240)}
# hashed into the campaign manifest before generation 1; never re-drawn.
# dev  -> search/proposer ONLY, never gated
# val  -> gated every generation
# test -> read EXACTLY ONCE, enforced by runs/<campaign>/test_ledger.json {"reads": N}
```

`read_test_split()` increments and refuses at `reads >= 1`. The count is printed in the final report.

Sizing at the measured 457 eps/min: 240 seeds × 2 arms × 5 percept configs = 2400 eps ≈ **5.3 min**. Three generations ≈ 16 min. The overnight budget is not close to binding.

---

## 7. Tonight's observable result

`governor campaign --generations 3` prints and writes:

```
privilege ablation curve   (n=240 val seeds, paired same-seed, exact one-sided McNemar)
 max_tier    est_noise_sd   baseline   governed   delta    fixed  broke   p
 PRIVILEGED  0.000            ~50%       ~100%    +50pt      ~120     0   <1e-9   [PRIVILEGED - not transferable]
 EXTERO      0.010            ~50%        ~90%    +40pt      ~104     8   <1e-9
 EXTERO      0.020            ~50%        ~63%    +13pt       ~44    12   ~1e-5   [HONEST ARM]
 EXTERO      0.030            ~50%        ~53%     +3pt       ~20    12    n.s.
 PROPRIO     n/a            recovery raises MissingPercept: no observable cube-pose estimator exists
```

Promotion requires the **EXTERO @ 0.020** row to clear `alpha=0.025` on val, then one read of test. The PRIVILEGED row is reported and explicitly refused for promotion.

**Acceptance #4** — `tests/test_invariant_violation.py`: the runner logs `frame_sha256` of the exact `PerceptFrame` handed to `evaluate()`; an invariant re-derives it from the logged `obs/frame` event and compares hashes. The test mounts a `LyingPercept` that returns a value it does not log; the invariant must raise `InvariantViolation`. This test fails loudly on purpose and is the proof the reconstruction invariant is real.

---

## 8. Non-negotiable platform rules encoded as tests

```python
# test_spawn_guard.py
def test_run_jobs_refuses_to_run_in_a_child():
    # run_jobs() asserts current_process().name == "MainProcess" and raises otherwise,
    # converting the fork bomb observed tonight into a stack trace.

# test_tier_map_pinned.py
EXPECTED = {"cube_pos":"object", "gripper_to_cube_pos":"object", "cube_quat":"object",
            "robot0_gripper_qpos":"robot0_proprio", "robot0_eef_pos":"robot0_proprio", ...}
def test_robosuite_modality_map_unchanged():
    # pins env._observables[name].modality for robosuite 1.5.2; fails closed on unknown keys.
```

Workers are spawned with `mp.get_context("spawn")` and `sys.executable`; `OMP_NUM_THREADS=1` is set per worker so 10 workers do not oversubscribe 18 cores.
