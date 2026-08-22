# physical-harness

**Agentic OS 骨架: 一个插件内核, 承载一台证据机器。**

三个来源, 一个自己的贡献:

- **插件内核**来自 dsh(deepseek-harness)的 everything-is-a-plugin 思想 —— 能力是缝, provider 是插件, 换实现 = 改 mount, 不是改代码。
- **RSI workload** 来自 Zetta 的 physical RSI —— 冻结策略不动, 演化的是 critic/recovery(何时介入、怎么恢复、能力边界)。
- **自己的贡献是把特权预算机制化**: 特权特征读取(FeatureView)与特权能力解析(Kernel)全部记账吃预算, 每条技能附带特权消融曲线 —— sim-to-real gap 从一句担忧变成一条可测的曲线。

所有数字来自真实 robosuite/MuJoCo 仿真回合, 无 mock 验证, 无外部 API 调用。

## 30 秒上手

```bash
uv venv && uv pip install -e ".[dev]"
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python -m pytest tests/
```

当前全量: **223 通过, 3 跳过**(跳过 = Mac 归档 campaign 与 BC 权重不在 git, 预期)。

- `mujoco==3.3.7` + `robosuite==1.5.2` 是硬 pin: mujoco>=3.4 把 `qM` 改名 `M`, robosuite 1.5.2 会崩。
- Linux 无头环境要 `MUJOCO_GL=egl`; 无 GPU 训练、无网络、无 API key 需求。

## 可跑入口

每条都是一条命令, 参数见 `--help`:

| 命令 | 干什么 |
|---|---|
| `scripts/demo_campaign.py` | Lift 端到端 RSI campaign(~2500 回合)。数字逐位可复现: 跨机(4090 vs Mac 封存, round 77)与同机重跑(runs/demo-r1..r3)均逐位一致, 差异仅限新增字段 |
| `scripts/stack_campaign.py` | 长程 Stack RSI campaign(round 79 接线), 终态判据 + 阶段机归因 |
| `scripts/task_plan.py --seed 90000` | 规划闭环(round 83): plan → validate → 受治理 rollout → verify → 失败折回重规划, 账本收一条链 |
| `scripts/watch_stack.py --scan` / `--seed N --governed` | GUI 看营救: 真 `governed_rollout` 加一扇渲染窗, 零逻辑复制; `--scan` 无头找"素跑失败、规则救回"的种子 |
| `scripts/calibrate_stack.py --pass A` | Stack 基线离线标定(pass A 扫放置高度 / pass B 扫感知噪声), 标定块永不再当门禁 |
| `scripts/round25_rerun.py` | 三臂对比(round 81): 确定性搜索 vs 天真挑法 vs qwen38 本地端点(端点不在则优雅跳过) |
| `python -m board.report --out out.html` | **RSI 监控报告**(round 95 起): 自发现 `runs/` 全部 store, 生成自包含 HTML——代际时间线/held-out 多块对比/阶段归因/运行时会话链校验/种子账本/rounds 流, cron headless 导出。实时驾驶舱改由 dsh + `board/mcp_server.py`(MCP 七只只读工具 + submit_brief)承载, 手搓 web 仪表盘 round 95 退役。纯 stdlib 生成, 对 store 只读 |

`runs/stack-g1`(round 79 长程 campaign)的收尾输出, 数字直接来自封存 artifacts:

```text
=== published skills ===
  57162e40d2bd  gen1  trigger observable.finger_gap lt 0.001  dev 59.7% -> 65.8% (12 fixed / 0 broken)

held-out (n=200): 58.5% -> 65.0%, 13 fixed / 0 broken, p=0.00024
```

同一份 artifact 里: 对盲发孪生 +35.5pp(盲发基线 29.5%, p=8.4e-16, judgement established); 消融曲线零破坏全档(真值 +18.0 / sd=0.010 +13.5 / 0.020 +6.5 / 0.030 +3.5pp)。**头条现为四区块**(rounds 85/91 复验): **+6.5 / +9.5 / +11.0 / +10.5pp, 75 修 0 破, n=800**, 判定四块全确立(42000/42200/42400 + place-g1 的处女块 47000)。

## 架构

能力缝是 `harness/definitions.py` 的清单, 契约是 `harness/contracts.py` 的 runtime_checkable Protocol —— 挂错形状在 mount 时就报错, 不是 mid-episode:

