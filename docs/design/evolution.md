======================================================================
# VERSION 1
======================================================================

## QUESTION
Design the evolution loop for `governor` (physical-harness) — adversarially reviewed and revised to the version that actually runs and is verifiable overnight on macOS arm64, CPU-only MuJoCo. Covers: loop state machine, proposer interface, gate specification with exact statistics, artifact layout, critic execution, and failure clustering.

## DECISION
The original decision survives on architecture and dies on arithmetic. I ran the two claims it rests on. Both verdicts are empirical, not rhetorical.

**What I verified holds (keep):** `sandbox-exec` exists at `/usr/bin/sandbox-exec` on macOS 26.3.2; the profile compiles; `PermissionError: [Errno 1] Operation not permitted` on a write outside the allowed subpath — kernel confinement is real. `python -S` starts under it (4 `sys.path` entries). Idle sandboxed socketpair round trip: **p50 37 µs, p99 153 µs, boot 32 ms** — better than D1's own targets. And the sandboxed-IPC critic costs *nothing* in throughput under the real load: 80 ep/min with no critic vs 82 ep/min with a sandboxed critic per tick. D1's central latency argument is correct and I keep it.

**What I verified breaks (six revisions):**

**R1 — The SBPL profile in D2 does not deny network. Verified: `NETWORK REACHABLE UNDER PROFILE`.** `(allow default)(deny file-write*)` closes file writes and leaves exfiltration wide open, while D2's prose claims it covers "file writes, network, exfiltration." One-token fix — add `(deny network*)` — but it is exactly the class of gap that gets shipped because the prose asserted it. Also: `python -S` gives 4 path entries, so `-m governor.critic_worker` cannot import; the worker must be an absolute script path with no package import.

**R2 — The 500 µs hard budget is self-refuting under the design's own infra ceiling. This is the design-killing finding.** Idle numbers do not survive the real configuration. With 10 rollout workers doing genuine robosuite stepping plus 10 sandboxed critic children on 18 cores (6 P + 12 E), measured over 3000 real ticks, twice:

```
p50 = 49-55 µs   p90 = 6.1 ms   p99 = 108-169 ms   max = 358-473 ms
ticks over the 500 µs HARD budget: 14.7% and 16.9%
ticks over even 5 ms:              10.4%
```

The design routes every hard-timeout to `infra_invalid` and voids any gate arm whose `infra_invalid_rate > 0.05`. At ~15% per-tick violation over ~100 ticks/episode, **essentially 100% of episodes become `infra_invalid` and every gate voids as `INCONCLUSIVE_INFRA`. The campaign provably cannot complete.** The mean is fine; the tail is 1000× the budget. The cause is not the critic — it is the macOS scheduler descheduling a child that holds no CPU. **Fix: the budget must be CPU time measured in the child (`resource.setrlimit(RLIMIT_CPU)` + `time.process_time()`), never wall-clock measured in the parent.** "The critic is looping forever" is a CPU-time question; "the scheduler parked it for 100 ms" is not a candidate defect and must never be scored as one. Parent wall-clock survives only as a loose liveness backstop (2 s). And the design's named fallback is also dead: I measured the batched-tick protocol at **34.3% of batches over 500 µs** — batching moves the tail, it does not remove it.

**R3 — The seed budget in D5 is off by 2.6× because env construction was never measured.** `make_env` = **1195 ms**, `reset` = 99 ms, 100 steps = 565 ms (5.65 ms/step). Construction is 68% of an episode and is *mandatory*: the determinism contract requires `suite.make(seed=N)` per seed, so the env cannot be reused across seeds. Real governed throughput at 10 workers is **~80 ep/min, not 212** — the doc's 212 figure does not carry per-episode construction at this concurrency. So "150 dev episodes cost ~42 s" is really ~113 s and "a 25-round campaign fits comfortably under an hour" is really ~2 hours. Overnight-survivable, but the inflation to 150/40/60 is no longer free. Tonight: **dev 60 / heldout 40**, matching the seed counts round 1 already validated. Corollary the design got backwards: since construction dominates and the parent is frozen and deterministic, **parent adoption is worth 2.6× more than credited** — so adopt the parent on *every* gate including held-out. The design's "held-out must execute both arms fresh" doubles the most expensive block to buy something the determinism canary already provides.

**R4 — Deferring the privilege ablation to CONFIRM is the single largest self-deception vector, and the project's own STATUS.md says so.** `docs/headline-finding.md` is unambiguous: the same critic-recovery pair scores **+50.0pt (p<1e-6, 30 fixed / 0 broken)** with a privileged recovery percept and **+13.3pt, not significant (p=0.057)** once the recovery reads at the policy's own noise. STATUS.md: "门禁必须能同时报出这两个数，否则会误报成功." The design puts `ablation_zero_privilege.json` in `confirm/` — at campaign end. A campaign gating on the privileged number promotes 25 generations of gains that are artifacts of simulator access and discovers it only in the final report. **The ablation arm is a per-candidate gate artifact, not a terminal report**, and G-ABL is a *promotion* condition. This is also the one thing that makes GOAL.md acceptance #3 real rather than decorative.

