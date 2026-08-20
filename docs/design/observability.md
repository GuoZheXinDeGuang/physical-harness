======================================================================
# VERSION 1
======================================================================

## QUESTION
How should Governor design an Observation Feature Contract that mechanically enforces the separation between real-robot-measurable and simulator-only state, budgets a promoted critic's privilege dependency, and turns "will this transfer?" into a measured number?

## DECISION
Adopt a four-layer mechanism replacing Zetta's prose rule (Zetta-Embodiment/README.md:201 "must not become hidden task control", which nothing enforces):

L1 STATIC - A tiered feature namespace whose tier is a property of the SOURCE BINDING, not of the name a producer happens to type. Four roots, one per tier: `proprio.`/`harness.` (t0), `onboard.` (t1), `estimated.` (t2), `oracle.` (t3). Each FeatureSource is constructed bound to exactly one root and one tier; emitting a key outside its root raises at frame-assembly time. Tier-2 sources must declare `derived_from` and the registry validates that closure is entirely tier<=1 at mount time, so "just call it estimated" is a mount error, not a runtime surprise. The `oracle.` source is a separate capability-seam Provider mounted only by sim configs; on a real-robot config it is absent and every `oracle.*` read raises, so real deployment fails loudly instead of silently.

L2 RUNTIME - Critics never receive the raw frame. They receive a `FeatureView` that records every `__getitem__` BEFORE deciding to mask or perturb it. The recorded access set is the ground truth for the declaration, checked per episode (set-diff) and per step in strict mode by a prepended invariant listener - a direct port of dsh's `packages/core/agent-loop/src/invariant.ts` "model-visible IFF logged" check, with the deliberate divergence that it compares a hash of the accessed-name set rather than a JSON string, because the dsh approach is unaffordable at control frequency. This is the layer that beats Zetta structurally: Zetta's critics are a single-feature DSL whose dependencies can be read off statically; Governor runs sandboxed code, where AST analysis is unsound (`obs["oracle." + part]`) and only a runtime access recorder is sound.

L3 GATE - A `PrivilegePolicy` preregistered inside the protocol and frozen into `manifest.runtime['privilege_policy']` (exactly Zetta's `EvolutionProtocol.runtime_policy()` pattern, protocol.py:19-154) with `critic_max_tier`, a cumulative per-BUNDLE `budget`, and a separate, much stricter `action_max_tier` default of 1. Three checkpoints: G1 static admission at PROPOSE (zero simulator cost, sits beside `_candidate_feature_contract` at lifecycle.py:1428); G2 the runtime invariant; G3 `store.promote` refuses without a `TransferAblationLedger` for the exact candidate_sha256, in report mode as well as enforce mode - so the number always exists even when it cannot reject.

L4 MEASUREMENT - A transfer-ablation gate that re-runs the promoted candidate on the same preregistered held-out seeds with the same `policy_rng` and the same physical reset, under an ablation ladder applied at the FeatureView boundary (never inside the simulator): `full`, `mask_t3`, a noise sweep in units of each feature's declared real-sensor sigma, `substitute` (registered real-observable surrogates), optional `latency_L`. Headline numbers: `transfer_score = G_mask_t3 / G_full` ("this critic retains X% of its gain over parent with zero privileged features") and `sigma_half_ratio` (the noise magnitude at which half the gain is gone, expressed in multiples of the real sensor's own sigma; >=1 means it tolerates realistic sensor noise). Significance on the ablated arm reuses Zetta's `one_sided_exact_mcnemar` (gating.py:12-25) unchanged. A free offline preflight runs the same ladder through ablated shadow replay over frozen `states.jsonl` before any simulator time is spent.

