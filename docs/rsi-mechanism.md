# 通用 RSI 机制

一句话：`{"kind":"rsi","task":"<任务名>"}` 投进进化态 runtime，整条纪律链自己走完。
不再为每个任务手写一个 campaign 脚本。

实现：`scripts/rsi_campaign.py`（链本体）+ `scripts/harness_runtime.py`（brief 面）。
通用路径里**没有任何任务名 if 分支**——任务是参数，加任务仍然是装一张卡。

## brief 形状

```
{"kind":"rsi","task":"kitchen_thaw"}
```

最小形态就这一行。可选键**只用来覆盖本来由测量决定的东西**：

| 键 | 默认 | 覆盖了什么 |
|---|---|---|
| `node` | 由归因选 | 目标治理节点（覆盖会记进裁决书） |
| `cal` / `dev` / `heldout` | 自动领 | `[lo,hi]`，钉住某个块而不是领新的 |
| `workers` | 10 | 池子宽度 |
| `floor` | 0 | 领块的起始下限 |

其他键一律被 `_BRIEF_KEYS` 拒掉（和 task/campaign 同一道闸）。
`kind:"rsi"` 和 `kind:"campaign"` 一样**只在进化态被接受**。

## 链上七步

| 步 | 做什么 | 在哪 |
|---|---|---|
| a. 领种子 | 从 STATUS.md 账本前沿领**一整块 650**，切成标定 150 / dev 300 / held-out 200。领完的 dev∪heldout 喂回 `_declared_ranges` 那道原闸；标定块不过闸（标定永不设门、永远可复测），可用 `cal` 钉住旧块复测 | `rsi_campaign.allocate` + `harness_runtime._rsi_blocks` / `_assert_unburned` |
| b. 标定 | **通用探针**：把 `{"kind":"task"}` 那条路在池子里跑 N 次，skills root 指向空目录 → 臂天然是 baseline。产出链基率、**逐节点 × 机制**首死、每集耗时。任务的节点图/kind/after 边由 planner 现问，不是硬编码表 | `rsi_campaign.calibrate` / `_probe_one`，brief 装配复用 `harness_runtime.task_brief`（和活跑逐字节同一张 brief） |
| c. 门禁 | M7 §3 / M6 §4 写成六条机械判据，逐条打分。**没过就停在这里**，裁决书写清缺哪条能力 + 触发它的那个数，一粒 dev 种子不烧 | `rsi_campaign.gate` |
| d. prereg | content-hash 封存，**在任何 dev 种子跑之前** | `rsi_campaign.build_prereg` + `plugins.rsi.workload.run` 盖 provider 三元组 |
| e. dev campaign | 调既有 `run_campaign`，FROM-SCRATCH（`parent_store=None`）。门 = 配对同种子 McNemar（对父）+ blind twin + `min_fixed`，功效缩放取前缀 | `plugins/rsi/campaign.py`（未改） |
| f. held-out | 仅当有晋级，**只评一次** | 同上（`run_campaign` 既有行为） |
| g. 折入 | 发布记录复制进该 session 的 skills root；两态铁律照旧（执行态 skills-root 变更触审计 → 归档旧 log + 全新 boot 封 row0） | `harness_runtime._run_rsi` → `_copy_skills`（未改） |
| h. 账本 | 生成一段 STATUS.md 形状的条目**打印给操作员**，并进 `runtime.rsi_scheduled` 链行。**从不自动 append**——账本是人写的，第二个写手就是它要防的那种污染 | `rsi_campaign.ledger_entry` |

### 六条门禁判据

| id | 触发条件 | 结论 |
|---|---|---|
| `c1_base_degenerate` | 基率 0% 或 100% | 无残余可学，停 |
| `c2_base_ceiling` | 基率 ≥ 0.90 | 诚实 null，不烧 dev/held-out |
| `c3_budget_exhaust_dominant` | 多数失败死于 `max_actuations`/horizon 耗尽 | 调配置，不是 RSI |
| `c4_attribution` | 未治理节点死数 ≥ 可治理节点死数 | 归因 pivot，先要那个节点的能力 |
| `c5_recovery_primitive` | 目标节点所属本体没注册恢复原语 | RSI 无从下手 |
| `c6_wall_clock` | 估计标定 + 一代 dev > 2h | 今夜只标定 |

## 诚实边界