**R5 — Two metrics that cannot fail, and one gate that cannot reject.** (a) G1 lists `len(broken) == 0` as a pass condition while admitting it is "== 0 by construction" — gate seeds ⊂ target cluster ⊂ parent failures, so the parent has nothing to break. It is a green light wired to nothing; the real breakage check is G2 on the full dev block, and G1 should report rescue rate only. (b) Promotion is decided *entirely on dev seeds* — the same block the proposer fit on — while the only gate carrying an inferential test (held-out) is report-only and can never reject. The stall detector needs `gain <= 0` twice consecutively, so an unbroken chain of `gain = 1` generations never stalls. The fit/gate split inside the cluster is a real defense and I keep it, but it is a within-dev split; nothing outside dev can stop a promotion. Tonight, with one generation, this is moot — but the rule must be written now: **held-out gets a rejecting role (`mode=validation`) the moment generations exceed 3**, because report-only is an anti-overfitting control for a *many-round* campaign that has some other brake, and this design has none.

**R6 — D4 reaches the right conclusion through an argument that, applied consistently, deletes the gate it wants to keep.** "Under bitwise determinism the no-effect null predicts b=0 with probability 1, so a p-value is theater" is equally true of the held-out gate: the parent is frozen and deterministic, the candidate is deterministic, both arms are facts. If determinism voids inference, it voids G3. It does not, and the reason is that **the sampling unit is the seed draw from the task-instance population, not the execution.** Under that framing McNemar on held-out is legitimate. The actual reason to drop the same-seed p-value is *selection*, not determinism: those seeds are conditioned on being failures and their sibling fit seeds trained the threshold, so the null is not "no effect" but something unstated. Keep the decision, replace the justification — otherwise a later round "applies D4 consistently" and deletes the only real test in the design.

**What I cut as unearned, and it is a lot.** Failure clustering is the clearest: 34-dim standardized vectors, complete linkage, silhouette-selected cuts, medoid representatives, blinded LLM re-partition. This project has **one task and one dominant failure mode with 40× separation** (`finger_gap`: 0.038-0.046 success vs 0.0010-0.0012 failure). The hard partition on `(failure_class, phase)` — which the design correctly identifies as the load-bearing part — already returns the answer, and silhouette over buckets of n<6 is not meaningful. Ship ~25 lines of hard partition plus count ranking; the metric is a Night-3 problem that only becomes real with a second task family. Also cut for tonight: the 15-phase state machine (5 phases), the content-addressed store with four ledgers (two JSONL files), the DSL front end (the `Trigger` dataclass in `search.py` *is* the front end and already exists), `EnsembleProposer`, the five proposal templates (one, T1, is what round 1 proved), and the P0/P1/P2 tier scheme — which silently rewrites the tested binary `Privilege` enum in `features.py` and invents `*_est` features that do not exist in the registry. Do not rewrite a working, tested privilege mechanism to add a tier nothing uses yet.

**And the largest spec gap, which the review must not let pass: recovery is never specified.** `recovery_source` and a `failure_class -> RecoverySpec` table appear, but nothing says where recovery executes, how it obtains observations, or how its privilege is accounted — while `headline-finding.md` proves the entire result lives in exactly that question, and progress.md records the author already getting burned by it once ("我自己漏了 recovery 的特权... 是消融实验把 `target = obs["cube_pos"]` 这句抓出来的"). D1/D2 spend their whole budget isolating the *critic*, which reads features, and leave unspecified the *recovery*, which reads features **and writes actions**. The revision below makes `RecoveryPolicy` a first-class seam with its own declared feature set and its own privilege cost, and makes the ablation sweep the recovery percept.

**On the sandbox, the one place I overrule D1's "day one".** The isolation argument is sound and I keep the seam — but its threat model only activates for *arbitrary generated code*. Tonight's critic is a `Trigger` dataclass: data, not code. `features.py` already makes undeclared access structurally impossible (`privilege_cost` raises `KeyError` on an unknown name; `extract` projects only declared names), so a data-only trigger has no address-space threat to isolate and is `scoring_safe` in-process. `SubprocessCriticRuntime` is built behind the same ABC as the second provider, the night arbitrary code lands — with `scoring_safe=False` on any code-carrying bundle until it does. That is fail-closed and it keeps D1's one-executor principle intact rather than trading it for a second runtime.

## RATIONALE
**On method.** I did not evaluate this design by reading it. Every verdict above is a command I ran on this machine: the sandbox profile probe (network reachable), the idle latency bench (p99 153 µs), the 10-worker load bench with real robosuite stepping (p99 169 ms, 16.9% over budget), the mode comparison isolating IPC from contention (80 vs 82 ep/min), and the episode cost breakdown (make_env 1195 ms). The design's most confident numbers — "<5% of a sim step", "212 episodes/min", "500 µs p99 hard budget" — are the ones that did not survive contact, and they did not survive in a specific and instructive way: **the architecture was right and the operating points were wrong.** A design that had been merely argued would have shipped a campaign that voids every gate on the first generation and reports `INCONCLUSIVE_INFRA` forever, which is the failure mode this review exists to prevent.