The sharpest single rule in the design: `action_max_tier=1` by default even when `critic_max_tier=3`. Privilege in a critic's TRIGGER is a sensing problem and is potentially surrogate-able; privilege in a recovery action's ARGUMENTS (Zetta's `RecoveryStep.parameters`, models.py:553-560, e.g. a waypoint computed from true object pose) is unimplementable on any hardware at any price. Those two must not share a budget.

## RATIONALE
Zetta already carries the shape of the right idea and stops one step short of it in three specific places, and each shortfall maps to one layer above.

1. Zetta's classification lives in the value stream, not in a registry. `robots/robocasa/privileged_state.py:213-214` emits `"privileged.source": "live_mujoco_simulator"` and `"privileged.class": "simulator_ground_truth"` as ordinary payload fields. A self-reported string inside the data it is classifying is forgeable by whoever writes the payload, and the `privileged.` prefix is likewise just a name the producer chose. Governor deletes both fields and moves classification into the registry, where it is bound to the source object and content-hashed into the manifest. This is the same move Zetta itself makes elsewhere and did not make here: `materialize_cluster_targets` (lifecycle.py:2475-2529) deliberately overrides the agent's own `dominant_cluster_id` and records `ranking_authority = "harness_unique_failure_episode_count"`. We extend that precedent - the privilege declaration's authority is `harness_registry_closure`, the agent supplies only justification and realization plan, and a mismatch between agent-declared and harness-computed features is a hard reject (mirroring the `deterministic_source_sha256` binding that keeps the multimodal cluster review honest).

2. Zetta already has the right enforcement point and uses it for the wrong question. `_candidate_feature_contract` (lifecycle.py:1428-1527) walks every replay trajectory and rejects a candidate whose rule features are not co-present in every state row from first evaluability onward. That is a real, mechanical, pre-simulator feature check - it just asks "is this feature dense enough to replay?" rather than "is this feature obtainable on a robot?". G1 is written to run in the same place with the same disposition semantics, so the mechanism is additive rather than parallel.

3. Zetta's shadow replay proves the template for measuring a counterfactual cheaply and refusing to overclaim. `shadow_replay.py:39-187` replays the delta rule read-only over frozen parent trajectories, computes recall against `earliest_divergence_step` and a false-positive rate against successful controls, fails CLOSED to "inconclusive, require online evidence" on a missing feature rather than inferring detector failure, and ships an explicit limitations string saying it validates detection only and cannot establish recovery causality. The ablation ladder is the same instrument pointed at a different question, and it carries the same kind of honest limitation string in the report itself: ablation measures dependence on privileged CHANNELS, not the sim-to-real gap as a whole. It is a lower bound - a critic that fails ablation will certainly fail on hardware; a critic that passes may still fail for dynamics, visual, actuation, or calibration reasons this instrument cannot see.

Why a budget rather than a ban. Privileged state is legitimately load-bearing for Stage-1 diagnosis and as the training label for a surrogate. Banning it outright makes gen-0 campaigns find nothing and throws away Zetta's actual working signal. Making the cost visible, cumulative, and preregistered is strictly more useful than a boolean, and it ranks two candidates against each other, which a boolean cannot.

Why the budget is per-BUNDLE and cumulative rather than per-delta. Zetta freezes parent rules and appends one rule per generation. A per-delta cap therefore permits ten generations of individually-cheap rules to compose into a fully oracle-dependent bundle with every single gate passing. The cost function is evaluated over the transitive primitive closure of the whole bundle.

Why record-before-mask in FeatureView. This mirrors the reasoning behind dsh's `wakingAfterAbort`, which is computed BEFORE the inbox insertion so a reentrant callback cannot reclassify the message (agent.ts:113-119). If access were recorded after the mask decision, a critic could hide its dependence by reading an oracle feature only when it is unmasked, and the ablation arm would measure a critic that is not the one being promoted.

Determinism under ablation. Noise is seeded per frame from `H(manifest_sha | candidate_sha | rung_id | seed | frame_seq)`, and ablation is applied at the FeatureView boundary rather than inside the simulator. That keeps physics bit-identical up to the first step where the critic's action differs, which preserves the reset binding Zetta's paired gates already assert via `state_sha256` equality in `_same_physical_reset` (gating.py:110-138). Ablation arms are paired-at-reset, not paired-along-trajectory - the same honest limitation the same-seed gate already has.

Cost. On the measured machine (progress.md: 212 episodes/min at 10 workers) a full ladder is 20 held-out seeds x 8 rungs = 160 candidate episodes, roughly 45 seconds, with parent arms adopted from the frozen ledger via Zetta's `_adopt_frozen_parent_evidence` (gate_runner.py:319-389). The measurement is cheap enough that there is no defensible reason to make it optional, which is precisely why G3 can refuse promotion without it even in report mode.

## REJECTED
- Prefix naming convention plus a lint rule (effectively what Zetta ships today). Rejected because the producer chooses the name: robots/robocasa/privileged_state.py could emit the identical residual as `task.rack.residual` and every lint passes. It also has no notion of a derivation closure, so a tier-0-looking feature computed from an oracle input is invisible, and it cannot constrain sandboxed critic code at all.
- Passing two separate arguments to the critic, `critic(obs, privileged)`. This is the honest minimal version and L1 is its hardened descendant, but on its own it fails three ways: a code critic can capture and stash the privileged reference across steps; there is no per-step verification of what was actually read; and it yields a boolean rather than a cost, so it cannot rank candidates or feed a budget.
- Static analysis of critic source to extract feature names. Unsound the moment critics are real sandboxed code rather than Zetta's single-feature DSL, because names can be computed (`obs["oracle." + part]`). Retained only as a cheap G1 prefilter over literal string constants; the runtime access recorder is the sound backstop.
- Banning privileged features outright (critic_max_tier=1 as a hard product rule). Rejected as the default: privileged state is the correct input for Stage-1 causal diagnosis and is the supervision label a surrogate needs. Kept as a first-class preregisterable lane (`critic_max_tier=1`), which makes an entire campaign real-robot-plausible end to end.
- A single boolean `uses_privileged` on the bundle. Not measurable, not rankable, does not compose across generations, and answers none of the question that matters, which is how much of the gain is real.
- Domain randomization or a robustness ensemble instead of channel ablation. Much more expensive, and it measures a different quantity: general robustness rather than dependence on a specific unobtainable channel. Ablation isolates exactly the channel the contract is about.
- Measuring the sim-to-real gap on real hardware. Not available (GOAL.md hard constraints: Mac arm64, no GPU, no robot). Stated explicitly in the report as the reason the ablation number is a proxy and a lower bound rather than a transfer measurement.
- Reusing Zetta's `infra_invalid` disposition for an undeclared feature read. Rejected because infra_invalid is retried up to `max_infrastructure_attempts` and never becomes learning signal. An undeclared read is a candidate defect, not infrastructure noise, so it gets its own terminal disposition `contract_violation` that fails the candidate without retry.
- Making the ablation gate a fourth entry in the existing gate-kind union alongside same_seed/regression/heldout. Rejected in favor of an explicit new ABLATION_GATE phase, because Zetta's ALLOWED_TRANSITIONS (store.py:24-56) is a whitelist and silently reusing a gate kind would let an ablation decision satisfy a held-out requirement in `store.promote`'s required-kind set.

## RISKS
- PDI is degenerate for single-oracle-feature critics. Masking the one feature a rule reads makes the rule inert, so `transfer_score` is 0 by construction and the metric collapses to a binary restatement of the tier. The informative rungs are `substitute` and the noise sweep, and both require a registered surrogate, which is real per-feature engineering. Mitigation: report `substitute` availability per feature explicitly, flag `oracle_only_detector` at preflight, and treat the absence of any surrogate as itself a reportable negative finding rather than a missing measurement.
- The sigma_half number is only as honest as the declared `real_noise` model, which is a guess unless sourced. Mitigation: `NoiseModel.citation_kind` in {datasheet, paper, measured, guess}; `require_sourced_noise_models` disqualifies a guess-sourced feature from contributing to the headline sigma_half, and the report prints `unsourced` beside it rather than silently averaging it in.
- Tier assignment at the boundary between t1 and t2 is a judgement call (is a wrist-camera pose estimate an onboard sensor or an estimator?). Mitigation: tier is a property of the source, declared once, reviewed once, and content-hashed - it is not re-litigated per campaign, and a tier change invalidates the manifest hash so it cannot be quietly retuned mid-campaign.
- The access recorder adds a dict-lookup plus a set-add per feature read per control step. At the measured 96 control steps/s single-process this is negligible, but strict per-step invariant checking is not. Mitigation: follow dsh's registrable diagnostics-companion pattern - strict mode in CI and sim, per-episode set-diff on normal runs, hash comparison rather than value comparison.
- A sandboxed code critic could inflate its declared closure by reading everything, or stash values in module state across steps to launder a privileged read. Reading everything fails closed (it raises the declared cost and will bust the budget), which is the desired direction. Cross-step laundering is mitigated by a fresh interpreter namespace per episode and by computing the closure over the whole episode rather than per step, but a determined adversarial proposer inside a single episode is not fully closed off by this design.
- Ablation arms are paired at reset only. Once the critic's action differs, the trajectories diverge and the pairing is no longer physical along the trajectory. This is the same limitation Zetta's same-seed gate carries and must be stated in the report rather than papered over.
- `mode=report` is a live escape hatch: if it becomes the habitual default, the mechanism degrades back toward the prose rule it replaces. Mitigation is partial by construction - mode is preregistered and covered by the manifest hash, and G3 refuses promotion without an ablation ledger in report mode too, so the number always exists even when it cannot reject. The residual risk is that nobody reads it.
- Adding an ABLATION_GATE phase widens the campaign state machine and its rejection edges, and every new edge is a new way for a crash-resumed campaign to land somewhere unexpected. Mitigation: whitelist the edges explicitly and add a state-machine test that enumerates unreachable and dead-end phases.

## SPEC
## 1. Namespace

Tier is determined by the ROOT, and the root is bound to the source object at construction. There is exactly one root per tier so the tier is legible in every rule the proposer writes.

```
harness.*     t0  step_index, sim_time, wall_time, controller mode, critic internal state
proprio.*     t0  joint qpos/qvel, commanded torque, gripper width, action history
onboard.*     t1  wrist F/T, tactile, wrist/scene RGBD raw-derived scalars, joint current
estimated.*   t2  estimator outputs whose inputs are ENTIRELY tier<=1 (pose detector, EKF)
oracle.*      t3  simulator internals: true object pose, residual_to_success, contact
                  ground truth, task success flag, task stage label, anything about a
                  body the robot cannot see
```

Tier semantics are pinned by one question: what does it cost to obtain this on the target platform?
t0 = zero (encoders + our own clock). t1 = a sensor the platform already has, plus calibration and noise. t2 = an estimator that must exist and be validated; error is a distribution, not a scalar. t3 = unobtainable at any price with current hardware.

```python
TIER_BY_ROOT = {"harness": 0, "proprio": 0, "onboard": 1, "estimated": 2, "oracle": 3}

@dataclass(frozen=True, slots=True)
class NoiseModel:
    kind: Literal["gaussian", "quantize", "dropout", "latency"]
    sigma: float                 # in `unit`; for dropout, probability
    unit: str
    citation: str
    citation_kind: Literal["datasheet", "paper", "measured", "guess"]

@dataclass(frozen=True, slots=True)
class Realization:
    kind: Literal["none", "onboard_sensor", "estimator", "added_hardware"]
    note: str
    cost_note: str = ""
    # kind == "none" is legal ONLY for tier 3

@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str                          # full dotted name; root determines tier
    dtype: Literal["float", "bool", "int", "str", "vector"]
    unit: str | None
    realization: Realization
    real_noise: NoiseModel | None      # REQUIRED for tier <= 2
    derived_from: tuple[str, ...] = () # edges into the derivation DAG
    surrogate_of: str | None = None    # this feature stands in for that one
    estimator_id: str | None = None    # REQUIRED iff tier == 2
    source_id: str = ""

    @property
    def tier(self) -> int:
        return TIER_BY_ROOT[self.name.split(".", 1)[0]]

class FeatureSource(ABC):
    root: str        # exactly one, e.g. "oracle"
    source_id: str
    @abstractmethod
    def schema(self) -> tuple[FeatureSpec, ...]: ...
    @abstractmethod
    def sample(self, ctx: EpisodeContext) -> Mapping[str, Any]: ...
```

`FeatureRegistry.mount(source)` validates, at mount time and never later:
- every `spec.name` starts with `source.root + "."`; every sampled key is in `schema()`
- no duplicate name across sources
- tier 2 => `estimator_id` set AND every name in the transitive `derived_from` closure has tier <= 1  (this is the rule that kills "just call it estimated")
- tier <= 2 => `real_noise is not None` and `realization.kind != "none"`
- `surrogate_of` target exists and has strictly higher tier than the surrogate
`registry.schema_sha256 = canonical_sha256(sorted(asdict(spec) for spec in all specs))`, written into the episode manifest and therefore covered by `manifest_sha256`.

Deliberate divergence from Zetta: delete `"privileged.source"` and `"privileged.class"` from the emitted payload (robots/robocasa/privileged_state.py:213-214). A self-declared classification inside the value stream is forgeable by the producer; classification lives in the registry.

Capability-seam wiring (dsh Definition/Provider/Consumer): `FeatureSource` is the Definition. `MujocoOracleSource` is a Provider mounted only by sim config rows. A real-robot config row simply does not mount it, so `oracle.*` resolves to nothing and every read raises. Absence of the provider is what makes real deployment fail loudly.

## 2. The view the critic actually sees

```python
class FeatureView:
    """Handed to every critic and recovery-argument evaluator. Never the raw frame."""
    def __init__(self, frame, registry, policy, mask, recorder): ...

    def __getitem__(self, name: str) -> Any:
        spec = self._registry.get(name)
        if spec is None:
            raise UnknownFeatureError(name)          # fail-closed on typos
        self._recorder.record(name)                  # BEFORE any mask decision
        if spec.tier > self._policy.critic_max_tier:
            raise PrivilegeDeniedError(name, spec.tier)
        if name in self._mask.masked:
            raise FeatureUnavailableError(name)      # identical to a missing real sensor
        return self._mask.perturb(name, self._frame[name], self._frame.frame_seq)
```

Record-before-mask is load-bearing: an attempted read of a masked oracle feature still counts against the declared closure, so a critic cannot hide dependence by reading it only when it is present.

Failure semantics on `FeatureUnavailableError`: FAIL-INERT. The rule is skipped for that step, a `critic/feature_unavailable` event is appended, the episode is NOT invalidated. That is exactly what a robot with a dead sensor does. Separately, a rule inert for more than `sparse_feature_threshold` of its evaluable steps in a NON-ablation run is a candidate defect `sparse_feature` (this generalizes Zetta's co-occurrence check in `_candidate_feature_contract`).

## 3. Declaration schema

```python
@dataclass(frozen=True, slots=True)
class RealizationClaim:
    feature: str
    kind: Literal["onboard_sensor", "estimator", "added_hardware", "unrealizable"]
    detail: str
    estimated_error: str
    evidence: str

@dataclass(frozen=True, slots=True)
class SurrogateClaim:
    oracle_feature: str
    surrogate_feature: str | None      # None == "no surrogate exists", reported as such
    rationale: str

@dataclass(frozen=True, slots=True)
class PrivilegeDeclaration:
    schema_version: int
    bundle_sha256: str
    parent_bundle_sha256: str | None
    delta_rule_ids: tuple[str, ...]

    # --- harness-computed. authority == "harness_registry_closure" ---
    primitive_closure: tuple[str, ...]        # whole BUNDLE, transitively expanded
    tier_histogram: Mapping[int, int]
    max_tier: int
    action_max_tier: int                      # tier of any RecoveryStep.parameters input
    privilege_cost: float
    cost_weights_sha256: str
    registry_schema_sha256: str

    # --- agent-supplied. must not contradict the above ---
    justification: str
    realization_plan: tuple[RealizationClaim, ...]   # one per tier>=2 primitive
    surrogate_claims: tuple[SurrogateClaim, ...]     # one per tier-3 primitive
    agent_declared_features: tuple[str, ...]
    agent_declared_max_tier: int

    authority: str = "harness_registry_closure"
```

Cost function (weights preregistered and hashed):
```python
TIER_WEIGHT = {0: 0.0, 1: 1.0, 2: 3.0, 3: 10.0}
VALIDATED_SURROGATE_DISCOUNT = 0.4     # applies to a t3 feature whose surrogate has
                                       # passed a substitute rung at least once

privilege_cost = sum(
    TIER_WEIGHT[registry[f].tier] * (VALIDATED_SURROGATE_DISCOUNT
                                     if has_validated_surrogate(f) else 1.0)
    for f in primitive_closure
)
```

Integrity check (rejects under-declaration):
```python
harness_side = {f for f in primitive_closure if registry[f].tier >= 1}
if set(declaration.agent_declared_features) != harness_side:
    reject("privilege_underdeclaration", missing=sorted(harness_side - agent_side),
           extra=sorted(agent_side - harness_side))
```

Action-argument tiering. Every `RecoveryStep.parameters` value that is an expression over features contributes its features to `action_closure`; `action_max_tier = max(tier)` over that closure. This is tracked and capped separately from the critic closure.

Artifacts, written `overwrite=False` (Zetta immutability):
```
analysis/privilege/<candidate_sha256>/declaration.json
analysis/privilege/<candidate_sha256>/ablation_preflight.json
analysis/privilege/<candidate_sha256>/ablation_ledger.json
```

## 4. Policy (preregistered, frozen in the manifest hash)

```python
@dataclass(frozen=True, slots=True)
class PrivilegePolicy:
    mode: Literal["enforce", "report"] = "enforce"
    critic_max_tier: int = 3
    action_max_tier: int = 1              # the sharpest rule in the design
    budget: float = 10.0                  # per BUNDLE, cumulative, not per delta
    require_realization_plan: bool = True
    forbid_unrealizable: bool = False
    require_sourced_noise_models: bool = True
    ablation_required: bool = True
    ablation_rungs: tuple[str, ...] = ("full", "mask_t3", "noise_sweep", "substitute")
    noise_sweep_k: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)
    min_transfer_score: float | None = None      # None == report-only
    min_sigma_half_ratio: float | None = None

    def runtime_policy(self) -> dict:  # flattened into manifest.runtime['privilege_policy']
        ...
```
`__post_init__` validates ranges, `action_max_tier <= critic_max_tier`, and that `"full"` and `"mask_t3"` are always present in `ablation_rungs`.

## 5. Gate rules

G1 - static admission at PROPOSE, zero simulator cost. Runs immediately after `_candidate_feature_contract` and before shadow replay, sharing its disposition semantics.
```
reject "unknown_feature"            any declared name absent from the registry
reject "privilege_tier_exceeded"    max_tier > policy.critic_max_tier
reject "action_privilege_exceeded"  action_max_tier > policy.action_max_tier
reject "privilege_budget_exceeded"  bundle privilege_cost > policy.budget
reject "realization_plan_missing"   a tier>=2 primitive with no RealizationClaim
reject "unrealizable_forbidden"     a claim kind == "unrealizable" and forbid_unrealizable
reject "privilege_underdeclaration" agent/harness feature-set mismatch
reject "undeclared_literal"         AST prefilter: a literal string constant in the critic
                                    source that resolves in the registry but is not in the
                                    declaration  (cheap prefilter only; L2 is the sound check)
```
A G1 rejection counts against `max_candidate_rounds_per_cluster` exactly like any other candidate rejection and routes through `_advance_after_candidate_rejection`.

G2 - runtime invariant `observation-privilege`, registered as an opt-in diagnostics companion, listener PREPENDED so a replay or mock cannot short-circuit it (dsh invariant.ts pattern).
```
per step (strict mode only):  accessed_this_step ⊆ declaration.primitive_closure
per episode (always):         accessed_this_episode ⊆ declaration.primitive_closure
per step (always):            append H(sorted(accessed_names)) to the episode log; replay
                              must reproduce it   ← "critic-visible IFF logged", hashed
                              rather than JSON-compared (deliberate divergence from dsh,
                              which stringifies the whole message array per request)
violation => episode disposition "contract_violation": terminal, NOT retried, fails the
             candidate.  Explicitly distinct from Zetta's retryable "infra_invalid".
```

G3 - promotion. Extend `CampaignStore.promote`'s required-evidence set:
```python
required_gate_kinds = {"same_seed"} | ({} if skip_regression else {"regression"}) | ...
# plus, unconditionally, in BOTH policy modes:
if ablation_ledger_for(candidate_sha256) is None:
    raise RuntimeError("promotion requires a transfer ablation ledger")
if policy.mode == "enforce":
    if policy.min_transfer_score is not None and ledger.transfer_score < policy.min_transfer_score:
        reject("transfer_score_below_threshold")
    if policy.min_sigma_half_ratio is not None and ledger.sigma_half_ratio_sourced_only < ...:
        reject("noise_tolerance_below_threshold")
```
Report mode cannot reject, but it cannot skip the measurement either. That is the property that makes this a mechanism rather than a policy in prose.

New campaign phase (the transition table is a whitelist, so the edges must be explicit):
```
HELDOUT_GATE -> ABLATION_GATE -> PROMOTE
ABLATION_GATE -> PROPOSE          (enforce-mode rejection, refine same cluster)
ABLATION_GATE -> DIAGNOSE         (enforce-mode rejection, switch to rank-1 cluster)
ABLATION_GATE -> COMPLETE         (budget exhausted)
```

## 6. Ablation procedure

Preflight (free, offline, read-only). Re-run the delta rule over the frozen parent `states.jsonl` with each rung's mask/noise applied to the STORED feature stream, reusing the shadow-replay machinery unchanged. Produces per-rung detection recall against `earliest_divergence_step` and false-positive rate against successful controls.
```
detection_transfer = recall(mask_t3) / recall(full)      # None if recall(full) == 0
flag "oracle_only_detector" when recall(mask_t3) == 0 and no surrogate is registered
```
In enforce mode with `min_transfer_score > 0`, this flag rejects at G1 before any simulator time is spent.

Online ladder. Each rung is a mask/perturbation applied at the FeatureView boundary, never inside the simulator, so physics is bit-identical to the `full` arm up to the first step where the critic's action differs.
```
full         reuses the already-executed held-out candidate arms. Zero extra episodes.
mask_t3      every tier-3 primitive in the closure raises FeatureUnavailableError
mask_t2      (optional) also tier 2
noise_k      for each k in policy.noise_sweep_k: every tier>=2 primitive perturbed with its
             registry real_noise scaled by k. float: += N(0, k*sigma). bool: flipped with
             p = min(0.5, k*p_dropout). vector: per-component.
substitute   each tier-3 primitive with a registered surrogate is replaced by that
             surrogate's live value (surrogate source is tier<=2 and must be mounted);
             primitives with no surrogate remain masked. Per-feature availability reported.
latency_L    (optional) tier>=1 features served from the frame L steps ago
```
Determinism:
```python
seed_bytes = sha256(f"{manifest_sha256}|{candidate_sha256}|{rung_id}|{seed}|{frame_seq}".encode()).digest()
rng = numpy.random.default_rng(int.from_bytes(seed_bytes[:8], "big"))
```
Per-frame seeding means the noise stream is reproducible even though the trajectory diverges.

Arms and pairing. Every rung runs the same preregistered held-out seeds with the same `policy_rng`, and asserts reset `state_sha256` equality against the recorded `full` arm before stepping. The parent arm is adopted from the frozen ledger rather than re-run. Episodes = |rungs| x |seeds| for the candidate only; 20 x 8 = 160, about 45 s at the measured 212 episodes/min.

Metrics.
```python
G_full  = S_full - S_parent                     # paired success-count difference
G_rung  = S_rung - S_parent

transfer_score(rung) = G_rung / G_full          # None when G_full <= 0; clamp to [0,1] for
                                                # display, keep the raw value in the ledger
PDI(rung)            = 1 - transfer_score(rung)

# significance of the ABLATED arm against parent, reusing Zetta's test unchanged:
p_rung = one_sided_exact_mcnemar(candidate_wins_rung, parent_wins_rung)

# noise tolerance, by linear interpolation between adjacent sweep rungs:
sigma_half       = smallest k with G_noise(k) <= G_full / 2
sigma_half_ratio = sigma_half                   # already in units of the real sensor's sigma
                                                # >= 1  tolerates realistic sensor noise
                                                # <  1  depends on precision the real sensor
                                                #       does not have
```
Headline for GOAL.md acceptance item 3: `transfer_score(mask_t3)` rendered as "retains X% of its gain over the frozen baseline with zero privileged features", alongside `sigma_half_ratio` when every contributing noise model is sourced (guess-sourced models are printed with an `unsourced` marker and excluded from the headline).

Ledger:
```python
@dataclass(frozen=True, slots=True)
class TransferAblationLedger:
    schema_version: int
    candidate_sha256: str
    parent_bundle_sha256: str
    declaration_sha256: str
    registry_schema_sha256: str
    policy_sha256: str
    paired_seed_sha256: str                 # binds the exact held-out seed list
    rungs: tuple[AblationRungResult, ...]   # per rung: successes, safety events, wins,
                                            # p_value, G, transfer_score, per-feature
                                            # substitute availability, episode ids
    transfer_score: float | None            # == rungs["mask_t3"].transfer_score
    sigma_half_ratio: float | None
    sigma_half_ratio_sourced_only: float | None
    limitations: str
    ledger_sha256: str
```

`limitations` is emitted in the report itself, not only in docs:
> Ablation measures dependence on privileged observation channels only. It does not measure the dynamics gap, the visual gap, the actuation gap, or calibration drift, and it is paired at reset rather than along the trajectory. Read it as a lower bound: a critic that fails ablation will fail on hardware; a critic that passes may still fail for reasons this instrument cannot see.

======================================================================
# VERSION 2
======================================================================

## QUESTION
How should Governor design an Observation Feature Contract that mechanically enforces the separation between real-robot-measurable and simulator-only state, budgets a promoted critic's privilege dependency, and turns "will this transfer?" into a measured number?

## DECISION
The original design does not survive. Four of its load-bearing claims are empirically false or vacuous on this machine, and I verified each by running code in the repo. The core insight — measure privilege dependence by ablation instead of asserting it in prose — is right and is kept. Everything built on top of it is cut by roughly 80%.

**What I proved false tonight (not argued, ran):**

**F1. The capability seam fails SILENTLY, in the permissive direction.** The design's central safety claim is that `oracle.*` is a Provider "mounted only by sim configs; on a real-robot config it is absent and every `oracle.*` read raises, so real deployment fails loudly." On macOS arm64 `multiprocessing.get_start_method()` is **spawn**, and `governor/parallel.py` already runs 10 workers. I unmounted every privileged feature in the parent and asked a spawned worker what it saw:
```
PARENT after unmount: []
WORKER sees          : ['privileged.cube_z', 'privileged.grasp_error']
```
The worker re-imports `governor.features`, module-level `register()` calls re-run, and the oracle source is resurrected in every worker regardless of what the config mounted. A registry whose contents are *runtime-mounted state* cannot cross a spawn boundary. Relatedly, 4 of the 6 existing `Feature` objects fail `pickle.dumps` outright (`extract` is a lambda), so they cannot be shipped to a worker either. Any mount-time-validated dynamic registry is unimplementable as specified on this platform.

**F2. The threat model is aimed at the wrong hole.** The design spends four layers policing the *tier of declared feature names*. The privilege leak that actually happened in this repo — the one that inflated the headline from +13.3% to +50% (`docs/headline-finding.md`) — was `target = obs["cube_pos"]` inside the **recovery**. That is a raw-observation read that never enters the feature namespace at all. I confirmed the raw robosuite obs dict handed around by `governor/env.py` contains `['cube_pos', 'cube_quat', 'gripper_to_cube_pos', 'object-state']`. Tiering names does nothing about this. The fix is containment, not taxonomy: candidate code must never receive the raw obs dict.

**F3. The headline number is statistically vacuous at the specified sample size.** `transfer_score = G_mask_t3 / G_full` on 20 held-out seeds, simulated 20k times against this repo's own measured rates (parent 50%, full 100%, masked 63.3%, true score 0.266):
```
n= 20  median=0.273  95% CI=[-0.500, 0.700]   width 1.20
n= 60  median=0.267  95% CI=[-0.111, 0.531]   width 0.64
n=200  median=0.266  95% CI=[ 0.081, 0.422]   width 0.34
```
A CI of width 1.20 on a quantity defined to live in [0,1] — and this is the *most favorable* case, a 50pp G_full. The design proposes to gate promotion on this via `min_transfer_score`, and specifies no interval anywhere. The design's own cost argument refutes its sample size: I measured 1.05 s/episode single-process (57 eps/min, consistent with the recorded 212 eps/min at 10 workers), so n=200 across 7 rungs is ~1400 episodes, roughly 7 minutes. There is no reason to run 20.

**F4. The metric is degenerate in the *passing* direction on the candidate this harness actually produces.** The design's risk list anticipates degeneracy toward 0. The real degeneracy is toward 1. `docs/search-beats-hand.md` records that the best trigger found is `observable.gripper_effort` at score 1.105, *beating* the best privileged trigger at 1.095 — privilege does not help detection on this task. A zero-privilege critic reads no tier-3 feature, so `mask_t3` changes nothing and `transfer_score = 1.0` by construction. The harness would print "retains 100% of its gain with zero privileged features" as GOAL.md acceptance-item-3 evidence, having measured nothing. That is the self-deception the review asked for, and it is in the headline metric.

**F5. It is a patch to a codebase Governor does not have.** The spec instructs edits at `lifecycle.py:1428`, `store.py:24-56`, `gate_runner.py:319-389`, and extends `CampaignStore.promote` and `ALLOWED_TRANSITIONS`. Those live in `/Users/yusenthebot/Desktop/Zetta-Embodiment` — a separate reference tree totalling 7,640 lines across the six files named. Governor is 540 lines with no campaign store, no promote, no manifest, no gate phases, no proposer, no sandbox, and no event log. Adding an `ABLATION_GATE` phase to a state machine that does not exist is not buildable tonight.

**The revised decision: CONTAINMENT + CURVE. Two mechanisms, ~250 lines, runnable tonight.**

**M1 CONTAINMENT (replaces L1+L2).** The enforcement is not a tier lattice; it is that raw `obs` never reaches candidate code. `governed_rollout` holds the raw dict, and critic and recovery receive only a `FeatureView`. Keep the existing two-class `Privilege` enum and the existing prefix-equals-declaration check in `features.py` — with containment in place, two classes are sufficient and the t1/t2 boundary is unresolvable anyway with no robot to calibrate against. The registry stays **static and module-level** so it survives spawn; "no oracle" is expressed as a per-episode frozen `allowed: frozenset[str]` carried inside the picklable `EpisodeSpec`, not as a mutated global. This is the direct fix for F1 and F2. Record-before-mask is kept verbatim from the original — it is correct and it is three lines.

**M2 CURVE (replaces L3+L4).** The ablation ladder collapses to three rungs that actually discriminate, and the noise sweep is pointed at **recovery perception**, which is where `docs/headline-finding.md` shows the gain actually lives: `full`, `mask_privileged`, `noise_k` for k in (0.5, 1.0, 1.5, 2.0, 3.0). Paired same-seed against the frozen baseline, exact McNemar (12 lines, ported from Zetta, needs no scipy), n=200 held-out seeds, and **every ratio carries a bootstrap CI over the paired seed list**. `transfer_score` is reported as `None` unless G_full's own CI excludes zero — a ratio whose denominator is not significantly positive is not a number, it is a division accident.

Two rules from the original survive intact because they are correct and cheap: **`action_max_tier` stricter than `critic_max_tier`** (privilege in a trigger is a sensing problem, privilege in a recovery argument is unimplementable at any price — this is the design's sharpest idea and this repo has the scar to prove it), and **the ledger always exists even when it cannot reject**, so the number is never optional.

**Cut entirely:** four tiers, `derived_from` closure validation, `estimated.` root, dynamic `FeatureRegistry.mount`, `FeatureSource`/Provider ABC, `substitute` rung and `VALIDATED_SURROGATE_DISCOUNT` (no surrogate is registered and registering one is a research project per feature), `latency_L`, the `ABLATION_GATE` campaign phase, the 12-field `PrivilegePolicy`, `NoiseModel.citation_kind` and `sigma_half_ratio_sourced_only`, the AST prefilter, and the per-step hash chain (the accessed-name set is near-constant across steps, so it is one constant repeated ~100 times per episode — store it once per episode plus a delta list).

## RATIONALE
The design's diagnosis is excellent and its prescription is a different project's patch. Three reasons the revision looks like this.

**Ablation is the right instrument and this repo already proved it works by hand.** `docs/headline-finding.md` is exactly a transfer-ablation ledger produced manually: parent 50%, full-privilege 100%, and a recovery-perception noise sweep at sd = 0.000/0.010/0.020/0.030/0.040 giving +50.0/+40.0/+13.3/+3.3/+1.7 with exact McNemar p at each rung. The honest headline is already computable from it: transfer_score = 13.3/50.0 = 0.266. The single highest-value thing to build tonight is the automation of the table that already exists on disk, because that hits GOAL.md acceptance items 2, 3, and 5 in one run and can be checked against a known-correct answer. A design that cannot reproduce the repo's own headline number is not verifiable; this one is verifiable *against a fixed target*, which is much stronger than merely being runnable.

**The noise sweep must be aimed at recovery perception, not at critic features.** This is the substantive redirection. The measured table shows detection was never the bottleneck: the critic fired 34 times, caught all 30 failures, broke nothing. The entire gap between +50% and +13.3% came from how well the *recovery* could re-perceive the cube. `docs/search-beats-hand.md` independently confirms privilege is useless for detection on this task (zero-privilege 1.105 > privileged 1.095). So an ablation ladder that masks critic features measures the part that was already fine and misses the part that was rotten. Pointing the sweep at recovery perception makes the curve bite, and it is also what makes the number non-degenerate (F4): masking the critic's features changes nothing, degrading the recovery's percept changes everything.

**Static registry plus per-episode allowlist is the only shape that survives spawn.** Once F1 is proven, the architecture is forced. Mount-time validation of a dynamic registry is a fiction under spawn; the parent's mutations evaporate. But the enforcement does not need dynamism — it needs the *permission* to travel with the work unit, and `EpisodeSpec` is already a frozen dataclass of primitives that already crosses the boundary correctly. Putting `allowed: frozenset[str]` in it is a two-line change that makes the seam real, checkable in a worker, and covered by the existing determinism tests. The lambda-pickling failure (4 of 6 features) is a standing latent bug worth fixing on the way past regardless — promote the lambdas to module-level functions.

**On keeping `action_max_tier` separate.** The original's sharpest single claim is that critic-trigger privilege and recovery-argument privilege must not share a budget, because the first is potentially surrogate-able sensing and the second is unimplementable. This repo is the proof: the author's own round-1 conclusion was wrong for exactly this reason, and `STATUS.md` records the lesson under "do not redo" as "不要只给 critic 算特权预算". Keeping the two closures separate costs one extra field and prevents a recurrence of the one mistake this project has already made twice.

**On why the ratio needs a CI and the tier lattice does not need to exist.** Complexity should go where the uncertainty is. The uncertainty in this system is overwhelmingly sampling noise on small paired binomial differences — quantified above at CI width 1.20 for n=20 — not ambiguity about whether a wrist-camera pose estimate is t1 or t2. The original spends its complexity budget on a four-tier ontology that cannot be validated without hardware, and spends nothing on the interval around the number it proposes to gate on. The revision inverts that allocation.

## REJECTED
- Keeping the four-tier namespace (t0-t3) with the `estimated.` root and `derived_from` closure validation. Rejected because there is no robot to calibrate the t1/t2 boundary against, so the distinction is unfalsifiable this week, and because the mount-time closure check is the specific piece that requires a dynamic registry, which F1 proves cannot survive spawn. The existing two-class enum in `governor/features.py` already carries all the discriminative power the ablation actually uses. Revisit when real hardware or a real estimator exists.
- Keeping the dynamic `FeatureRegistry.mount(source)` + `FeatureSource`/Provider ABC and simply forcing workers to re-mount. Rejected: this is achievable (pass a mount manifest in the spec and re-run mounts in the worker) but it reintroduces exactly the failure mode it is meant to prevent — a worker that forgets to re-mount silently gets the *permissive* default. The frozen `allowed` set in `EpisodeSpec` fails closed instead, and is 2 lines rather than ~150.
- Keeping the `substitute` rung and `VALIDATED_SURROGATE_DISCOUNT`. Rejected for tonight: zero surrogates are registered, and the original's own risk #1 concedes the rung is where the information is *only if* a surrogate exists. Building the rung before any surrogate exists produces a column of `unavailable`. The honest version is the one the original also proposes — report surrogate absence as a finding — which needs no rung at all, just a field.
- Keeping the `ABLATION_GATE` campaign phase and the `store.promote` required-evidence extension. Rejected because `CampaignStore`, `promote`, `ALLOWED_TRANSITIONS` and the phase machine are Zetta's, in a 7,640-line reference tree Governor does not import. Governor has no campaign loop yet at all. The equivalent enforcement tonight is a single function `require_ledger(candidate_sha256)` that the promotion path calls; it becomes a phase edge later, once there is a state machine to add an edge to.
- Keeping the per-step accessed-name hash chain in the episode log ('critic-visible IFF logged'). Rejected in its per-step form: the accessed set is near-constant across a ~100-step episode, so the chain is one hash repeated. Kept as per-episode accessed-set equality plus a per-step *delta* list, which carries the same information, still catches a critic that reads an undeclared feature on one step only, and is what the replay assertion actually needs. This preserves the dsh invariant's substance at ~1% of the log volume.
- Dropping the ablation gate entirely and reporting only the full-privilege gain. This is what Zetta does and what the original design correctly attacks. Rejected: `docs/headline-finding.md` shows it yields a headline that is off by a factor of 3.8 (+50.0% vs +13.3%) and flips the significance verdict from p<1e-6 to p=0.057. The whole project exists to not do this.
- Masking critic features as the primary ablation rung, per the original. Demoted rather than dropped: kept as `mask_privileged` because it is nearly free (it reuses the full arm's episodes for the parent side) and because it will matter for future candidates that do use privileged triggers. But it is no longer the headline, because on the candidate the search currently produces it is 1.0 by construction (F4). The recovery-perception noise sweep is the headline.
- Requiring sourced `NoiseModel` citations before a sigma number may be reported. Rejected as a gate for tonight, kept as a printed annotation. With `require_sourced_noise_models=True` and no datasheet work done, every noise model is `guess`, so the flag would exclude 100% of the sweep from the headline — i.e. the default configuration silently deletes the only number that discriminates. Print `unsourced` beside it and let it into the headline; an unsourced curve that shows +50%→+13.3% is enormously more informative than no curve.

## RISKS
- The recovery-perception noise sweep is parameterized by a sd in the same units as the frozen policy's own `percept_noise`, which makes k=1.0 mean 'the recovery sees exactly as badly as the policy does'. That is a defensible and legible anchor, but it is NOT a real sensor sigma, and it must be labelled as a self-referential unit in the report or it will be misread as a hardware claim. This is the honest residue of cutting `NoiseModel.citation_kind`.
- n=200 makes transfer_score's CI width ~0.34, which is adequate to distinguish 0.27 from 1.0 but NOT adequate to enforce a threshold like `min_transfer_score=0.5` — the CI straddles it. Mitigation: report the CI and refuse to enforce a threshold in round 3; enforcement needs either more seeds or a bootstrap-lower-bound criterion, and that decision should be made after seeing the first real ledger.
- Containment (M1) is only as good as the audit that no code path hands raw `obs` to candidate code. Today `governor/env.py:rollout` passes `obs` straight to `FrozenPolicy.act`, which is correct (the policy is the black box under governance, not a candidate) — but the boundary is a convention, not a type. One careless signature reintroduces the round-1 bug. Mitigation: the deliberate-violation test (GOAL acceptance #4) targets exactly this, and `governed_rollout` should be the ONLY function that ever holds the raw dict.
- The frozen `allowed` set in `EpisodeSpec` is checked inside `FeatureView.__getitem__`, which runs in the worker. If a future refactor constructs the view from the module-level REGISTRY rather than from the spec, F1's silent-permissive failure returns unnoticed. Mitigation: a spawn-boundary regression test that asserts a worker with `allowed=frozenset()` raises on every privileged read — this is the test that the original design had no way to write.
- Reusing the already-executed held-out `full` arm as the ablation denominator means G_full is selected-on-max (the candidate was promoted partly because that arm looked good), so transfer_score is biased slightly upward in the denominator and therefore conservative. Acceptable direction, but it should be stated, and it argues for a fresh full arm once episodes are this cheap.
- Cutting the tier lattice means a future `estimated.*` feature (an EKF or a learned pose detector) has no home and no closure validation. This is a real capability deferred, not a problem solved. The cut is right for tonight because no such feature exists; it becomes wrong the moment someone adds one, and that is the trigger to revisit rather than a permanent judgement.
- The whole instrument still measures dependence on privileged CHANNELS, not the sim-to-real gap. It says nothing about the dynamics gap, contact model, actuation, or calibration. The original's `limitations` string is correct and is kept verbatim in the ledger; the risk is that a reader takes transfer_score=0.27 as 'will get 27% of the benefit on a robot', which it emphatically is not.
- Governed episodes run longer than the 100-step ungoverned schedule (recovery adds a descend/reopen/re-approach/re-close/re-lift sequence), so the measured 1.05 s/episode understates ablation cost by roughly 1.5-2x. 200 seeds x 7 rungs lands nearer 12-15 minutes than 7. Still trivially affordable overnight, but the 45-second figure in the original was optimistic by an order of magnitude once sample size is corrected.

## SPEC
## 0. Scope: 5 files, ~250 new lines, all inside `/Users/yusenthebot/Desktop/physical-harness/governor/`. No Zetta code is imported; the 12-line McNemar is copied.

## 1. `governor/features.py` — three surgical edits, no new concepts

**(a) Fix the latent spawn bug.** Promote all four lambdas to module-level `def`s so `Feature` is picklable. Verified failing today for `observable.eef_z`, `observable.gripper_effort`, `privileged.cube_z`, `privileged.grasp_error`.

**(b) Add the recovery-perception noise unit** (the only surviving piece of `NoiseModel`):
```python
@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    privilege: Privilege
    extract: Callable[[Mapping[str, np.ndarray]], float]
    doc: str
    noise_sd: float = 0.0        # 1-sigma perturbation unit for the ablation sweep,
    noise_source: str = "guess"  # in the feature's own units. Printed with this tag.
```

**(c) Keep** the `Privilege` two-class enum, the prefix-is-the-declaration `__post_init__`, `privilege_cost` raising `KeyError` on unknown names, and the module-level `REGISTRY`. The registry stays **static** — never mutated after import. That is what makes it spawn-safe.

## 2. `governor/view.py` (new, ~70 lines) — the actual mechanism

```python
class ContractViolation(Exception): ...      # terminal, never retried
class FeatureUnavailable(Exception): ...     # fail-inert, like a dead sensor

@dataclass(frozen=True, slots=True)
class Mask:
    hidden: frozenset[str] = frozenset()
    noise_k: float = 0.0          # multiples of Feature.noise_sd
    rung_id: str = "full"

class FeatureView:
    """The ONLY object candidate code (critic + recovery) ever receives.
    The raw obs dict never crosses this boundary. This is the containment
    that the tier lattice was not: docs/headline-finding.md's real leak was
    `obs["cube_pos"]` inside recovery, a raw read that no namespace sees."""

    def __init__(self, obs, allowed: frozenset[str], mask: Mask, spec, step: int):
        self._obs, self._allowed, self._mask = obs, allowed, mask
        self._spec, self._step = spec, step
        self.accessed: set[str] = set()

    def __getitem__(self, name: str) -> float:
        feat = REGISTRY.get(name)
        if feat is None:
            raise ContractViolation(f"unknown feature {name!r}")
        self.accessed.add(name)                      # BEFORE any mask decision
        if name not in self._allowed:
            raise ContractViolation(f"{name!r} not in this episode's allowed set")
        if name in self._mask.hidden:
            raise FeatureUnavailable(name)
        v = feat.extract(self._obs)
        if self._mask.noise_k and feat.noise_sd:
            v += self._rng(name).normal(0.0, self._mask.noise_k * feat.noise_sd)
        return v

    def _rng(self, name: str) -> np.random.Generator:
        h = hashlib.sha256(
            f"{self._spec.seed}|{self._mask.rung_id}|{name}|{self._step}".encode()
        ).digest()
        return np.random.default_rng(int.from_bytes(h[:8], "big"))
```
Record-before-mask is kept verbatim from the original design and is load-bearing for the same reason. Per-frame seeding keeps the noise stream reproducible after trajectories diverge. Noise is applied at this boundary only, never inside MuJoCo, so physics is bit-identical to the `full` arm until the first differing action.

## 3. `governor/env.py` — carry the permission in the picklable spec

```python
@dataclass(frozen=True, slots=True)
class EpisodeSpec:
    ...
    allowed: frozenset[str] = frozenset(REGISTRY)   # travels WITH the work unit
```
`frozenset[str]` pickles cleanly and survives spawn; a mutated module global does not (proven: parent `[]`, worker `['privileged.cube_z','privileged.grasp_error']`). A zero-privilege campaign sets `allowed=frozenset(observable_names())`.

New function, the only one that ever holds raw `obs`:
```python
def governed_rollout(spec, trigger, recovery, mask=Mask()) -> dict:
    env, obs = make_env(spec), None
    obs = env.reset(); policy = FrozenPolicy(spec); policy.observe_once(obs)
    accessed, per_step, fired_at = set(), [], None
    for t in range(total_steps):
        view = FeatureView(obs, spec.allowed, mask, spec, t)
        if fired_at is None:
            try:
                if trigger.fires(view):      # trigger reads ONLY the view
                    fired_at = t
            except FeatureUnavailable:
                pass                          # FAIL-INERT: dead sensor, skip this step
        act = (recovery.act(view, t - fired_at) if fired_at is not None
               else policy.act(obs, phase_at(spec.schedule, t)))
        obs, *_ = env.step(act)
        per_step.append(sorted(view.accessed - accessed)); accessed |= view.accessed
    return {"seed": spec.seed, "success": bool(env._check_success()),
            "fired_at": fired_at, "accessed": sorted(accessed),
            "accessed_delta": per_step}
```
`recovery.act(view, ...)` is the fix for the round-1 bug: recovery re-perception now goes through the same masked, noised, recorded boundary as the critic. `accessed` + `accessed_delta` is the per-episode-set-plus-delta form of the dsh invariant, replacing the constant-valued per-step hash chain.

## 4. `governor/ablation.py` (new, ~110 lines)

```python
RUNGS = (("full",            Mask(rung_id="full")),
         ("mask_privileged", Mask(hidden=frozenset(privileged_names()), rung_id="mask_privileged")),
         *((f"noise_{k}", Mask(noise_k=k, rung_id=f"noise_{k}")) for k in (0.5,1.0,1.5,2.0,3.0)))

def one_sided_exact_mcnemar(candidate_wins: int, parent_wins: int) -> float:
    d = candidate_wins + parent_wins
    if d == 0: return 1.0
    return sum(math.comb(d, v) for v in range(candidate_wins, d + 1)) / 2**d

def run_ladder(seeds, trigger, recovery, base_spec, workers=10) -> "AblationLedger":
    parent = {r["seed"]: r["success"] for r in rollout_many(
        [base_spec.child(seed=s) for s in seeds], workers)}
    rows = []
    for rung_id, mask in RUNGS:
        res = govern_many([base_spec.child(seed=s) for s in seeds],
                          trigger, recovery, mask, workers)
        cw = sum(1 for r in res if r["success"] and not parent[r["seed"]])
        pw = sum(1 for r in res if not r["success"] and parent[r["seed"]])
        rows.append(RungResult(
            rung_id=rung_id,
            success_rate=mean(r["success"] for r in res),
            gain=mean(r["success"] for r in res) - mean(parent.values()),
            fixed=cw, broke=pw,
            p_value=one_sided_exact_mcnemar(cw, pw),
            accessed=sorted(set().union(*(r["accessed"] for r in res))),
            n_fired=sum(1 for r in res if r["fired_at"] is not None)))
    return AblationLedger.build(rows, parent, seeds, trigger, recovery)
```

**Bootstrap CI — the fix for F3.** Resample the paired seed list with replacement, 5000 draws, recompute every gain and ratio inside each draw:
```python
def paired_bootstrap(parent, arms, n_boot=5000, seed=0):
    """Returns {stat_name: (point, lo95, hi95)}. Resamples SEEDS, not episodes,
    so the pairing is preserved inside each draw."""
```
Gating rule, replacing `min_transfer_score` as specified:
```python
transfer_score = None if G_full_ci_lo <= 0 else G_mask.point / G_full.point
# A ratio whose denominator's CI includes zero is not a measurement.
```

Ledger, written `overwrite=False` to `analysis/ablation/<candidate_sha256>.json`:
```python
@dataclass(frozen=True, slots=True)
class AblationLedger:
    schema_version: int
    candidate_sha256: str        # sha256 over (trigger, recovery, base_spec, allowed)
    registry_sha256: str         # over sorted feature names + privilege + noise_sd
    paired_seed_sha256: str
    n_seeds: int
    parent_success_rate: float
    rungs: tuple[RungResult, ...]
    critic_closure: tuple[str, ...]      # features the TRIGGER touched
    action_closure: tuple[str, ...]      # features the RECOVERY touched  <- separate
    critic_privilege_cost: int
    action_privilege_cost: int           # capped separately and much tighter
    transfer_score: float | None
    transfer_score_ci: tuple[float, float] | None
    sigma_half: float | None             # k where gain <= G_full/2, linear interp
    noise_unit_note: str = ("k is in multiples of each feature's declared noise_sd, "
                            "which is a GUESS anchored to the frozen policy's own "
                            "percept_noise, not a hardware sigma.")
    limitations: str = ("Measures dependence on privileged observation CHANNELS only. "
                        "Not the dynamics, visual, actuation, or calibration gap. "
                        "Paired at reset, not along the trajectory. Lower bound: a "
                        "critic that fails ablation will fail on hardware; one that "
                        "passes may still fail for reasons this cannot see.")
```

## 5. Enforcement, without a state machine

```python
# governor/promote.py
def require_ledger(candidate_sha256: str, policy) -> AblationLedger:
    led = load_ledger(candidate_sha256)
    if led is None:
        raise ContractViolation("promotion requires a transfer ablation ledger")
    if led.action_privilege_cost > policy.action_max_privilege:   # default 0
        raise ContractViolation(f"recovery reads privileged: {led.action_closure}")
    if led.critic_privilege_cost > policy.critic_max_privilege:   # default 1
        raise ContractViolation(f"critic over budget: {led.critic_closure}")
    return led    # report-mode cannot reject on transfer_score, but the ledger exists
```
`PrivilegePolicy` is 5 fields, not 12: `mode`, `critic_max_privilege=1`, `action_max_privilege=0`, `n_heldout_seeds=200`, `noise_k=(0.5,1.0,1.5,2.0,3.0)`. **`action_max_privilege=0` while `critic_max_privilege=1` is the surviving sharp rule** — a privileged trigger is a sensing problem, a privileged recovery argument is unbuildable.

## 6. Tests — GOAL.md acceptance #4 and #5

`tests/test_contract.py`:
1. **Spawn-boundary regression (the test the original design could not write).** Submit `EpisodeSpec(allowed=frozenset(observable_names()))` through `rollout_many` with a trigger that reads `privileged.cube_z`; assert `ContractViolation` propagates out of the worker. Today's registry-mount approach silently passes this — that is the bug.
2. **Deliberate violation is caught.** A critic reading an undeclared name raises `ContractViolation`, terminal, not retried.
3. **Picklability.** `pickle.dumps(f)` for every `f in REGISTRY.values()` — currently fails 4/6.
4. **Record-before-mask.** A masked read still appears in `view.accessed`.
5. **Noise determinism.** Same `(seed, rung_id, name, step)` yields an identical perturbation across processes.

## 7. Tonight's run and its falsifiable target

```
governor ablate --seeds 3000-3199 --trigger auto --recovery reopen_retry
```
200 held-out seeds x 7 rungs, ~1400 governed episodes plus a 200-episode parent arm, ~12-15 min at the measured 212 eps/min (governed episodes run ~1.5-2x the 1.05 s I measured, because recovery extends the schedule).

**This is verifiable because it has a known-correct answer.** `docs/headline-finding.md` recorded by hand: parent 50.0%; recovery percept sd 0.000 → 100.0% (30 fixed, 0 broke, p<1e-6); sd 0.020 → 63.3% (11 fixed, 3 broke, p=0.057). The `noise_1.0` rung must land near +13.3% and the `full` rung near +50%, giving `transfer_score ≈ 0.27, CI ≈ [0.08, 0.42]`. If the harness prints anything materially different, the harness is wrong — not the docs. That is a real acceptance criterion rather than a self-graded one, and it is the property the original design lacked: it could not be checked against a number that already existed.

======================================================================
# VERSION 3
======================================================================

## QUESTION
Design the simulation task suite and the frozen base policy for the Governor harness: simulator stack, robot model, 3-5 tasks, exact observation/action spaces, base policy design, seed/perturbation protocol, success criteria, and expected per-task baseline success rates with difficulty-calibration rationale.

## DECISION
STACK: robosuite 1.5.2 + mujoco 3.3.7 (pinned), Panda arm, BASIC composite controller (OSC_POSE), `use_camera_obs=False`, headless, CPU-only. Measured 400-470 sim steps/s per process; ~1240 episodes/min at 10 spawn workers. Rollout budget is not the bottleneck.

SUITE — 3 calibrated tasks (all N=60, terminal-state success, dev seeds 0-59):
- T1 `lift` (robosuite Lift), perception sd=0.009 -> **50.0%**
- T2 `stack` (robosuite Stack), sd=0.008 -> **38.3%**
- T3 `pickcan` (robosuite PickPlaceCan), sd=0.018 -> **41.7%**
- T4 `pickmilk` (PickPlaceMilk) designated cross-task TRANSFER task, uncalibrated by design — calibrated only at promotion time so it cannot leak into development.

EXCLUDED after measurement: NutAssemblySquare (0% even at sd=0 — needs yaw control the policy family lacks; unrecoverable failure, worthless for evolution), Door/Wipe (different contact modality).

FROZEN POLICY: one noisy perception read at t=0 (p̂ = p_true + N(0, sd²I₃)), then a fixed open-loop phase schedule, closed-loop in EEF space only: `a[:3] = clip(8.0*(goal - eef_pos), -1, 1)`, `a[3:6] = 0`, `a[6] = grip`. Verified **100% success at sd=0** on lift and pickcan, 86.7% on stack — so every failure is attributable to perception error and is recoverable by re-perception.

HELD-OUT AXIS: object SIZE is the primary axis (drives both difficulty, 7%->50%, and a real memorization trap: success finger_gap tracks object width 0.0284->0.0562 while failure gap is pinned at 0.0010 regardless of size). Secondary axes: object pose range, initial arm config, distractors, target pose, mass (capped at 0.5 kg).

FRICTION IS REJECTED AS AN AXIS — measured inert across a 40x range (0.05->2.0 gives flat 0.475).

SUCCESS CRITERION: terminal-state `_check_success()`, never latched-over-episode.

## RATIONALE
Every number below is measured on this machine today, not carried over from documentation.

**Why this stack.** It is the only viable one: LIBERO/RoboCasa/GR00T need flash-attn (linux_x86_64 only) + CUDA + EGL. mujoco must stay pinned at 3.3.7 because >=3.4 renames `MjData.qM`->`M` and robosuite's OSC controller calls `mj_fullM(..., data.qM)`. Keeping robosuite rather than a hand-written MuJoCo scene preserves the shared-substrate transfer argument to RoboCasa.

**Why the frozen policy fails like a real VLA.** It reproduces the four named failure modes structurally, not by injected bugs: (a) compounding error — it commits to one perception estimate at t=0 and never revisits it; (b) no contact awareness — it never reads `gripper_qpos`, so it cannot know the hand is empty; (c) no retry — the phase schedule is fixed-length with no branch; (d) brittle under shift. Critically, the sd=0 check proves the controller is *sound*: at kp=8 it succeeds 100% with perfect perception, while kp=4 succeeds 0% (never arrives in the phase budget). The kp=4 regime is exactly the unrecoverable "controller can't do it" failure that has no evolutionary value, and it is excluded by construction.

**Why 38-50% and not something else — this is a power question, not an aesthetic one.** I simulated the held-out gate (exact one-sided McNemar, alpha=0.025, 5% break rate):

| baseline | n=200, rescue 10% | rescue 20% | rescue 30% |
|---|---|---|---|
| 42% | 0.38 | **0.96** | 1.00 |
| 70% | 0.00 | 0.16 | 0.52 |
| 90% | 0.00 | 0.00 | 0.00 |

A 90% baseline yields power **0.00** even for a 30% rescue rate — statistically dead, "nothing to evolve" made quantitative. A 70% baseline is underpowered (0.16). At ~42% with 200 held-out seeds there are ~116 rescuable failures and the gate reaches power 0.96 at a 20% rescue rate. Separately, the minimum evidence that can ever pass is a clean sweep of 6 discordant pairs (p=0.0156<0.025); 5-0 gives 0.03125 and fails. Both bound the design from above and below.

**Why failures will cluster (the harness is pointless otherwise).** Measured on lift: 38/60 terminal failures split into at least two mechanically distinct classes — `miss_grasp` (finger_gap 0.0010-0.0030, object never leaves the table; 33/60) and `slip_after_lift` (object lifted then dropped; 5/60, found by comparing latched vs terminal success). Place tasks add a third, `misplace` (grasped, carried, released off-target). That is a natural hard partition on (failure_class, phase, task), mirroring Zetta's hard-key bucketing before any similarity is computed.

**Why terminal success, and why this is load-bearing.** Latching `ok=True` on any mid-episode success contaminates the label: 5/60 episodes lift the cube then drop it. Under latched labelling the success-gap range becomes 0.0010..0.0606, overlapping the failure range and destroying separability. Under terminal labelling the split is clean and non-overlapping: failure 0.0010..0.0030 vs success 0.0377..0.0576. The critic's detection signal only exists if the label is terminal. This is a genuine discovery from running it, not a stylistic preference.

**Why the held-out axis is real and not memorization-proof theatre (the LIBERO-Pro lesson).** The success `finger_gap` tracks object width almost exactly (width 0.024->gap 0.0284; width 0.062->gap 0.0562), while the failure gap is invariant at 0.0010. So a threshold tuned mid-range on dev (tau≈0.019-0.030, a perfectly reasonable choice given dev's 0.001-vs-0.040 separation) false-positives on every small-object success in held-out, while a principled threshold near the failure mode (tau≈0.008) transfers with zero loss. The benchmark therefore discriminates memorized thresholds from mechanism-grounded ones — which is exactly what a held-out axis must do, and what a pure pose-randomization axis would NOT do.

**Why I reject friction — a fake axis would silently corrupt the whole claim.** Sweeping the cube's friction 0.05->2.0 left success flat at 0.475. Diagnosis: the gripper pad geoms carry friction 2.0 with `priority=0` on both sides, so MuJoCo's equal-priority contact rule takes the element-wise **max** and the object's friction is entirely masked. Setting both cube and pad still gave a flat 0.450 at mu=0.02, because the pinch force vastly exceeds the 0.7 N weight of a 0.07 kg cube. Friction only becomes live jointly with mass, and there it is a cliff, not a gradient: mass 2.0 kg with mu<=0.3 collapses to **0.000** — an unrecoverable failure. Declaring "robust to friction" from an axis that never varied effective friction is precisely the class of silent-fake result this harness exists to prevent.

**Why perturbations must be applied post-reset.** robosuite's `hard_reset` rebuilds the model on every `reset()`: a mass write of 0.5 came back as 0.0826, and a friction write reverted to 1.0. Lift also re-randomizes cube *size* per episode, which is why the mass changed at all. So `init_qpos` set pre-reset is silently ignored (verified: identical joints). Native `initialization_noise` is the correct seeded hook for arm config.

## REJECTED
- NutAssemblySquare / NutAssemblyRound as a task: measured 0.000 success at sd=0 (perfect perception). The policy family controls position only (a[3:6]=0) and peg insertion needs yaw alignment. A task the frozen policy can never solve produces unrecoverable failures with no rescue path — the exact 'kp=5 can't reach' pathology the project already excluded.
- Friction as a perturbation axis: measured flat at 0.475 across mu in [0.05, 2.0] on the object, and flat at 0.450 across [0.02, 2.0] when gripper pads are included. Masked by MuJoCo's max-combination rule (pads at 2.0, priority 0) and by grip force >> object weight. Including it would manufacture a fake robustness result.
- High mass (>=1.0 kg) as a difficulty knob: 2.0 kg with mu<=0.3 gives 0.000 success — a cliff to total slip, unrecoverable and unclusterable. Mass is retained only in the mild band [0.05, 0.5] kg where it moves 0.475->0.450.
- Object-pose range as the PRIMARY held-out axis: mechanically live (xy spread 0.056 -> 0.197 at half=0.12) but nearly inert on success (0.475 -> 0.450), because the policy is pose-conditioned and closed-loop in EEF space. Kept as a secondary generalization axis; it cannot carry the memorization test that object size can.
- Initial arm configuration as a primary axis: native `initialization_noise` works and is seeded (joint sd scales linearly 0.0113 -> 0.2255), but success only moves 0.475 -> 0.400 at magnitude 0.4, for the same closed-loop reason. Secondary axis only.
- Latched ('ever succeeded') success labelling: contaminates the critic's target with 5/60 lifted-then-dropped episodes and destroys the finger_gap separability that the entire zero-privilege critic depends on.
- Setting model perturbations before reset(): silently discarded by robosuite's hard_reset model rebuild. Verified — mass 0.5 came back 0.0826, friction 0.3 came back 1.0, init_qpos delta 0.35 produced bit-identical joints.
- fork-based multiprocessing: BrokenProcessPool with MuJoCo on macOS arm64. Must use spawn, which forces top-level picklable worker functions and re-import of robosuite per worker (~0.05s construct, amortized).
- Raw hand-written MuJoCo scenes instead of robosuite: more control but forfeits the shared-substrate transfer argument to RoboCasa, and would require re-deriving placement sampling, success criteria, and the proprio/object observation split that already maps cleanly onto the privilege boundary.
- A learned BC policy as the frozen baseline for this round: more realistic compounding error, but it makes the failure distribution non-stationary and unattributable while the harness itself is unproven. Deferred to a frontier round — the scripted policy's sd=0 = 100% property is what makes failures attributable NOW.

## RISKS
- Held-out shift can accidentally make the task EASIER, invalidating attribution. Measured: my first held-out config (size 0.016-0.030, wider pose, init noise, mass) gave 58.3% vs dev 45.0% because the size range was centered above dev's 0.021. MITIGATION: the held-out perturbation distribution must be difficulty-matched empirically — require |p_heldout - p_dev| <= 0.08 on the frozen policy, verified and content-hashed BEFORE any candidate is proposed, and frozen thereafter.
- Only ~2 failure clusters exist on lift (miss_grasp 33/60, slip_after_lift 5/60). A campaign configured for maximum_target_clusters=2 could exhaust its cluster budget in one generation. Stack and pickcan add a `misplace` class, which is part of why the suite is 3 tasks rather than 1.
- The dominant failure mode is single-mechanism (one bad perception commitment). A critic that detects it generalizes across all three tasks, which is good for transfer but means the suite may not discriminate between *different* critic designs — most reasonable critics will find the same finger_gap signal. The object-size memorization trap is the main thing separating good from lucky.
- finger_gap is nearly a perfect detector on dev (12.5x separation, zero overlap). Detection is NOT the bottleneck — the prior round already measured that de-privileging the RECOVERY's perception collapses +50pt to +13.3pt (n.s.). The suite must therefore be evaluated with the privilege budget covering recovery, or it will report a fake win.
- Small-object held-out cells can hit 7% success (width 0.024), which is too sparse to rescue and would dominate the held-out block with unrecoverable episodes. MITIGATION: clamp the held-out size range to [0.018, 0.028] half-extent and verify the cell-wise floor stays above ~25%.
- robosuite re-randomizes cube size per episode within [0.020, 0.022] independently of my perturbation. Post-reset geom_size writes must also correct the free-joint z or the object spawns intersecting the table; I do this explicitly, but any future code path that resizes without the z fix will silently produce penetration artifacts.
- Terminal-state success is stricter than robosuite's own instantaneous reward semantics. Anything reusing robosuite reward/`staged_rewards` for shaping or for cluster labels will disagree with the gate's label unless it also uses terminal state.
- spawn workers re-import robosuite and rebuild the controller config per episode. At 10 workers this is amortized (~1240 episodes/min measured), but a naive per-episode env construction inside a tight gating loop would be dominated by construct+reset rather than stepping.
- The policy has no orientation control (a[3:6]=0). This caps the suite's reachable task families and is the direct reason NutAssembly is excluded — a future frontier round wanting insertion tasks must extend the policy family, which changes the frozen baseline and invalidates cross-generation comparisons.

## SPEC
# Governor Simulation Task Suite + Frozen Base Policy — v1 spec

All figures measured on this machine (macOS arm64, mujoco 3.3.7, robosuite 1.5.2), 2026-08-19.

## 1. Environment provider (capability seam)

Adopts the dsh Service Definition / Provider / Consumer split (`packages/shell/shell/src/index.ts`,
`packages/fs/fs/src/index.ts`), as plain Python ABCs — NOT Cordis fibers/effects.

```python
class EnvProvider(abc.ABC):
    """Contract note: build() NEVER applies perturbations itself.
    Perturbations are applied by apply_perturbation() AFTER reset(), because
    robosuite hard_reset rebuilds the MjModel on every reset and silently
    discards any pre-reset model write. (Measured: mass 0.5 -> 0.0826.)"""
    @abc.abstractmethod
    def build(self, task: str, seed: int) -> Env: ...
    @abc.abstractmethod
    def apply_perturbation(self, env: Env, pert: "Perturbation") -> None: ...
```

Mandatory construction (regression-test this; a silent revert to global seeding
degrades the paired gate into a coin flip):

```python
suite.make(env_name=..., robots="Panda",
           controller_configs=load_composite_controller_config(controller="BASIC", robot="Panda"),
           has_renderer=False, has_offscreen_renderer=False, use_camera_obs=False,
           reward_shaping=False, control_freq=20, horizon=HZ,
           seed=N,                      # REQUIRED. np.random.seed() does not reach the env RNG.
           placement_initializer=...,   # object-pose axis
           initialization_noise={"magnitude": m, "type": "uniform"})  # arm-config axis
```
Do NOT set `MUJOCO_GL` on macOS. Parallelism MUST use `multiprocessing.set_start_method("spawn")`
— fork gives `BrokenProcessPool` with MuJoCo on arm64.

## 2. Task table (measured, N=60, terminal success, dev seeds 0-59)

| id | env | horizon | sd | baseline | sd=0 check | failures |
|---|---|---|---|---|---|---|
| `lift` | Lift | 100 | 0.009 | **50.0%** | 100% | 30 |
| `stack` | Stack | 220 | 0.008 | **38.3%** | 86.7% | 37 |
| `pickcan` | PickPlaceCan | 220 | 0.018 | **41.7%** | 100% | 35 |
| `pickmilk` | PickPlaceMilk | 220 | TBD | transfer-only | — | — |

Noise->success curve for `lift` (N=40 each, latched labelling, for shape):
0.004->0.900, 0.006->0.750, 0.008->0.600, 0.010->0.500, 0.012->0.475, 0.016->0.425, 0.020->0.250.

## 3. Observation space and the privilege boundary

Three declared tiers. A critic/recovery feature contract MUST name the tier of every feature it reads;
the gate reports zero-privilege AND full-privilege scores. This closes the gap Zetta left open
(it reads `privileged.*.residual_to_success` and forbids misuse only in prose).

- **T0 proprioceptive** (real-robot measurable; from robosuite `robot0_proprio-state`):
  `robot0_joint_pos`(7), `robot0_joint_vel`(7), `robot0_eef_pos`(3), `robot0_eef_quat`(4),
  `robot0_gripper_qpos`(2), `robot0_gripper_qvel`(2).
  Derived: `finger_gap = abs(gripper_qpos[0] - gripper_qpos[1])`.
- **T1 exteroceptive-noisy**: the SAME estimate the policy consumed, `p_hat`, plus its `sd`.
  Re-reads by a recovery skill draw fresh noise at the same `sd` — never ground truth.
- **T2 privileged (sim-only)**: `object-state` — `cube_pos`, `cube_quat`, `gripper_to_cube_pos`,
  contact/grasp flags. Usable only if explicitly declared and paid for in the privilege budget.

## 4. Action space

`action_dim = 7` (OSC_POSE 6 + gripper 1), delta control.
Policy writes `a[:3]` (position delta) and `a[6]` (gripper); `a[3:6] = 0` (no orientation control).

## 5. Frozen base policy

```python
KP, HOVER, GRASP_Z, LIFT_Z = 8.0, 0.10, 0.005, 0.25
PHASES_PICK  = [("above",25,HOVER,-1), ("descend",25,GRASP_Z,-1),
                ("close",12,GRASP_Z,+1), ("lift",38,LIFT_Z,+1)]
PHASES_PLACE = PHASES_PICK + [("carry",40,None,+1), ("lower",30,None,+1),
                              ("release",12,None,-1), ("retreat",18,LIFT_Z,-1)]
# NOTE: sum(PHASES_PLACE) == 200 steps; horizon MUST be >= 220 or the episode
# terminates mid-release and success is 0.000 even at sd=0. (This was a real bug.)

def act(obs, p_hat_src, p_hat_dst, phase, zoff, grip):
    eef  = obs["robot0_eef_pos"]
    goal = target_for(phase, p_hat_src, p_hat_dst, zoff)
    a = np.zeros(7)
    a[:3] = np.clip(KP * (goal - eef), -1, 1)   # closed loop in EEF, open loop in TARGET
    a[6]  = grip
    return a
```
Perception, once, at t=0 only:
`p_hat = p_true + default_rng(seed*7919 + 13).normal(0, sd, 3)`

Place-phase z offsets must match the grasp offset (`p_dst[2] + 0.005`, not `+0.02`) — the EEF holds
the object at `center + GRASP_Z`, so a mismatched offset drops the object from 1.5 cm onto a 5 cm target.

Frozen artifact: `policy_sha256 = sha256(source || KP || HOVER || GRASP_Z || LIFT_Z || phase_table || sd_by_task)`.
Recorded in the episode manifest; any change is a new generation, never an in-place edit.

## 6. Perturbation axes — measured verdicts

| axis | hook | measured effect | verdict |
|---|---|---|---|
| **object size** | post-reset `geom_size` + free-joint z fix | 7%->50% success; success-gap 0.0284->0.0562 | **PRIMARY held-out** |
| object pose | `placement_initializer` x/y range | spread 0.056->0.197; success 0.475->0.450 | secondary (generalization, not difficulty) |
| initial arm cfg | `initialization_noise` magnitude | joint sd 0.011->0.226; success 0.475->0.400 | secondary |
| distractors | `PickPlace(single_object_mode=1)` | 4 objects present, `obj_id` names target | secondary |
| target pose | `target_bin_placements` / `cubeB_pos` | — | secondary |
| mass | post-reset `body_mass`, clamp [0.05,0.5] | 0.475->0.450 mild; 2.0 kg -> **0.000** | mild only, hard cap |
| **friction** | — | flat 0.475 over mu 0.05..2.0 (masked by max-rule + pad mu=2.0) | **REJECTED** |

## 7. Seed and perturbation protocol

Adopts Zetta's preregistration + content-addressing (`zetta/evolution/protocol.py`,
`store.py:ALLOWED_TRANSITIONS`), diverging on `heldout_mode` default.

```python
DEV_SEEDS      = range(0, 600)         # rollout + same-seed + regression
HELDOUT_SEEDS  = range(10_000, 10_200) # 200, disjoint; assert set(dev) & set(heldout) == set()
```
- Perturbation is a **pure function of (split, seed)**: `rng = default_rng(PERT_SALT ^ (split_id<<32) ^ seed)`.
  Reproducible offline, no hidden state.
- Dev split: nominal distribution (robosuite defaults) — pose ±0.03, size [0.020,0.022], mass nominal, no init noise.
- Held-out split: size half-extent U[0.018,0.028], pose half 0.08, init noise 0.10, mass U[0.05,0.40], distractors on for `pickcan`.
- **Difficulty-match precondition:** measure frozen-policy success on the held-out distribution and require
  `|p_heldout - p_dev| <= 0.08` BEFORE registering the manifest. My first attempt failed this
  (58.3% vs 45.0%) and had to be re-centered — enforce it mechanically, not by inspection.
- `manifest_sha256` covers: policy_sha256, task table, sd per task, both seed tuples, perturbation
  distribution params, and the evolution policy dict. Frozen for the generation.
- Ordering per episode: `build(seed)` -> `reset()` -> `apply_perturbation()` -> `sim.forward()` -> step loop.

## 8. Success criteria

```python
success = bool(env._check_success())   # evaluated ONCE, at the terminal state
```
Never latch mid-episode success. Measured on `lift` N=60: latched 45.0% vs terminal 36.7%,
5 lifted-then-dropped. Terminal labelling yields non-overlapping signatures:

| | finger_gap |
|---|---|
| success (n=22) | 0.0377 .. 0.0576 |
| failure (n=38) | 0.0010 .. 0.0030 |

## 9. Failure classes (clustering hard key)

Hard partition on `(task, failure_class, phase)` — buckets never merge regardless of similarity,
mirroring Zetta `clustering.py` complete-link-within-bucket.

- `miss_grasp` — `finger_gap < 0.005` at end of `close`, object z unchanged. Dominant (33/60 on lift).
- `slip_after_lift` — object left table then returned. 5/60 on lift.
- `misplace` — grasped and carried, terminal placement check fails. Place tasks only.
- `horizon_incomplete` — fallback, emitted ONLY when nothing else fires (prevents a manufactured
  100%-prevalence cluster).

## 10. Difficulty calibration — power basis

Exact one-sided McNemar, alpha=0.025, 5% break rate, 4000 trials:

| baseline | n=200 rescue 10% | 20% | 30% |
|---|---|---|---|
| 42% | 0.38 | **0.96** | 1.00 |
| 70% | 0.00 | 0.16 | 0.52 |
| 90% | 0.00 | 0.00 | 0.00 |

Minimum passing evidence: 6 discordant pairs, clean sweep (p=0.0156); 5-0 fails at 0.03125.
Target band **35-55%** — below ~25% failures are dominated by unrecoverable modes; above ~70%
the gate is underpowered; at 90% it is statistically dead.

## 11. Memorization trap (the held-out teeth)

Success `finger_gap` tracks object width; failure gap does not:

| half-extent | width | success | gap(success) | gap(failure) |
|---|---|---|---|---|
| 0.012 | 0.024 | 0.07 | 0.0284 | 0.0010 |
| 0.016 | 0.032 | 0.20 | 0.0332 | 0.0010 |
| 0.021 | 0.042 | 0.37 | 0.0397 | 0.0010 |
| 0.026 | 0.052 | 0.47 | 0.0475 | 0.0010 |
| 0.031 | 0.062 | 0.50 | 0.0562 | 0.0010 |

A dev-tuned mid-range threshold (tau ~0.019-0.030) false-positives on small-object successes in held-out;
a mechanism-grounded threshold (tau ~0.008, sitting just above the invariant failure mode) transfers exactly.
Report `tau` and its margin to both modes on every promoted critic.

## 12. Throughput budget

400-470 sim steps/s/process; 280 lift episodes in 13.6 s at 10 spawn workers (~1240 episodes/min).
One generation = 600 dev rollouts + (same-seed ~35 pairs) + 600 regression + 400 held-out arms
≈ 2000 episodes ≈ **under 3 minutes** of wall clock for `lift`, ~2.2x that for the 220-horizon tasks.
Rollout budget is not the constraint; proposer latency is.

======================================================================
# VERSION 4 (post-critique, authoritative)
======================================================================

## QUESTION
Design the simulation task suite and the frozen base policy for the Governor harness: simulator stack, robot model, 3-5 tasks, exact observation/action spaces, base policy design, seed/perturbation protocol, success criteria, and expected per-task baseline success rates with difficulty-calibration rationale.

## DECISION
VERDICT: the stack, the policy design, the privilege boundary, the size axis, and the entire statistical basis SURVIVE — I re-ran them on this machine tonight and they reproduce. Two load-bearing claims BREAK, and they force a smaller suite.

WHAT I VERIFIED (re-measured, not accepted):
- Stack runs: mujoco 3.3.7 + robosuite 1.5.2 + numpy 1.26.4 on py3.12.13/arm64, headless, CPU-only. No CUDA/EGL path anywhere.
- Throughput: 468/447/408 steps/s (Lift/Stack/Can). 300 Lift episodes in 15.4s @10 spawn workers = **1167 eps/min** (design said ~1240; use the measured number).
- Lift sd=0 -> **1.000** terminal. Attribution property holds on Lift.
- Terminal-vs-latched is real on Lift (0.510 vs 0.450 at N=200). Keep terminal labelling.
- Post-reset `geom_size` write + free-joint z fix **works** (0.0201 -> requested); size is a live difficulty axis (0.100 -> 0.725) and the memorization trap is real (failure gap pinned at 0.0010 at every size while success gap tracks width).
- McNemar power table reproduces **exactly** (42%: 0.37/0.96/1.00; 70%: 0.01/0.14/0.52; 90%: 0.00/0.00/0.00) and the 6-discordant-clean-sweep minimum is exact.

WHAT BREAKS:

**(1) The 50.0% baseline is an N=60 artifact.** At sd=0.009, N=60 gives 0.500; **N=200 gives 0.600**. The sd was tuned against a point estimate with SE 6.5%. Recalibrated at N=200: sd=0.011->0.530, **sd=0.013->0.450**, sd=0.015->0.385. The claimed 12.5x gap separation is likewise N=60: at N=200 it is **6.4x** (gap_succ min 0.0147 vs gap_fail max 0.0023). Fix: sd=0.013, and no calibration constant may ever be set from N<200.

**(2) The detector is exactly dead on the place tasks at terminal state.** This is the serious one. Stack terminal finger_gap: success [0.0788,0.0788], failure [0.0788,0.0788] — *identical*, because the gripper is open after release. PickPlaceCan is *inverted* (failure gap larger). The whole zero-privilege detection story is Lift-and-terminal-only, and the design silently generalizes it to three tasks. Risk #3's "a critic that detects it generalizes across all three tasks" is measurably false. Constructive fix: the signal is **perfectly separated mid-episode** — Stack @ end-of-LIFT gives success [0.0389,0.0555] vs failure [0.0010,0.0017]. So decouple the success LABEL (terminal, correct) from the detection FEATURE (phase-anchored, must not be terminal), and gate every task on a measured detectability precondition before admitting it.

**(3) PickPlaceCan is not attributable: sd=0 gives 0.800, not 1.000.** One in five failures has nothing to do with perception, so a critic can be credited for "rescuing" episodes that never had a perception fault. REJECT pickcan (and pickmilk, same family, same geometry) until that 20% is diagnosed.

**(4) Stack adds no third failure class.** At sd=0.008 every Stack failure has end-of-lift gap ~0.001 — 100% miss_grasp. The `misplace` cluster that justified going to 3 tasks does not occur at the calibrated noise level.

REVISED SUITE — 2 tasks, not 4:
- **`lift` (Lift, sd=0.013, horizon 100)** — baseline **45.0% ±6.9 @N=200**, sd=0 = 100%. The only dev/search task.
- **`stack` (Stack, sd=0.008, horizon 220)** — baseline **42.5%**, sd=0 = 92.5%. **Transfer-only**, never searched on. Replaces pickmilk as the transfer task because unlike pickmilk it has a *verified* separable phase-anchored signal and a verified viable baseline.
- CUT: pickcan, pickmilk (attribution), NutAssembly (design already cut it, correctly).

ADMISSION GATE (new, mechanical): a task enters the suite only if (a) sd=0 success >= 0.90, (b) dev success in [0.35,0.55] at N>=200, (c) a zero-privilege feature separates success from failure at a *declared phase checkpoint* with no overlap. lift passes all three; stack passes with a 7.5% attribution floor that must be subtracted from any claimed rescue rate; pickcan fails (a).

ALSO CUT AS UNEARNED: all five secondary perturbation axes (pose, init arm, distractors, target pose, mass) — all measured near-inert (0.475->0.450/0.400) and none is needed for the gate. Keep size only. Cut ALLOWED_TRANSITIONS state machine down to a flat frozen JSON manifest.

SMALLEST THING THAT RUNS TONIGHT: lift only, 200 dev + 200 held-out + 200 regression = 600 episodes = **31 seconds measured**. The full 2-task version is under 3 minutes. Rollout budget is genuinely not the constraint — that part of the design was right.

## RATIONALE
I treated every number in the design as a hypothesis and re-ran it, because the design's own strongest argument is "measured on this machine today." Two of its measurements were taken at N=60, where the standard error is 6.5% — large enough that both the headline baseline (50.0%) and the headline separation (12.5x) moved materially at N=200 (to 60.0% and 6.4x). A design whose calibration constant is fit to a 60-sample point estimate will drift under the harness's own re-measurement, so the fix is not just a new sd but a rule: no calibration constant from N<200.

The place-task failure is the one that would have silently corrupted the project. The design's central empirical asset is round 1's finding that failure is detectable from proprioception alone. That was established on Lift at terminal state. Extending to Stack and PickPlace without re-measuring the *feature* looks safe because success rates land in band — and they do, I confirmed 42.5% and 40.0%. But the feature is dead: after `release` the gripper is open in both outcomes, so terminal finger_gap is literally identical (0.0788 both ways on Stack) and inverted on Can. A harness built this way would report "the critic generalizes across the suite" while the critic was actually keying on a Lift-specific terminal artifact and finding nothing on the other two. That is precisely the class of silent-fake result the design says it exists to prevent, reproduced inside the design itself.

The fix is constructive rather than fatal because the signal does exist — it is just phase-anchored. At end-of-LIFT on Stack the separation is clean (0.0389-0.0555 vs 0.0010-0.0017, no overlap). So the correct structure is an asymmetry the design collapses: the *label* should be terminal (the design is right, and I confirmed 6% lifted-then-dropped at N=200), while the *feature read* must be at a declared mid-episode checkpoint. Round 2's existing EOD scan already does the right thing; the suite spec was about to contradict it.

PickPlaceCan's sd=0 = 0.800 breaks the attribution argument that makes the whole harness interpretable. The design's own reasoning is that sd=0 -> 100% proves the controller sound so every failure is perception-caused and rescuable. At 80% that inference fails: 20% of failures are geometry/controller faults, and a critic that "rescues" them earns credit for noise. I could have tuned my place z-offsets to close the gap, but that is the point — a task whose attribution depends on hand-tuned offsets is not frozen-baseline material tonight. Deferring it costs nothing because Stack already supplies a second task with a verified signal.

Cutting from 4 tasks to 2 is the main complexity judgment. The design's stated reason for 3 tasks was that place tasks add a `misplace` failure class, addressing the "only 2 clusters" risk. I measured that class out of existence at the calibrated noise: all 34 Stack failures at sd=0.008 have end-of-lift gap ~0.001, i.e. all miss_grasp. So the third task buys no cluster diversity, and two of the four tasks fail the attribution gate. Two tasks — one to evolve on, one to transfer to — is what the evidence supports.

On the "does it run" question the design is essentially correct and unusually well-grounded; I found only additions, not errors. Worth recording: spawn requires the *driver* to be an importable file, not just the worker (I hit BrokenProcessPool running from stdin, which is exactly how an agent would naturally invoke it); `env.step()` past the horizon raises rather than saturating; `PickPlace._check_success()` mutates `self.objects_in_bins`, so it is a side-effecting predicate being used as a pure label; PickPlace success requires the gripper to be >=4.24cm away (`r_reach < 0.6`), making the retreat phase load-bearing rather than cosmetic; and this machine has 18 logical but only 6 performance cores, which is why 10 workers yields 1167 and not 1240 eps/min. Also, the system `python3` is 3.14.6 while pyproject caps <3.13, so any step invoking bare `python3` instead of the venv fails immediately.

Finally I kept what the design got right and resisted trimming it: the three-tier privilege boundary is earned — round 1's own finding is that de-privileging the recovery collapses +50pt to +13.3pt, so a gate that does not report both numbers reports a fake win. The size axis is earned and I confirmed the write applies. The power analysis is earned and exact. The terminal-labelling rule is earned. Those are the parts of the design doing real work.

## REJECTED
- Keeping sd=0.009 and the 50.0% headline baseline: refuted by direct re-measurement. N=60 gives 0.500 but N=200 gives 0.600, outside the design's own 35-55% target band and into the region where its own power table shows meaningful loss. The constant was fit to a 6.5%-SE point estimate. Replaced with sd=0.013 -> 0.450 at N=200.
- Keeping PickPlaceCan and PickPlaceMilk in the suite: rejected because PickPlaceCan gives 0.800 at sd=0, so 20% of failures are not perception-attributable. The design's entire interpretability argument is 'sd=0 -> 100%, therefore every failure is a perception fault and is rescuable.' At 80% a critic gets credit for rescuing episodes that had no perception fault, which is unfalsifiable inside the harness.
- Keeping pickmilk as an uncalibrated transfer task 'so it cannot leak': rejected. This is presented as anti-leakage rigor but it means there is zero evidence the transfer task is even in the viable band; if it turns out to be 5% or 95% the transfer claim is untestable and you discover that only at promotion. Difficulty calibration on the frozen policy is not the leak vector - inspecting failure traces is. Replaced by Stack, which is calibrated (42.5%), attributable (92.5% at sd=0), and has a verified separable signal.
- Measuring the critic's detection feature at terminal state: refuted hard. Stack terminal finger_gap is [0.0788,0.0788] for BOTH success and failure; PickPlaceCan's is inverted. The feature must be read at a declared phase checkpoint (Stack @ end-of-LIFT separates cleanly, 0.0389-0.0555 vs 0.0010-0.0017). Note this rejects only the FEATURE read; the terminal success LABEL is correct and retained.
- Justifying 3 tasks by the 'misplace' third failure class: rejected on measurement. At the calibrated sd=0.008 every Stack failure has end-of-lift gap ~0.001, i.e. 100% miss_grasp. The place tasks add no cluster diversity at the noise level actually used, so they do not address the 'only 2 clusters' risk they were introduced to address.
- Keeping the five secondary perturbation axes (object pose, initial arm config, distractors, target pose, mass): cut as unearned for tonight. The design's own measurements show them near-inert (0.475->0.450, ->0.400) and none is required by the gate. Size alone carries both the difficulty gradient and the memorization trap. Keep the others as a documented backlog, not code.
- Claiming ~1240 episodes/min: measured 1167 at 10 spawn workers. Minor, but the machine has only 6 performance cores behind its 18 logical ones, so the figure should not be quoted above what was observed, and worker count should not be scaled past ~10 expecting linear return.
- Claiming the memorization trap defeats any dev-tuned threshold in tau ~0.019-0.030: overstated. At N=200 the dev-consistent interval is (0.0028, 0.0176) and held-out small-object successes bottom out at 0.0135, so only thresholds in (0.0135, 0.0176) actually break - roughly the upper quarter of the interval, not the whole mid-range. The trap is real and worth keeping; its teeth are narrower than advertised and should be reported as a measured margin rather than asserted.
- Rewriting the frozen policy to add orientation control so NutAssembly becomes reachable: correctly rejected by the original design and still rejected. It changes the frozen baseline and invalidates cross-generation comparison, for a task family the harness does not need yet.
- Abandoning robosuite for hand-written MuJoCo scenes: still rejected, and the original reasoning holds. I hit three robosuite-specific traps tonight (hard_reset discarding pre-reset writes, the side-effecting PickPlace success predicate, the r_reach<0.6 retreat requirement) and all three are cheaper to document than to re-derive.

## RISKS
- The held-out difficulty match must be re-run at sd=0.013. I verified size U[0.018,0.028] passes the |delta|<=0.08 gate at sd=0.009 (0.620 held-out vs 0.600 dev) and that U[0.015,0.025] FAILS it (0.510, delta 0.090) - so the gate has real teeth and is not decorative. But dev drops from 0.600 to 0.450 at sd=0.013, so the clamp will almost certainly need re-centering downward. Do this first tonight; it is 400 episodes, ~20s.
- Stack carries a 7.5% attribution floor (sd=0 = 0.925). Any claimed rescue rate on Stack must have that subtracted, or the harness will over-credit the critic by up to 7.5 points. Enforce it as a reported number, not a footnote.
- The revised suite has ONE search task. All evolutionary pressure is on lift, so an overfit-to-lift critic is the dominant failure mode and Stack is the only thing standing between it and a false transfer claim. This is a real narrowing versus the original 3-task plan - accepted deliberately, because two of the original tasks failed the attribution gate, but it means the Stack transfer result is load-bearing and must never be searched on.
- Phase-anchored feature reads introduce a new degree of freedom the proposer could exploit: choosing WHICH checkpoint to read. If the checkpoint is searchable, the search can find a checkpoint that happens to separate on dev by luck. The checkpoint must be declared per-task in the frozen manifest, not selected by the proposer.
- finger_gap separation at N=200 is 6.4x, not 12.5x, and on held-out the failure gap reaches 0.0063 (vs dev's 0.0023). The margin is narrower than the design assumed in both directions, so a threshold set just above the dev failure mode can now false-negative on held-out. Report tau's margin to BOTH modes on both splits, as the design already requires - that requirement is correct and becomes more important, not less.
- PickPlace._check_success() mutates self.objects_in_bins. Nothing in the revised suite uses PickPlace, but if pickcan is later readmitted, any code reading env.objects_in_bins after a partial or mid-episode call gets contaminated state. Treat the predicate as impure and never expose that array as a feature.
- The 20% non-perception failure rate on PickPlaceCan is undiagnosed. I did not determine whether it is my place-phase z-offsets, can geometry, or the bin-traverse distance. Until it is diagnosed, do not assume the pick-and-place family is recoverable by tuning - it may be a genuine limitation of a position-only policy over a 0.75m traverse.
- spawn's file-import requirement is an agent-specific trap: driving the pool from `python -c` or a heredoc gives BrokenProcessPool, which reads like a MuJoCo/arm64 incompatibility rather than a packaging mistake. An autonomous overnight run that hits this could wrongly conclude parallelism is unavailable and fall back to serial, silently costing 10x. Pin the driver as a module with a __main__ guard and regression-test it.
- Everything here is single-machine and single-night. The 6-performance-core ceiling means the throughput headroom is smaller than the 18-logical-core count suggests; a campaign that scales workers past ~10 expecting linear return will stall.

## SPEC
# Governor Task Suite + Frozen Base Policy — v2 (adversarially re-measured)

All figures re-measured tonight on this machine: macOS arm64, Python 3.12.13, mujoco 3.3.7,
robosuite 1.5.2, numpy 1.26.4. Raw log: `/private/tmp/claude-501/-Users-yusenthebot-Desktop/16dbbfa8-38e0-4039-aac6-48516c9bcd53/scratchpad/adversarial-measurements.txt`

## 0. Platform preconditions (all hit tonight; assert them)

```python
assert sys.version_info[:2] == (3, 12)        # system python3 is 3.14.6; pyproject caps <3.13
assert mujoco.__version__ == "3.3.7"          # >=3.4 renames MjData.qM -> M
assert "MUJOCO_GL" not in os.environ          # illegal value on macOS
mp.set_start_method("spawn")                  # fork -> BrokenProcessPool
```
**The multiprocessing DRIVER must be an importable .py file with a `__main__` guard.**
Running the pool from `python -c` or a heredoc gives BrokenProcessPool that looks like a
MuJoCo/arm64 incompatibility but is not. Regression-test this.
Other traps: `env.step()` past the horizon raises `ValueError("executing action in
terminated episode")`; `PickPlace._check_success()` MUTATES `self.objects_in_bins`.

Hardware: 18 logical / **6 performance** cores. 10 workers = **1167 eps/min** measured.

## 1. Suite — 2 tasks

| id | env | horizon | sd | dev baseline | sd=0 | detect checkpoint | role |
|---|---|---|---|---|---|---|---|
| `lift` | Lift | 100 | **0.013** | **45.0% ±6.9 (N=200)** | 1.000 | end-of-`lift` | dev + search |
| `stack` | Stack | 220 | 0.008 | **42.5% (N=40)** | 0.925 | end-of-`lift` | **transfer only** |

CUT: `pickcan`/`pickmilk` (sd=0 = 0.800, attribution broken), NutAssembly (0% at sd=0).

Lift dev recalibration, N=200 terminal (the design's sd=0.009 gives **0.600**, not 0.500):

| sd | terminal | latched | gap_succ | gap_fail | sep |
|---|---|---|---|---|---|
| 0.011 | 0.530 | 0.580 | [0.0178,0.0607] | [0.0010,0.0021] | 8.5x |
| **0.013** | **0.450** | 0.510 | [0.0147,0.0606] | [0.0010,0.0023] | **6.4x** |
| 0.015 | 0.385 | 0.435 | [0.0174,0.0605] | [0.0010,0.0044] | 4.0x |

**Rule: no calibration constant may be set from N < 200.** (SE at N=60 is 6.5%.)

## 2. Task admission gate (mechanical, run before a task enters the manifest)

```python
def admit(task) -> bool:
    return (sd0_success(task, n=40) >= 0.90            # attribution
        and 0.35 <= dev_success(task, n=200) <= 0.55   # power
        and separates_at_checkpoint(task))             # zero-privilege detectability
```
Measured: `lift` (1.000 / 0.450 / clean) PASS. `stack` (0.925 / 0.425 / clean) PASS with a
**7.5% attribution floor that MUST be subtracted from any claimed rescue rate**.
`pickcan` (0.800) FAIL.

## 3. Feature reads are PHASE-ANCHORED, never terminal  ← the v1 bug

The success LABEL is terminal. The detection FEATURE is not. Measured on Stack, N=60:

| checkpoint | success | failure | verdict |
|---|---|---|---|
| end-of-`close` | [0.0392,0.0555] | [0.0055,0.0533] | OVERLAP |
| **end-of-`lift`** | **[0.0389,0.0555]** | **[0.0010,0.0017]** | **SEPARATED** |
| TERMINAL | [0.0788,0.0788] | [0.0788,0.0788] | **ZERO — identical** |

Terminal gap is dead on every place task (gripper open after `release`; PickPlaceCan is
*inverted*, failure gap larger). The checkpoint is **declared per task in the frozen
manifest and is NOT searchable** — otherwise the proposer can shop for a lucky checkpoint.

```python
@dataclass(frozen=True)
class TaskSpec:
    env_name: str; horizon: int; sd: float
    detect_checkpoint: str        # phase name; feature read at its LAST step
    sd0_floor: float              # subtract from claimed rescue
```

## 4. Success label

```python
success = bool(env._check_success())   # ONCE, at terminal state
```
Never latch. Lift N=200 sd=0.013: latched 0.510 vs terminal 0.450 (6% lifted-then-dropped).
On Stack latched == terminal, so this rule is Lift-motivated but harmless elsewhere.

## 5. Frozen policy (unchanged from v1 except sd)

```python
KP, HOVER, GRASP_Z, LIFT_Z = 8.0, 0.10, 0.005, 0.25
PHASES_PICK = [("above",25,HOVER,-1), ("descend",25,GRASP_Z,-1),
               ("close",12,GRASP_Z,+1), ("lift",38,LIFT_Z,+1)]
p_hat = p_true + default_rng(seed*7919 + 13).normal(0, sd, 3)   # ONCE, t=0, 3-D
a[:3] = clip(KP*(goal - eef_pos), -1, 1); a[3:6] = 0; a[6] = grip
```
Stack appends `("carry",40,pB,LIFT_Z,+1), ("lower",30,pB,0.055,+1), ("release",12,pB,0.055,-1),
("retreat",18,pB,LIFT_Z,+1->-1)`; total 200 steps, horizon 220.
`policy_sha256` over source + constants + phase table + sd-by-task.

## 6. Perturbation — ONE axis

Object half-extent, applied **post-reset** (hard_reset discards pre-reset writes):

```python
env.reset()
gid = m.geom_name2id("cube_g0"); m.geom_size[gid] = [half]*3
qadr = m.jnt_qposadr[m.body_jntadr[m.body_name2id("cube_main")]]
d.qpos[qadr+2] = 0.825 + half + 1e-4      # free-joint z fix; omit -> penetration
env.sim.forward(); obs = env._get_observations()
```
Verified applied (0.0201 -> requested) and live: half 0.012/0.016/0.021/0.026/0.031 ->
success 0.100/0.250/0.475/0.725/0.725, with **failure gap pinned at 0.0010 at every size**
while success gap tracks width — the memorization trap.

All five secondary axes CUT (measured near-inert: 0.475->0.450 pose, ->0.400 arm, ->0.450 mass).
Friction stays REJECTED (v1's diagnosis is correct).

## 7. Seeds and the difficulty-match gate

```python
DEV_SEEDS, HELDOUT_SEEDS = range(0, 600), range(10_000, 10_200)
half = default_rng((SALT ^ (seed*2654435761)) & 0xFFFFFFFF).uniform(lo, hi)  # pure fn of (split,seed)
```
The gate has teeth — measured at sd=0.009 (dev 0.600): U[0.018,0.028] -> 0.620, delta 0.020 **PASS**;
U[0.015,0.025] -> 0.510, delta 0.090 **FAIL**. **Re-run and re-center at sd=0.013 (dev 0.450)
before registering the manifest** — this is the first job tonight, 400 eps / ~20s.

## 8. Failure classes

`miss_grasp` (checkpoint gap < 0.005) dominant; `slip_after_lift` (latched but not terminal,
6% on Lift); `horizon_incomplete` fallback only. **`misplace` is NOT emitted** — measured out
of existence: all 34 Stack failures at sd=0.008 have end-of-lift gap ~0.001, i.e. 100% miss_grasp.

## 9. Gate (unchanged — verified exact)

Exact one-sided McNemar, alpha=0.025. Power at n=200 (20k trials): 42% -> 0.37/0.96/1.00 for
rescue 10/20/30%; 70% -> 0.01/0.14/0.52; 90% -> 0.00/0.00/0.00. Minimum passing evidence is a
6-discordant clean sweep (p=0.0156); 5-0 fails at 0.03125. Target band 35-55%.
Gate reports **zero-privilege AND full-privilege** scores, privilege budget covering
critic AND recovery (round 1: +50pt collapses to +13.3pt n.s. when recovery is de-privileged).

## 10. Tonight's build order

1. Re-center held-out size clamp at sd=0.013 until |p_ho - p_dev| <= 0.08.  (~20 s)
2. `lift` end-to-end: 200 dev + 200 held-out + 200 regression.             (31 s measured)
3. Paired McNemar + privilege ablation, both numbers reported.
4. Only then add `stack` as the transfer arm.

Full 2-task generation is under 3 minutes of wall clock. Proposer latency is the constraint,
not rollouts — v1 was right about that.