| 能力 | 契约 | 当前 provider |
|---|---|---|
| `embodiment.env` | `EnvProvider` | robosuite Panda(`plugins/embodiment_robosuite`); Sawyer = 换 `sawyer_bundle` |
| `embodiment.ground_truth` | `GroundTruthState`(特权) | 无默认挂载 —— 特权不进 base profile, 真机部署天然缺席 |
| `policy.driver` | `PolicyFactory` | Lift/Stack 脚本策略 + 行为克隆适配(`plugins/policies`) |
| `percept.model` | `PerceptModel` | 板载估计, 消融梯载体(`plugins/embodiment_robosuite/percept.py`) |
| `exec.rollouts` | `RolloutExecutor` | 本地进程池(`harness/executor.py`); 分布式 = 换一个 mount |
| `reasoner.proposer` | `Reasoner` | 确定性搜索(`plugins/reasoner`); qwen38/naive transport 已备(round 81), 真模型 = 换 mount |
| `task.planner` | `TaskPlanner` | 确定性 StackPlanner + fail-first 验证器(`plugins/task`, round 83); VLM = 换 mount |
| `graph.skill` | `SkillGraph` | 内容寻址技能库, `root=` 落盘可跨进程回读(`plugins/graphs.py`) |
| `graph.scene` | `SceneGraph` | 真 provider ×2(round 82): SimSceneGraph(robosuite obs)/WorldSceneGraph(zos World, 走 `zos_world_bundle`) |

内核(`harness/`)做五件事:

1. **解析记账**: 每次 `resolve` 记录消费者、provider ref、是否特权; 特权解析吃预算。
2. **契约 mount 校验**: Protocol 结构性检查在挂载点失败, 错误不流进实验。
3. **配置进哈希**: Profile/Bundle/Patch → `resolve_plan` → `MountPlan.sha`; 能改变结论的手调常数进预注册哈希 —— 挂载即实验身份。
4. **链式账本**: `SessionLog` 链式承诺, 挂载、解析、campaign 结果写进同一条链, 就地篡改会被审计抓住。
5. **percept 隔离**: critic/recovery 只能碰 `FeatureView`, 特权特征读取被逐次记账 —— 真实泄漏当年就发生在 recovery 的感知里。

内核零插件依赖、插件互不 import(跨插件走 registry ref 字符串), 均由 AST 测试强制。细节见 [ARCHITECTURE.md](ARCHITECTURE.md), 目标与验收见 [GOAL.md](GOAL.md)。

## 证据纪律

一条技能想被"发布", 要过完整流水线, 每一环都有实测教训背书:

- **配对同种子门禁**: 候选 vs 父代, 精确 McNemar, 样本量由实测功效规划决定([docs/round18-power.md](docs/round18-power.md))。
- **盲发孪生**: 同一恢复、无条件触发的孪生做成对对比 —— 必须证明赢的是判断, 不是多跑的控制步(一条 fires≈100% 的"规则"曾合法通过旧门禁, 见 phase 1 报告第 06 节)。
- **held-out 烧一次**: 从未用于搜索的种子区块只评一次, 区块账本在 [STATUS.md](STATUS.md); 头条数字至少三区块。
- **消融曲线**: 每条技能带特权消融([docs/headline-finding.md](docs/headline-finding.md)) —— 特权买到的是幅度不是有无, 这是策略的性质, 要逐策略重测。
- **链式账本 + 离线审计**([docs/round5-log-and-audit.md](docs/round5-log-and-audit.md)): shadow replay 曾抓到 step 索引 off-by-one。

产出物是 `SkillRecord`: 前置条件 = 触发器, 效果 = 对父代配对增益, 失败模式 = broken 计数, 能力边界 = 特权声明 + 消融曲线。**集成判据 = provider + 过门禁的证据, 不是 demo。** phase 1 的 53 轮全过程(含 23 个被自己抓住的错误)在 [docs/report.html](docs/report.html) 与 [progress.md](progress.md)。

## 现状(round 91 止)

**R 阶梯(robosuite 轨迁移):**