**Why the tail latency finding matters more than its size suggests.** p50 = 49 µs against a 500 µs budget looks like a 10× margin. It is not a margin at all, because the enforcement rule is a per-tick deadline and the distribution has a 473 ms tail. The design chose wall-clock because it was reasoning about "a pathological candidate that loops," but wall-clock in the parent cannot distinguish a looping candidate from a descheduled one, and on a machine with 12 efficiency cores and 20 runnable processes the second case is 15% of ticks. This is precisely the class of error the risks section gestured at ("macOS scheduler wakeup jitter... could blow past it") and then mispriced by three orders of magnitude while proposing a fallback (batching) that I measured making the tail *worse*. CPU-time in the child is the honest instrument: it measures the thing the budget is actually about and is immune to scheduling. That correction saves D1's architecture rather than replacing it.

**Why the ablation timing is the review's most consequential change.** Everything else here is engineering; R4 is about whether the campaign's headline number is true. The project has already run this experiment by hand and knows the answer: the same pair is +50pt or +13.3pt-not-significant depending solely on what the recovery is allowed to see. A loop that gates on the privileged arm will promote, and promote, and promote — every gate green, every McNemar p tiny, every artifact hashed and preregistered — and the entire ledger will be an artifact of `obs["cube_pos"]`. All the rigor in the design (content hashes, preregistration, frozen parents, exact tests) is orthogonal to this failure and would faithfully certify it. The only defense is running the zero-privilege arm *as a promotion condition*, per candidate, which costs 30 episodes ≈ 23 s. That is the cheapest insurance in the document and the design filed it under `confirm/`.

**Why I cut clustering rather than fixing it.** The critique of Zetta's `segment_similarity` is correct and well-argued — 0.9 of the weight on two encodings of the same prose string really is a restatement of the hard-partition key. But the correct response to "the source's metric is bad" is not "build a better metric," it is "notice that on this task the metric is never consulted." One task, one failure mode, 40× separation: the hard partition returns a single bucket and the ranking is a `sorted()` call. Building a 34-dim standardized feature space with silhouette model selection to partition a set that has one element is the exact shape of complexity that looks rigorous in a design document and produces zero decisions in a run. It becomes real at the second task family, and that is when to build it — with the design's analysis in hand, which is genuinely good work that should be preserved as a note rather than as code.