**1. 恢复原语不存在就明说。**
恢复原语由**本体卡**在自己的 manifest.toml 里声明（`[recoveries.<name>]
ref = "module:attr"`，如 `plugins/embodiment_robosuite/`），由
`harness.manifest.discover()` 像 mounts/campaigns 一样折叠（重名 loud fail）；
`plugins/rsi/repertoire.py` 只读折叠结果，并按
`harness.contracts.RecoveryStrategy` Protocol 做 isinstance 校验。理由：
`RecoveryActor` 把 phase 名翻成动作靠的是 `harness/spec_tabletop.py` 的
`PHASE_HEIGHT` —— tabletop 手臂的 above/descend/close/lift 词汇，换本体即无意义，
所以修复形状写在说这套词汇的那张卡里。
`strategies_for("embodiment_robocasa")` 返回 `[]`（它的卡没声明任何
`[recoveries.*]`，因为确实没有），链就原样报「该本体（卡 X）无注册恢复原语，
RSI 无从下手」，并把 robosuite 侧的 `servo_descend`/`servo_probe` 指出来当**模板**，
**不现编一条**。

> 这条注册是必需的，因为更便宜的那个检查是**错的**：
> `plugins/embodiment_robocasa/kitchen_driver.py` 把 `retarget` / `on_handback`
> 定义成有文档的 **no-op**，所以 `hasattr` 探针会把厨房驱动报成「可治理」，
> 而真触发时那条规则悄无声息地什么都没做。**方法在场 ≠ 原语在场。**
> 驱动协议检查保留为第二道必要条件，不再是唯一条件。

第二条诚实缺口：`segment` 节点跑在 ONE 持久世界里，而 `run_campaign` 每集自建世界，
所以**持久集分段目前没有独立 campaign 路径**。这条和上一条独立汇报（`blockers` 是列表，
不是第一个就返回）——只被告知其中一条的操作员会去修错的东西。

**2. 目标节点不由 agent 挑。** 由 `attribute()` 从首死数据选：verify 节点的死沿
`after` 边回charge 给它验的那个执行节点（它本来就没有自己的治理面），perceive/decide
的死谁也不charge（那是 c4 的 pivot 信号），然后在可治理节点里取 argmax。
`node` 键能覆盖，但覆盖会写进裁决书。

**3. 阈值不由 agent 挑。** `critic_budget=0` 让 `plugins/rsi/stats/search.py` 结构性地
够不到特权特征——「优先非特权」是预算，不是偏好。特权规则只能靠调高预算进来，而
`run_campaign` 在每次晋级都跑转移消融，所以特权收益必定带着它的塌陷曲线一起出现。
恢复形状同理：由目标节点**实测**的主导失败 stage 决定（stage 名落在 place 词汇里 →
place 形修复），且只能在该本体已注册的 repertoire 里取。

**4. 诚实 NO-GO / 诚实 null 是合格产出。** 裁决书带 `proceed` + 逐条判据 + 触发它的
那个数；账本条目把「未烧」写明。链停在门禁时 store 里有裁决、没有 skills，这是**完成
态**，不是失败态。

## 既有脚本的去留

| 脚本 | 结论 |
|---|---|
| `scripts/probe_*.py`（6 个） | **可退役**：b 步的通用探针覆盖它们全部。它们的每任务 `ORDER` / `MECH` 表现在由 planner 的 `kind`/`after` 现推。保留为历史证据的复现入口 |
| `scripts/stack_campaign.py` | **可退役**：`--task stack` 走通用路径等价 |
| `scripts/grasp_cube_campaign.py` | **可退役**：`--task inventory_build` 的归因自动选中 `grasp-cube`（已实测，见下） |
| `scripts/clear_build_campaign.py` | **保留**：它是 `parent_store` 种起的续跑（从 place-g2 的 bundle 接着长），通用路径今天只做 FROM-SCRATCH |
| `scripts/place_campaign.py` | **保留**：同上，且带自己的消融/复现分相 |
| `scripts/acceptance_campaign.py` | **必须保留**：参数化验货，要 `--claim <卡目录>` 读该卡的 `[claim]` 表，不是「给任务名就够」的形状 |

退役 = 停止为新任务写新的同类脚本；已封存的实验脚本不删（它们是复现入口）。

## 已验证

`--task stack` 与 `--task inventory_build` 均在 scratch 种子（43xxxx，<542k、不在任何
STATUS 声明块内）上跑通：

* `inventory_build` 标定 20 席 → 链基率 45.0%，首死 `grasp-cube 7 / build-stack 4 / none 9`
  → 归因自动选中 **`grasp-cube`**，即 round 108 操作员手工挑的那个节点。
* `stack` 全链 a–h：标定 30 席（基率 53.3%）→ 门禁 GO → prereg 封存
  `dbe21b8b4081` → dev gen-1 功效前缀 100 席 → **0 晋级 = 诚实 null** → held-out
  431300-431329 **未烧**。