- R0/R1 ✅(round 77): 4090 冷启动全绿, 跨机 parity 实测成立(demo 与 Mac 封存数字逐位一致); 三个证据洞补齐, demo 逐位不变。
- R2 ✅(round 78-79 主体, 85 收尾): 阶段机、Stack 标定、长程 campaign `runs/stack-g1` 三区块复现 + 阶段归因(受治理残余放置反超抓取 ~45-47/200 = MSR place 原语定量进场配额)。
- R3 目标函数修复 ✅(round 88): 破案"修复价值随开火时机"——语义分裂(搜索评分带 reducer 而运行时不带)/峰值臂不可达/裁决盲修三件套齐修, 修好的搜索纯 dev 证据自选峰值臂, 追平天真挑法。
- R4 meta-RSI 未动; Isaac 具身 gated; anygrasp rung 1 几何链路 sim 实证 ✅(round 89), rung 2/3 gated(license 指纹漂移, 用户行动项)。

**M 阶梯(agentic OS 化):**

- M0 ✅(round 80): zos 经 `sim.*` 工具消费 harness。 M1 ≈(round 81): 三臂对比 harness 落地; qwen38 臂待 GPU。
- M2 ✅(rounds 82/87): graph.scene 真 provider、zos 证据顾问层、`Session.evidence` 接进规划提示与授权面板(注记永不改裁决)。
- M3 ✅(rounds 83/86): `task.planner` 缝 + 规划 workload; `clear_table` 双节点图真闭环(排序/跳过已完成/arg threading)。

**修复库存(round 90):** `replace` 原语进场——首个放置形修复(释放前重放置), 对抓取程序严格增量(金哈希钉死); 双探针裁决: 放置失败对本体感受不可见(tell 1.9%)→放置治理必须特权; bring-up 转化 42.6%。首个特权 campaign `runs/place-g1`(round 91): gen1 候选被 dev 门禁正确拒绝(触发器选席谜题进 frontier), 抓取规则在处女块复验 +10.5pp 判定确立。

**Phase 1/2 已收口的头条**(Lift, 多区块合并): 脚本策略 held-out 三区块 **+32.2pp**(193 修 / 0 破, n=600), 对盲发孪生 +27.0pp(p=3e-32); 克隆策略三区块 +13.2pp。方法的实测下界: 失败不可被选择性检测的策略长不出规则, 决定性的不是成功率。

## 生态(两个 README 都用这一块, 保持一致)

```
                    MSR 研究模块(论文导向)
              后训练 VLM · 新技能(如 compliance 开门) · 新原语
                          │ 进门条件: provider + 过门禁的证据, 不是 demo
                          ▼
   ┌─────────────── physical-harness ───────────────┐
   │  agentic OS 骨架: 插件内核(capability 缝) +      │
   │  证据机器(配对门禁·盲孪生·held-out·消融·内容哈希) │
   │  task.planner → 受治理执行 → SkillRecord 技能证书 │
   └───────┬──────────────────────────┬──────────────┘
     实测证据(顾问层)            仿真轨(robosuite; Isaac gated)
           ▼                          ▼
   ┌──── zos 驾驶舱 ────┐      ┌── go2W_Sim ──┐
   │ 操作员终端 · 世界状态│      │ Isaac 数字孪生 │
   │ 安全门·authority 互斥│      │ (gated 待接)  │
   │ 真机 Go2W+PiPER 执行 │      └──────────────┘
   └────────────────────┘
```

关系一句话: **physical-harness 是 OS(证据与治理的内核), zos 是驾驶舱(操作员与真机的活体层),
MSR 模块经门禁进入, 技能以实测 SkillRecord 流向驾驶舱做顾问(手写安全下界永不放宽)。**

## 复现已发布的结果(parity)

```bash
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/parity_check.py <archived_campaign_dir>
```

把封存的 campaign 经内核路径重跑, 逐位比对每代规则 canonical、bundle sha、dev/blind 门禁与 held-out 的全部配对字段。phase 2 以双策略(脚本/克隆)parity 四组 PASS 收口(对 Mac 封存的 `runs/campaign-pj-*`; 该归档尚未拷到本机, 对应上文 2 个 skip 测试)。跨机迁移的 parity 走另一条实测路: 4090 上 `runs/demo` 与 Mac 封存数字逐位一致(round 77)。

同机复现: `demo_campaign.py --out runs/demo-rN` 重跑后与 `runs/demo` 逐字段 diff —— round 77-79 三次重跑, 数字零移动, 差异仅限当轮新增字段(预期且诚实: 挂载与预注册进哈希, 加缝就换 sha)。