**Why "smallest version tonight" is not a retreat from ambition.** The floor in GOAL.md is five acceptance criteria, and the revised spec below hits four of them in one generation: a real end-to-end campaign that is not a mock (#1), a paired significance result (#2, on dev with held-out reported), a declared privilege budget with a zero-privilege ablation for every promoted rule (#3, and materially stronger than the original because the ablation gates), and a reconstruction-invariant test that deliberately fires (#4). It does that by reusing the ~500 lines that already exist and work — `features.py`'s namespace-as-declaration, `env.py`'s `suite.make(seed=)` contract with four green regression tests, `search.py`'s EOD scan that already beat the author's hand-tuning — and adding the two things genuinely missing: a recovery executor and a gate. The original design would have needed the sandbox protocol, the AST validator, the CAS, four ledgers, fifteen phases, and a clustering pipeline *before* the first governed episode ever ran, and by R2's arithmetic that first campaign would then have voided itself. Ship the loop that closes tonight, and let the sandbox, the richer expression language, and the clustering metric be Night 2, 3, 4 — each landing on a loop that already demonstrably runs.

**One thing I want to flag rather than fix.** `search.py` scores triggers with `recall - 1.2*fpr + 0.25*(lead/n_steps)`, and progress.md already notes the 0.25 is unvalidated *and already flipping the ranking* (`gripper_effort` beats `finger_gap` on lead alone). The revised SearchProposer promotes lead time from a 0.25-weighted term to the primary objective under a feasibility constraint, which is a defensible move — a detector firing at divergence gives recovery nothing — but it is a *larger* dose of the untested knob, and it will systematically prefer earlier, noisier triggers. The sensitivity analysis progress.md asked for should run before that objective is trusted, and until then the gate result on gate seeds versus the fit-seed shadow recall is the diagnostic to watch.

## REJECTED
- Keep the 500 us / 5 ms wall-clock critic timeout with SIGKILL-and-respawn. Rejected on measurement, not judgment: under the real 10-worker configuration 14.7-16.9% of ticks exceed 500 us and 10.4% exceed 5 ms (p99 = 108-169 ms, max = 473 ms). Every timeout routes to infra_invalid, and the design's own 5% per-arm infra ceiling then voids every gate arm as INCONCLUSIVE_INFRA. The campaign cannot complete. Replaced by a CPU-time budget enforced in the child (RLIMIT_CPU + time.process_time), which measures the thing the budget is about and is immune to scheduler jitter.
- Fall back to the batched-tick protocol the design names as its mitigation if the latency budget cannot be held. Rejected on measurement: batching 10 ticks per message still put 34.3% of batches over 500 us. Batching relocates the tail rather than removing it, and it pays for that with detection latency, which is the one thing the lead-time objective is trying to buy.
- Ship the SBPL profile exactly as specified in D2. Rejected: verified that `(version 1)(allow default)(deny file-write*)(allow file-write* ...)` leaves the network fully reachable from the confined child (probe connected to 1.1.1.1:80 and printed NETWORK REACHABLE UNDER PROFILE), while D2's prose claims the profile addresses 'file writes, network, exfiltration'. `(deny network*)` must be in the profile string. Also `python -S` yields 4 sys.path entries, so `-m governor.critic_worker` cannot resolve; the worker must be invoked as an absolute script path with zero package imports.
- Keep the dev 150 / held-out 40 / confirm 60 seed budget and the '~1.9 min per accepted generation' arithmetic. Rejected: it is derived from 212 episodes/min, which does not carry per-episode environment construction at 10-worker concurrency. Measured make_env = 1195 ms against 565 ms for the whole 100-step rollout, and construction is mandatory because the determinism contract requires suite.make(seed=N) per seed. Real governed throughput is ~80 ep/min, so the generation cost is ~5 min and a 25-round campaign is ~2 hours. Tonight uses dev 60 / heldout 40, the sizes round 1 already validated.
- Execute both arms freshly on the held-out block (parent adoption disallowed). Rejected once construction cost was measured: the parent is frozen and bitwise deterministic, so the second arm is a re-derivation of a known result at the most expensive point in the budget. The determinism canary already supplies the guarantee that fresh execution was buying. Adopt the parent on every gate and spend the saved episodes on the privilege-ablation arm, which buys something nothing else provides.
- Defer the zero-privilege ablation to a campaign-terminal confirm/ablation_zero_privilege.json. Rejected as the design's largest self-deception vector, and the project's own STATUS.md already names it: docs/headline-finding.md shows the identical critic-recovery pair scoring +50.0pt (p<1e-6) with a privileged recovery percept and +13.3pt (p=0.057, not significant) with a de-privileged one. Gating on the privileged arm promotes generation after generation of simulator artifacts with every hash, preregistration, and exact test faithfully certifying them. The ablation arm costs ~23 s and must be a per-candidate promotion condition.
- Keep `len(broken) == 0` as a pass condition on the same-seed gate. Rejected as a metric that cannot fail: gate seeds are a subset of the target cluster, which is a subset of parent failures, so the parent has no successes available to break. The design concedes this in the same line that asserts it ('== 0 by construction; ASSERT'). Breakage is measured by G2 over the full dev block; G1 reports rescue rate and its Clopper-Pearson bound.
- Keep held-out permanently report-only (mode=test) as the anti-overfitting control. Rejected as specified, kept as amended: report-only is the right control in a campaign that has some other brake on dev-fitting, and this design has none — promotion is decided entirely on dev, the block the proposer fit on, and the stall detector requires gain<=0 twice consecutively so an unbroken chain of gain=1 generations never fires. Held-out must acquire a rejecting role (mode=validation) once a campaign exceeds 3 generations.
- Keep D4's justification that bitwise determinism makes any p-value theater. Rejected as an argument while keeping its conclusion: it applies with equal force to the held-out gate (frozen deterministic parent, deterministic candidate, both arms are facts), so applied consistently it deletes the only real test in the design. The sampling unit is the seed draw from the task-instance population, not the execution. The correct reason to drop the same-seed p-value is selection — those seeds are conditioned on being failures and their sibling fit seeds trained the threshold.
- Build the 34-dimensional standardized clustering pipeline with complete linkage and silhouette-selected cuts. Rejected as unearned on this task: one task family, one dominant failure mode, 40x separation on finger_gap. The hard partition on (failure_class, phase) — which the design correctly identifies as the load-bearing piece — returns a single bucket, and silhouette over buckets of n<6 selects nothing. The critique of Zetta's prose-dominated similarity metric is correct and worth keeping as a note; the code becomes worth writing at the second task family.
- Adopt the P0/P1/P2 privilege tier scheme with `*_est` estimated features. Rejected for tonight: it silently rewrites the binary OBSERVABLE/PRIVILEGED enum in governor/features.py that is working, tested, and enforcing (privilege_cost raises KeyError on unknown names, so a proposer cannot invent a feature to reach past the budget), and the `*_est` tier it adds consists of features that do not exist in the registry. The ablation sweep over recovery percept noise delivers the sim-to-real curve the tier scheme was reaching for, without touching a load-bearing mechanism.
- Build SubprocessCriticRuntime with the Seatbelt profile, AST allow-list, and wire protocol before the first governed episode runs. Rejected on sequencing, not on merit — the isolation argument is sound and the seam is kept. Its threat model activates only for arbitrary generated code; tonight's critic is a Trigger dataclass, which is data, and features.py already makes undeclared access structurally impossible for data. The provider is built the night arbitrary code lands, with scoring_safe=False on any code-carrying bundle until then.
- Ship the 15-phase state machine, content-addressed store, four append-only ledgers, DSL front end, five proposal templates, and EnsembleProposer as the first drop. Rejected on time-to-first-real-result: all of it must land before a single governed episode exists, and by the corrected arithmetic that first campaign would then void itself on the infra ceiling. Five phases and two JSONL files carry the whole reconstruction invariant for a one-generation loop; the Trigger dataclass in search.py already is the DSL front end.
- Leave the recovery execution seam unspecified, as the original does. Rejected as the design's largest gap: D1 and D2 spend their entire isolation budget on the critic, which reads features, and say nothing about where recovery runs, how it obtains observations, or how its privilege is accounted — while headline-finding.md proves the whole result lives in exactly that question and progress.md records the author already being burned by an unnoticed `target = obs['cube_pos']` inside a recovery. RecoveryPolicy becomes a first-class seam with its own declared feature set and its own privilege cost.

## RISKS
- The 80 ep/min governed throughput I measured came from 3 episodes per worker, so per-worker interpreter and import warmup is amortized over very few episodes and the steady-state number is probably better. I am budgeting on the pessimistic figure deliberately, but the generation-cost estimates below could be 20-40% conservative. Re-measure over a full 60-seed block before trusting the campaign wall-clock projection.
- Moving the critic budget to CPU time removes the scheduler-jitter failure but weakens the liveness guarantee: a child blocked on something that consumes no CPU (a futex, a page fault storm) burns no CPU-time budget and is caught only by the loose 2 s wall-clock backstop. For a data-only Trigger with a static op bound this cannot happen; it becomes real the night arbitrary code lands, and the subprocess provider must ship with both budgets from its first commit.
- Cutting to one generation tonight means the fit/gate split inside the target cluster is exercised but the multi-generation dynamics it defends against are not. The dev-only promotion path (R5b) is harmless at one generation and becomes the dominant overfitting risk the moment the loop runs unattended overnight for many rounds. The mode=validation switch at generation 3 must be implemented before the first long run, not after the first surprising result.
- The privilege ablation as specified sweeps the recovery percept noise, which reproduces the round-1 curve faithfully. It does not catch a recovery that leaks privilege through a channel other than the percept - for instance by conditioning its phase schedule on something derived from a privileged feature at admission time. The declared-feature check covers the direct case; an indirect one would need the same address-space separation argument D2 makes for critics, applied to recovery, which is another reason the subprocess provider matters for recovery at least as much as for critics.
- Promoting median lead time from a 0.25-weighted term to the primary search objective is a larger dose of a knob progress.md already flags as unvalidated and already observed flipping the ranking (gripper_effort over finger_gap). It will systematically prefer earlier and noisier triggers, and the failure mode is silent: a trigger that fires early enough to look excellent on shadow replay but whose recovery cannot exploit the lead. Gate-seed rescue rate materially below fit-seed shadow recall is the diagnostic; run the sensitivity sweep before trusting the objective.
- Cutting clustering to a hard partition means the loop has no mechanism to notice that a second, distinct failure mechanism has appeared inside the same (failure_class, phase) bucket - it will silently fit one detector to a mixture. On this task that is safe because the mechanism is known and singular; it becomes unsafe the moment a second task family or a real BC policy makes failures heterogeneous, and there is no alarm that fires when it happens. Add a within-bucket dispersion report before the second task family, so the decision to rebuild clustering is triggered by data rather than by memory.
- Adopting the parent on every gate including held-out makes the whole design depend on the determinism canary being correct. The canary as originally specified re-runs parent episodes, which carry no critic and therefore cannot detect cross-episode critic state leakage (dwell and cooldown counters surviving an episode boundary inside a reused worker) - the exact bug that would silently corrupt pairing. The canary must run governed episodes, and even then it samples 3 of them.
- sandbox-exec is verified present and enforcing on macOS 26.3.2 but remains formally deprecated, and the design fails closed if a future macOS removes it. That is the correct behavior and it is a hard platform dependency that belongs in the README, not in a risks list nobody reads. Deferring the subprocess provider to Night 2 slightly increases the exposure window in which that dependency is discovered late.

## SPEC
══════════════════════════════════════════════════════════════
WHAT I WOULD BUILD TONIGHT — reuses the 501 lines that exist and work
══════════════════════════════════════════════════════════════

Existing and kept unchanged: `governor/features.py` (namespace-as-declaration,
`privilege_cost` raising on unknown names), `governor/env.py` (`suite.make(seed=)`,
FrozenPolicy), `governor/search.py` (EOD scan + Trigger), `governor/parallel.py`,
`tests/test_determinism.py` (4 green).

New tonight, ~600 lines total, in dependency order:
  governor/recovery.py    RecoveryPolicy seam + the one real recovery   (~120)
  governor/governed.py    governed rollout: policy + trigger + recovery (~150)
  governor/gate.py        McNemar, Clopper-Pearson, G1/G2/G3/G-ABL      (~140)
  governor/ledger.py      two JSONL files + reconstruction invariant    (~90)
  governor/campaign.py    5-phase loop + CLI `governor run`             (~120)
  tests/test_invariant.py the test that deliberately violates and fires (~40)

──────────────────────────────────────────────────────────────
1. LOOP STATE MACHINE — 5 phases, not 15
──────────────────────────────────────────────────────────────

```python
class Phase(StrEnum):
    INIT     = "init"       # preregistration, seed partition, canary
    ROLLOUT  = "rollout"    # dev block with the current bundle
    PROPOSE  = "propose"    # EOD scan + trigger search + recovery pick
    GATE     = "gate"       # G1, G2, G-ABL, G3-report, in that order
    DONE     = "done"       # promoted | rejected | stalled | aborted

ALLOWED = {INIT:{ROLLOUT,DONE}, ROLLOUT:{PROPOSE,DONE}, PROPOSE:{GATE,ROLLOUT,DONE},
           GATE:{ROLLOUT,DONE}, DONE:frozenset()}
```
`transition()` refuses any edge not in `ALLOWED` and appends one row to
`transitions.jsonl` with `{from,to,cause,ts}`. `cause` is a structured enum
(`operator|budget|canary_failed|infra_ceiling|exhausted`). `state.json` is the only
file written with overwrite=True. Cold-start rule kept verbatim from the original:
`step()` reads phase from disk every tick, never from memory.

Phase-entry precondition — **determinism canary, corrected**: re-run 3 preregistered
seeds *with the current bundle* (governed, not parent-only) and compare
`actions_sha256` to the frozen ledger value. Parent-only canaries cannot detect
cross-episode critic state leaking through a reused worker, which is the bug that
would silently corrupt pairing. Mismatch -> DONE{cause=canary_failed}.

Measured budget at **80 ep/min governed, 10 workers** (not 212):
```
ROLLOUT   60 dev episodes                                    ~45 s
G1        ~30 candidate arms on failing dev seeds, parent adopted  ~23 s
G-ABL     ~30 zero-privilege-recovery arms                    ~23 s
G2        60 candidate arms on all dev seeds, parent adopted  ~45 s
G3        40 held-out candidate arms, parent adopted          ~30 s
                                              ~2.8 min / generation
```

──────────────────────────────────────────────────────────────
2. CRITIC + RECOVERY SEAMS — one executor, recovery is first-class
──────────────────────────────────────────────────────────────

```python
class CriticRuntime(abc.ABC):
    provider_id: str
    scoring_safe: bool          # refused by the ledger when False
    @abc.abstractmethod
    def open(self, bundle, *, episode_seed, feature_names) -> CriticSession: ...

class CriticSession(abc.ABC):
    @abc.abstractmethod
    def evaluate(self, values: Mapping[str,float], *, step: int) -> CriticOutcome: ...
    @abc.abstractmethod
    def close(self) -> CriticStats: ...

CriticOutcome = Fire(rule_id, step) | Quiet() | CriticFailure(kind, msg, step)
CriticFailureKind = Literal["cpu_budget","exception","contract_violation",
                            "worker_exit","output_invalid"]
```
Error is a RETURNED field, never a raise. `evaluate()` raises only for harness
misuse (closed session, undeclared key).

**Provider `TriggerCriticRuntime` (tonight's default, `scoring_safe=True`).**
Evaluates the existing `search.Trigger` in-process. It is data, not code: no
address-space threat exists to isolate, and `features.py` already makes undeclared
access impossible (`extract` projects only declared names; `privilege_cost` raises
`KeyError` on anything not in `REGISTRY`).

**Provider `SubprocessCriticRuntime` (Night 2, `scoring_safe=True` for code bundles;
until it exists, any code-carrying bundle is `scoring_safe=False` and refused).**
Everything measured above applies:
```
profile = ('(version 1)(allow default)(deny network*)(deny file-write*)'
           '(allow file-write* (literal "/dev/null") (subpath "<realpath run_dir>"))')
```
`(deny network*)` is REQUIRED — verified that without it the confined child reaches
1.1.1.1:80. Invoke as an absolute script path, not `-m`: `python -S` gives 4 sys.path
entries and cannot import a package.

**Budget — CPU time in the child, never wall-clock in the parent.**
```
child:  resource.setrlimit(RLIMIT_CPU, (1,1)); per-eval time.process_time() delta
        soft 2 ms CPU  -> CriticFailure("cpu_budget") -> candidate REJECTED (not retried)
parent: 2 s wall-clock liveness backstop only -> worker_exit -> infra_invalid, retryable
```
Measured justification (10 rollout workers + 10 sandboxed children, 3000 real ticks):
```
p50 49 us | p90 6.1 ms | p99 108-169 ms | max 358-473 ms
over 500 us: 14.7% and 16.9%      over 5 ms: 10.4%
throughput with sandboxed IPC 82 ep/min vs 80 ep/min without  -> IPC costs nothing
batched (10 ticks/msg): still 34.3% over 500 us -> batching is NOT the fallback
```
A parent-side 500 us deadline would mark ~100% of episodes `infra_invalid` and void
every gate at the 5% ceiling. CPU time measures the actual defect and is immune to
scheduling.

**RecoveryPolicy — the seam the original omits.**
```python
@dataclass(frozen=True, slots=True)
class RecoverySpec:
    recovery_id: str
    declared_features: tuple[str, ...]      # priced by features.privilege_cost
    percept_noise: float                    # THE ablation knob
    params: Mapping[str, float]

class RecoveryPolicy(abc.ABC):
    spec: RecoverySpec
    @abc.abstractmethod
    def actions(self, obs, *, step: int) -> Iterator[np.ndarray]: ...
```
`RegraspRecovery` (the one round 1 validated): open -> re-read cube pose **through a
percept of declared noise `sd`** -> re-approach -> re-close -> re-lift.
`sd = 0.0` is the privileged arm; `sd = spec_policy.percept_noise` (0.020) is the
zero-privilege arm. Privilege cost of a bundle = `privilege_cost(critic.features) +
privilege_cost(recovery.declared_features)` — **both terms, per headline-finding.md.**

──────────────────────────────────────────────────────────────
3. PROPOSER SEAM — unchanged in shape, one template
──────────────────────────────────────────────────────────────

```python
class Proposer(Protocol):
    provider_id: str
    requires_network: bool
    def propose(self, brief: ProposalBrief) -> CandidateDraft | Exhausted: ...

@dataclass(frozen=True, slots=True)
class ProposalBrief:
    generation: int
    parent_bundle_sha256: str
    fit_traces: tuple[TraceRef, ...]        # FIT seeds only; gate seeds structurally absent
    fit_labels: tuple[bool, ...]
    privilege_budget: int                   # governor.features units
    recovery_catalog: tuple[RecoverySpec, ...]
    rejection_history: tuple[RejectedDraft, ...]
    brief_sha256: str

@dataclass(frozen=True, slots=True)
class CandidateDraft:
    trigger: Trigger                        # governor.search.Trigger, reused as-is
    recovery: RecoverySpec
    provider_id: str
    draft_sha256: str
```
**`SearchProposer@v1` (default, `requires_network=False`)** wraps the existing
`search.search_triggers` under the privilege budget, then picks the recovery from a
static `failure_class -> RecoverySpec` table plus a 3-point `open_width` grid.
Feasibility is the shadow-replay rule verbatim (`recall == 1.0 and fpr == 0.0`);
among feasible, maximize median lead. Novelty is a mechanical filter: reject any draft
whose `(feature, op)` matches a rejected one unless `theta` is outside every rejected
`theta` +/- one grid step. Empty frontier -> `Exhausted`, which drives PROPOSE->DONE.
**`LlmProposer` (`requires_network=True`)** is the same contract, Night 3+.

Fit/gate split inside the target cluster, kept from the original:
```python
fit  = {s for s in cluster_seeds
        if int(sha256(f"{target_sha256}:{s}".encode()).hexdigest()[:2],16) < 0x80}
gate = cluster_seeds - fit          # eligibility: len(gate) >= 6
```

──────────────────────────────────────────────────────────────
4. GATES — exact statistics, with the ablation promoted to a gate
──────────────────────────────────────────────────────────────

Seed partition, preregistered, disjointness asserted by construction:
```
dev     = 1..60        rollout, search, G1, G2, G-ABL
heldout = 1001..1040   G3, report-only at gen<=3, mode=validation beyond
```

```python
def one_sided_exact_mcnemar(b: int, c: int) -> float:
    d = b + c
    return 1.0 if d == 0 else sum(math.comb(d,i) for i in range(b,d+1)) / 2**d

def clopper_pearson_lower(x: int, n: int, alpha: float) -> float:
    return 0.0 if x == 0 else scipy.stats.beta.ppf(alpha, x, n-x+1)
```

**G1 SAME-SEED — deterministic decision rule, no p-value.**
```
pairs   : gate seeds of the target cluster, N >= 6
parent  : ADOPTED from the frozen dev ledger, never re-run
pairing : seed == , initial_state_sha256 == , env_build_sha256 == , both via suite.make(seed=)
          violation -> INCONCLUSIVE (not FAIL)
attest  : for every rescued seed, actions_sha256(cand) != actions_sha256(parent)
          AND >=1 logged critic fire AND >=1 logged recovery activation
rescued = {s: cand[s].success and not parent[s].success}
passed  = len(rescued) >= ceil(0.5*N) and all(attest) and infra_rate <= 0.05
report  : rescue_rate, clopper_pearson_lower(len(rescued), N, 0.05)
p_value = None; conclusive = True
```
`broken` is REPORTED but is NOT a pass condition: gate seeds are parent failures, so
breakage is 0 by construction and a check that cannot fail is not a check. Breakage
is G2's job. No p-value because these seeds are *selected* (conditioned on failing)
and their sibling fit seeds trained the threshold — not because determinism removes
inference, which would equally void G3.

**G2 REGRESSION — breakage budget over all 60 dev seeds.**
```
parent adopted; only the 60 candidate arms execute
b = #(cand ok, parent fail); c = #(cand fail, parent ok)
passed = c <= regression_max_breaks (DEFAULT 0) and b >= 1 and infra_rate <= 0.05
p_value = None
```
Never `candidate_successes >= parent_successes`: a net rule passes fix-4/break-4.

**G-ABL ZERO-PRIVILEGE ABLATION — new, and a PROMOTION CONDITION.**
```
same gate seeds, same critic, recovery re-run with declared_features restricted to
observable.* and percept_noise = policy percept_noise (0.020)
report : rescue_rate_privileged, rescue_rate_zero_privilege, delta,
         one_sided_exact_mcnemar on the zero-privilege arm vs adopted parent
passed = (bundle privilege_cost == 0)  OR  (zero_privilege arm is preregistered
          as a REPORTED-ONLY diagnostic AND both numbers appear in report.md)
```
Promotion writes both numbers or it does not happen. This is the mechanism that
would have caught round 1's `target = obs["cube_pos"]` automatically, and it is the
difference between a +50.0pt headline and the true +13.3pt (p=0.057).

**G3 HELD-OUT — the only inferential gate.**
```
40 preregistered seeds, disjoint from dev; parent ADOPTED (frozen + deterministic,
and the governed canary covers drift); single fixed look, no interim analysis
p = one_sided_exact_mcnemar(b, c); alpha = 0.025 one-sided
significant = p < alpha and gain >= 3 and cand_safety <= parent_safety
authority: generation <= 3 -> report-only;  generation > 3 -> REJECTING
```
Evidence arithmetic (strict `<`, alpha=0.025): (6,0)->0.015625 PASS, (5,0)->0.03125
FAIL, (9,1)->0.01953 PASS, (8,1)->0.03516 FAIL. The 6-0 minimum is what fixes
`same_seed_min_pairs = 6`. A two-stage design, if ever preregistered, MUST spend
alpha (O'Brien-Fleming one-sided, alpha1=0.005, alpha2=0.023) — never the same alpha
at both looks.

**Infra taxonomy — split, not uniform retry.**
```python
EpisodeOutcome = Scored(success: bool) | InfraInvalid(kind)
InfraKind = Literal["env_construction_failed","sim_instability","worker_exit",   # retryable
                    "critic_cpu_budget","critic_contract_violation",             # NOT retryable
                    "determinism_canary_failed"]                                 # ABORT
```
`InfraInvalid` rows go to `attempts.jsonl` only, never `episodes.jsonl`.
`max_infrastructure_attempts = 2`. Per-arm rate > 0.05 -> `INCONCLUSIVE_INFRA`.

──────────────────────────────────────────────────────────────
5. FAILURE GROUPING — hard partition only, ~25 lines
──────────────────────────────────────────────────────────────
```
bucket key = (failure_class, phase)      # different mechanisms NEVER merge
failure_class, priority-ordered, first match wins, on success is False only:
  p0 safety_violation      out-of-bounds / joint-limit event            sev 1.00
  p1 closed_empty_gripper  observable.finger_gap < 0.005 at close end   sev 0.95
  p2 lift_stall            grasped but observable.eef_z fails to rise   sev 0.85
  p3 horizon_incomplete    ONLY when nothing else fired                 sev 0.60
earliest_divergence_step: int | None   # None means "not localized"; NEVER coerced to 0
rank by (-unique_episode_count, -mean_severity, bucket_key)
eligibility: len(gate_seeds) >= 6
ranking_authority = "harness_unique_failure_episode_count"   # proposer cannot steer
```
No similarity metric, no linkage, no silhouette. One task, one dominant mechanism at
40x separation: the partition returns one bucket and the metric is never consulted.
Rebuild it with the original's 34-dim design at the second task family, triggered by
a within-bucket dispersion report rather than by memory.

──────────────────────────────────────────────────────────────
6. ARTIFACT LAYOUT — two ledgers, no CAS
──────────────────────────────────────────────────────────────
```
runs/<campaign_id>/
  preregistration.json     seed partition + sha, proposer provider_id/requires_network,
                           critic runtime provider_id, git sha, env_build_sha256
                           (mujoco 3.3.7 / robosuite 1.5.2 / env kwargs), alpha,
                           min_gain, privilege_budget   -- overwrite=False, ONCE
  state.json               ONLY overwrite=True: {phase, generation, bundle_sha256, cause?}
  episodes.jsonl           append-only, SCORED only
  attempts.jsonl           append-only, EVERY dispatch incl. infra_invalid
  transitions.jsonl        append-only, one row per phase edge
  g0000/
    bundle.json            trigger + recovery spec + privilege cost (critic AND recovery)
    canary.json            3 GOVERNED episodes, actions_sha256 comparison
    traces/<seed>.npz      per-step declared feature values + feature_vector_sha256
    draft.json             proposer output verbatim, draft_sha256
    precommit.json         overwrite=False BEFORE any gate episode
    gates/{same_seed,regression,ablation,heldout}.json
  report.md                generated, never hand-edited
```
Every `EpisodeRecord` carries `seed`, `bundle_sha256`, `initial_state_sha256`,
`actions_sha256`, `feature_vector_sha256`, `critic_runtime_provider_id`,
`recovery_privilege_cost`. `record_episode` REJECTS a record whose `bundle_sha256`
differs from `state.current_bundle_sha256`, whose seed is not preregistered, or whose
provider has `scoring_safe=False`.

**The reconstruction invariant (GOAL.md #4), and the test that fires.** At every step
the governed rollout writes the exact projected feature dict the critic received plus
`feature_vector_sha256`. `verify_reconstruction(episode)` re-projects from
`traces/<seed>.npz` and asserts every hash. `tests/test_invariant.py` deliberately
writes one trace row that differs from what was dispatched and asserts
`ReconstructionViolation` is raised — the invariant is proven to fire, not assumed to.

──────────────────────────────────────────────────────────────
7. TONIGHT'S ACCEPTANCE — observable, no mocks
──────────────────────────────────────────────────────────────
```
$ governor run --seeds 60 --heldout 40 --privilege-budget 0
```
must produce, in one unattended pass of ~3 min:
1. a real campaign directory with a promoted-or-rejected generation 0   (GOAL #1)
2. G1 rescue rate + Clopper-Pearson bound and a G3 exact-McNemar p on 40
   unseen seeds                                                        (GOAL #2)
3. gates/ablation.json with BOTH the privileged and zero-privilege numbers for the
   promoted rule, and a privilege cost that sums critic AND recovery  (GOAL #3)
4. `pytest tests/` green including test_invariant.py's deliberate violation (GOAL #4,#5)
5. a report.md whose numbers reproduce docs/headline-finding.md's hand-run experiment
   — the search finding the pair automatically, and the ablation collapsing the
   headline from +50.0pt to +13.3pt without a human noticing it needed to look.

Night 2 = SubprocessCriticRuntime + arbitrary code (with `(deny network*)` and the
CPU-time budget). Night 3 = LlmProposer behind the same contract. Night 4 = the
clustering metric, when a second task family makes it consult anything.
