# Architecture

English | [简体中文](#简体中文)

In one sentence: **a system built so that the claim "the robot's skill improved"
cannot lie** — frozen policies execute tasks, hash-chained logs seal the evidence,
and a preregistered statistical pipeline evolves the skills. The kernel is tiny;
robots, simulators, and tasks are all pluggable cards.

## 0. Three design laws

The whole architecture follows from three laws:

1. **Execution and evolution are separated (two modes).** During real work the
   policy is frozen — not a byte changes. Only evolution mode may produce new
   skills, and a new skill must pass statistical tests before it is installed.
2. **Evidence precedes conclusions.** Every episode is sealed into a hash-chained
   log — change one byte and the chain breaks. Experiment designs (seeds,
   hypotheses, thresholds) are preregistered and sealed *before* anything runs;
   there is no revising after the fact.
3. **Minimal kernel, everything else is a card.** The kernel knows no robot, no
   simulator, no task — all of them plug in through declarative manifests.

## 1. Repository layout

```
physical-harness/
├── harness/        kernel: episode loop, hash-chained session log, privilege budget
├── plugins/        cards: everything "concrete"
│   ├── embodiment_robosuite/   tabletop-arm card (env + perception + drivers + predicates)
│   ├── embodiment_robocasa/    kitchen-robot card (same shape, different deps)
│   ├── mission_*/              mission cards (task graph + planner, pure-data manifest)
│   ├── task/                   generic task machinery: graph execution, verify, replan
│   └── rsi/                    skill-evolution engine: gates, paired tests, campaigns
├── board/          the single public face ("one store, three faces")
├── scripts/        resident runtimes + evolution entry points
├── runs/           evidence store (gitignored, machine-local)
└── tests/          600+ tests; the base lane is green with zero simulators installed
```

## 2. Kernel (harness/) — exactly three guarantees

**Episode loop.** An episode is one complete life of a world, from reset to close.
Every task is an instance of this loop.

**Session log (hash chain).** Every event (a mount, a node execution, a verify
result, a campaign outcome) appends one row carrying the hash of the previous row.
Tamper with any row afterwards and `verify()` catches the broken chain. Audit here
is not a process — it is arithmetic.

**Privilege budget.** In simulation you can cheat: read an object's true pose
directly (privileged, god's-eye information). Real robots have no such luxury. The
kernel meters every privileged read, and every installed skill ships with a
privilege ablation curve — gains earned by cheating show up in the ablation.

Configuration integrity: everything that could change an experimental conclusion
(what was mounted, with what parameters) folds into one content hash
(`MountPlan.sha`). Different config = different hash = a different experiment
identity. There is no "quietly tweaked a constant".

## 3. Cards (plugins/) — three kinds

**Embodiment card**: one robot + simulator, complete — env construction,
perception, drivers (e.g. how "navigate to the fridge" maps onto wheels), and
predicates (e.g. "is the object inside the microwave?"). Each embodiment card gets
its own venv (robosuite and robocasa have mutually exclusive numpy ABIs — isolation
is cheaper than reconciliation) and neither knows the other exists. The card also
declares its RSI recovery repair shapes — `[recoveries.<name>] ref = "module:attr"`
in its manifest, folded by `discover()` like mounts/campaigns and isinstance-checked
against the `RecoveryStrategy` Protocol at load. A card declaring none has no
recovery primitives, and the RSI chain says so verbatim instead of borrowing another
embodiment's.

**Mission card**: one task's graph definition — how nodes connect, which driver
each node uses, which predicate verifies it. The manifest.toml is **pure data**, no
logic, so adding a task cannot break the kernel.

**RSI card**: the evolution engine, §6.

The boundary rule between cards is **provider by ref**: a card may not import
another card's code. It names a string reference in its manifest
(`"plugins.embodiment_robocasa:provider"`) that the kernel resolves at mount time,
where a Protocol isinstance check fails fast — a wrong shape errors at mount, never
inside an episode. Standing boundary tests turn red on any sneaky cross-card import.

## 4. The task graph — why a graph, not a script

A script hard-codes "grasp, walk, place" and can only start over on failure. We
split a task into a node graph with four node kinds:

| kind | what it does | example |
|---|---|---|
| perceive | look at the world | "what is in the kitchen?" |
| decide | make a choice | "which can first?" |
| segment | execute a motion (the only node that acts) | "navigate to the fridge" |
| verify | check live world state — **never trusts the segment's self-report** | "did we actually arrive?" |

The core mechanism is **in-episode replan**: when a verify fails, the world is not
reset — the plan is repaired and retried *in the same world*. Like fumbling a cup:
you re-grasp on the spot, you don't go home and restart the day. One episode can
carry dozens of manipulations with local recovery — that is where long-horizon
capability comes from. Long tasks run in one persistent episode
(`episodic: true`): environment, observations, and execution cursor survive across
nodes.

One more law: **rendering is live state, not evidence**. Viewport frames and
screenshots never enter the session-log chain.

## 5. Board and runtimes — how the outside world uses the system

**The board is the only door.** One set of functions, three faces: a Python API
(`board/store.py`), a CLI (`storecli`), and an MCP server (for AI agents). All
three change together — miss one and a test catches it. Reads are unrestricted;
writes have exactly one entry: `submit_brief`.

**A brief is a pure selector plus budgets** — no implementation inside:

```json
{"kind":"task",     "task":"kitchen_thaw", "seed":11, "max_replans":3}
{"kind":"campaign", "campaign":"stack", "dev":[41000,41999], "heldout":[42000,42199]}
{"kind":"rsi",      "task":"kitchen_thaw"}
```

Which code runs is decided server-side from the manifests; any extra key in a brief
is rejected. The execution entry point is not injectable.

**A runtime is a resident process** with almost embarrassingly simple logic: watch
one inbox directory, claim files by atomic rename (which makes double-claiming
impossible), move finished work to done/ or failed/. No message queue, no database
— filesystem atomicity is enough. One runtime, one venv, one session directory per
simulator.

**The hard boundary between the two modes**: mode is a per-session property,
default EXECUTION (fail-safe: real work can never trigger evolution). At boot, the
mode + skill manifest + mount hash are sealed as row zero of the chain. An
execution-mode runtime rejects campaign/rsi briefs outright, and before every task
re-folds the skill directory digest against the boot manifest — files smuggled in
out-of-band are caught. Only evolution mode writes to the skill store; execution
mode mounts only established (held-out-passed) frozen SkillRecords.

## 6. RSI — how skills improve scientifically

The unit of evolution is a **rule**: "when observable feature X crosses threshold
T, run recovery action R." Example: `finger_gap < 0.0096 → regrasp` (fingers
closed too far means nothing was caught — grasp again).

One `{"kind":"rsi","task":X}` triggers a seven-step chain; each step blocks one way
of fooling yourself:

| step | what | blocks |
|---|---|---|
| 1 calibration | 150 baseline episodes, per-node first-death attribution | picking bottlenecks by gut feeling |
| 2 mechanical gates | six hard criteria, scored in code; fail = stop | loosening standards because you want to proceed |
| 3 prereg | seeds/hypotheses/thresholds sealed (content hash) before running | changing the story afterwards |
| 4 dev campaign | search candidate rules; paired same-seed McNemar against the parent (count fixed vs broken) | mistaking luck for improvement |
| 5 blind twin | same recovery action fired unconditionally; the rule must beat it | credit for "knowing when" that has no value |
| 6 held-out | fresh seeds, scored exactly once, then burned forever | retaking the exam until it passes |
| 7 fold-in | the established rule becomes a frozen SkillRecord in the runtime | — |

Three things no agent is allowed to touch: the target node is chosen by
first-death attribution (not cherry-picked for a flattering result); thresholds
come from the search algorithm; and if an embodiment has no registered recovery
primitive, the honest answer is "nothing to work with" — no inventing an action on
the spot.

**Honest nulls and honest NO-GOs are valid deliverables.** When the gates say the
failures are capability gaps rather than governable mistakes, the correct move is
to build capability — not to bend a threshold until something promotes. Null
results in the ledger are worth as much as wins: they chart where the capability
boundary actually is.

**Seeds are exam questions**: simulation is deterministic, same seed = same world.
A burned seed block is retired forever (a one-shot ledger, enforced at the
scheduling boundary). Calibration blocks are the exception: calibration never
gates, so it may be re-measured freely.

## 7. Evidence (runs/)

Per-session sealed logs, SkillRecords, and campaign artifacts — all content-hash
named (filename = hash of content; edit the content and the name no longer
matches, the chain breaks). Not in git: it is this machine's lab notebook. The
repository ships the machine that *produces* evidence, not the evidence itself.
Outsiders read evidence through the board, never by picking through files.

## 8. UI (ph-station, separate repository)

One red line: **zero business logic in the frontend**. The browser panels
(execution graph, process flow, trajectory, sim viewport, skill-vault lineage,
evolution progress) are pure views; every piece of state crosses a single HTTP
surface (`POST /api/board/<fn>`) from the board. The frontend computes nothing —
so it cannot compute wrongly, and every number on screen traces back to the hash
chain.

## 9. Invariants (verified by standing tests)

- Seen means recorded: capability resolution, mounts, privileged reads all land in
  the chained log; tampering is caught by `verify()`.
- Every hashed config field must appear in the canonical serialization (tested).
- Boots with zero plugins: with `plugins/` empty the base lane is green — the
  kernel is genuinely sim-agnostic.
- Judgement-type claims must pass blind twin + held-out; headline numbers span at
  least three seed blocks.
- Execution mode never writes the skill store; real-robot cards
  (`actuation:real`) are refused by sim runtimes.

```
one sentence from you → brief → inbox → runtime mounts cards, runs the graph
                                   ↓
                     verify at every step; replan in place on failure
                                   ↓
                     everything sealed into the hash chain (runs/)
                                   ↓
      the UI only reads the board; RSI only grows skills out of evidence
```

**Execution never writes. Evolution never lies. The frontend never computes.
Evidence never changes.**

---

# 简体中文

一句话定位：**一个让"机器人技能变好了"这句话没法骗人的系统**——冻结策略执行任务、
哈希链封存证据、预注册的统计流程演化技能。内核极小，机器人/仿真器/任务全是插拔卡片。

## 0. 三条设计铁律

整个架构由三条铁律推导而来：

1. **执行与演化分离（execution / evolution 两态）**。干活时策略是冻结的，一个字节不改；
   只有演化态才能产生新技能，且新技能必须通过统计检验才能上岗。
2. **证据先于结论**。每集实验封存成 hash chain 日志，改一字节链就断；实验设计（种子、
   假设、门槛）在跑之前预注册封存，跑完不许反悔。
3. **内核极小，其余皆卡**。内核不认识任何机器人、仿真器、任务——这些全部通过声明式
   manifest 插进来。

## 1. 目录结构

```
physical-harness/
├── harness/        内核：episode 循环、session log 哈希链、特权预算
├── plugins/        卡片：所有"具体"的东西
│   ├── embodiment_robosuite/   桌面机械臂卡（环境 + 感知 + 驱动 + 谓词）
│   ├── embodiment_robocasa/    厨房机器人卡（同上，另一套依赖）
│   ├── mission_*/              任务卡（任务图 + planner，纯数据 manifest）
│   ├── task/                   通用任务机器：图执行、验证、replan
│   └── rsi/                    技能演化引擎：门禁、配对检验、campaign
├── board/          对外唯一门面（"一个店三张脸"）
├── scripts/        常驻 runtime + 演化入口
├── runs/           证据库（gitignored，只在本机）
└── tests/          600+ 测试；base lane 零仿真依赖即可全绿
```

## 2. Kernel（harness/）——内核只提供三个保证

**Episode loop**。一个 episode = 世界从 reset 到关闭的一次完整经历。所有任务都是
这个循环的实例。

**Session log（hash chain）**。每发生一件事（挂载、执行、验证、campaign 结果）追加
一行，每行携带前一行的 hash。事后改任何一行，`verify()` 立刻抓到断链——这是"证据"
的技术基础。审计不是流程，是数学。

**Privilege budget（特权预算）**。仿真里可以作弊（直接读物体真实坐标，privileged /
上帝视角），真机上没有。内核对每次特权读取记账；每条上岗技能附带特权消融曲线
（ablation curve），靠作弊涨的分在消融里现形。

配置完整性：所有能改变实验结论的东西（挂载了什么、用什么参数）折成一个内容哈希
（`MountPlan.sha`）。配置变 = 哈希变 = 另一个实验身份，不存在"悄悄改了个参数"。

## 3. Cards（plugins/）——三类卡片

**Embodiment card（本体卡）**：一个机器人 + 仿真器的全套——环境构造、感知、驱动
（driver，如"导航到冰箱"怎么用轮子实现）、判定谓词（predicate，如"物体在微波炉
里吗"）。每张本体卡配独立 venv（robosuite 与 robocasa 的 numpy ABI 互斥，隔离比
调和便宜），互相不知道对方存在。RSI 恢复原语也由本体卡在自己的 manifest 里声明
（`[recoveries.<name>] ref = "module:attr"`，由 `discover()` 像 mounts/campaigns
一样折叠，加载时按 `RecoveryStrategy` Protocol 做 isinstance 校验）；没声明的卡
就是没有恢复原语，RSI 链原样报告，不借别的本体的。

**Mission card（任务卡）**：一个任务的图定义——节点怎么连、每个节点用哪个驱动、用
哪个谓词验证。manifest.toml 是**纯数据**，没有逻辑，所以加任务不可能弄坏内核。

**RSI card（演化引擎）**：见 §6。

卡片间的边界规矩是 **provider by ref**：卡不许 import 另一张卡的代码，只能在
manifest 里写字符串引用（`"plugins.embodiment_robocasa:provider"`），由内核在挂载
时解析。挂载点做 Protocol isinstance 校验——形状不对当场报错，不流进 episode。
边界测试常驻，偷 import 隔壁卡直接红。

## 4. 任务图——为什么是图不是脚本

脚本写死"抓、走、放"，失败只能整个重来。我们把任务拆成节点图，节点四种 kind：

| kind | 干什么 | 例子 |
|---|---|---|
| perceive | 看一眼世界 | "厨房里有什么" |
| decide | 做决定 | "先拿哪个罐子" |
| segment | 执行一段动作（唯一动真格的节点） | "导航到冰箱" |
| verify | 读世界活状态核实，**不信 segment 的自我报告** | "真的到了吗" |

核心机制 **in-episode replan**：verify 失败时不重置世界，在同一个世界里重新规划
重试——就像抓杯子滑了一下是原地再抓，不是回家重新出门。一集里可以操作几十次、
错了就地补救，这就是长程任务能力的来源。长任务跑在一个持久 episode 里
（`episodic: true`），环境、观测、执行游标跨节点存活。

另一条铁律：**渲染是活状态不是证据**。取景窗帧、截图永远不进 session log 链。

## 5. Board 与 runtime——外界怎么用这个系统

**Board 是唯一门面**，同一套函数暴露三张脸：Python API（`board/store.py`）、
CLI（`storecli`）、MCP server（给 AI agent）。三脸必须同步改，漏一处测试抓。
读随便读；写只有一个口：`submit_brief`。

**brief 是纯选择器 + 预算**，不含实现：

```json
{"kind":"task",     "task":"kitchen_thaw", "seed":11, "max_replans":3}
{"kind":"campaign", "campaign":"stack", "dev":[41000,41999], "heldout":[42000,42199]}
{"kind":"rsi",      "task":"kitchen_thaw"}
```

用什么代码由服务端按 manifest 决定，brief 里多塞任何键都被拒——执行入口不可注入。

**Runtime 是常驻进程**，逻辑极简：盯一个 inbox 文件夹，有文件就认领（原子 rename，
天然防抢单），干完移到 done/ 或 failed/。没有消息队列、没有数据库，文件系统的原子
性够用。每个仿真器一个 runtime、一个 venv、一个 session 目录。

**两态的硬边界**：mode 是每 session 属性，默认 EXECUTION（fail-safe：真任务永不触发
演化）。boot 时把 mode + 技能清单 + 挂载哈希封成链的第 0 行；执行态收到 campaign/rsi
brief 直接拒；每次执行前重折技能目录摘要与 boot 清单比对——带外塞文件也抓得住。
只有演化态写技能库；执行态只挂已确立（held-out 通过）的冻结 SkillRecord。

## 6. RSI——技能怎么科学地变好

演化的对象是**规则（rule）**："观测特征 X 越过阈值 T 时，执行恢复动作 R"。
例：`finger_gap < 0.0096 → regrasp`（手指闭合后间隙过小说明没夹住，重抓）。

一句 `{"kind":"rsi","task":X}` 触发七步链，每步防一种自欺：

| 步 | 干什么 | 防什么 |
|---|---|---|
| 1 calibration | 跑 150 集 baseline，统计逐节点首死分布 | 凭感觉判断瓶颈 |
| 2 mechanical gates | 六条硬标准逐条打分，不过就停 | 想开工就放宽标准 |
| 3 prereg | 种子/假设/门槛先封存（content hash）再跑 | 跑完改口 |
| 4 dev campaign | 搜候选规则，配对同种子 McNemar（对父版本数"修好/弄坏"） | 把运气当提升 |
| 5 blind twin | 同样的恢复动作但无条件每次做，新规则必须打赢它 | "判断时机"没价值却邀功 |
| 6 held-out | 全新种子只考一次，考完作废 | 反复刷题刷到及格 |
| 7 fold-in | 确立的规则折进运行时技能库 | —— |

三个不许 agent 插手的点：治理节点由首死归因选（不是 agent 挑好看的）；阈值由搜索
算法定；本体没注册恢复原语就诚实报"无从下手"，不现编动作。

**诚实 null / 诚实 NO-GO 是合格产出**。门禁说"失败是能力缺口不是可治理失误"时，
正确动作是去补能力，不是调阈值凑一个晋级。账本里的 null 结论和成功一样值钱——
它们标出了能力边界。

**Seed 即考题**：仿真确定性，同 seed 同世界。考过的种子块永久作废（一次性账本），
runtime 在调度边界强制查重。标定块例外：标定永不设门，永远可复测。

## 7. Evidence（runs/）

每个 session 的封存日志、SkillRecord、campaign 产物，全部 content-hash 命名
（文件名 = 内容 hash，改内容 = 换名 = 断链）。不进 git：它是这台机器的实验记录，
代码库交付的是"能产生证据的机器"，不是证据本身。外界读证据一律走 board，
不直接翻文件。

## 8. UI（ph-station，独立仓库）

红线一条：**前端零业务逻辑**。浏览器里的面板（执行图谱、过程流、轨迹、仿真取景窗、
技能谱系、演进进度）全是纯视图，所有状态经唯一一条 HTTP 面
（`POST /api/board/<fn>`）从 board 读。前端不算任何数——所以它不可能算错，
所有数字都能追溯到 hash 链。

## 9. 不变量（可验，测试常驻）

- 可见即已记录：能力解析、挂载、特权使用全部落链式日志，篡改被 `verify()` 抓住。
- 进哈希的配置字段必须完整出现在 canonical 序列化里（有常驻测试）。
- 零插件可启动：`plugins/` 为空时 base lane 全绿——内核真正 sim-agnostic。
- 判断类主张必须过 blind twin + held-out；头条数字至少跨三个种子块。
- 执行态永不写技能库；真机卡（`actuation:real`）被 sim runtime 拒挂。

```
你的一句话 → brief → inbox → runtime 挂卡执行任务图
                                  ↓
                        每步 verify，失败就地 replan
                                  ↓
                        全程封进 hash chain（runs/）
                                  ↓
            UI 只读 board 画出来；RSI 只从证据里长出新技能
```

**执行不写、演化不骗、前端不算、证据不改。**
