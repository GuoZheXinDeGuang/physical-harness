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

当前全量: **457 通过, 3 跳过**(跳过 = Mac 归档 campaign 与 BC 权重不在 git, 预期)。

底座快道(W6, 改底座前后各跑一遍、变差不许合入)= 隔离进程(robosuite 卡缺席)里 `pytest -m "not robosuite"`: **427 通过, 6 跳过, 27 弃**。快照格式与隔离跑法见 [docs/base-gate.md](docs/base-gate.md)。

- `mujoco==3.3.7` + `robosuite==1.5.2` 是硬 pin: mujoco>=3.4 把 `qM` 改名 `M`, robosuite 1.5.2 会崩。
- robosuite/mujoco 进 `[embodiment_robosuite]` 可选 extra; 不装这块卡, 底座仍能开机、跑通自己的测试(纯假件)。
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

同一份 artifact 里: 对盲发孪生 +35.5pp(盲发基线 29.5%, p=8.4e-16, judgement established); 消融曲线零破坏全档(真值 +18.0 / sd=0.010 +13.5 / 0.020 +6.5 / 0.030 +3.5pp)。**头条现为四区块**(rounds 85/91 复验): **+6.5 / +9.5 / +11.0 / +10.5pp, 75 修 0 破, n=800**, 判定四块全确立(42000/42200/42400 + place-g1 的处女块 47000)。放置链(place-g2, round 96)成为并列第二个报告级技能: **+9.5 / +11.0 / +10.5pp, 68 修 6 破, n=600**, 三块判定全确立(自带块 47200 + 复现块 47400/48000), 对盲发孪生 +44.5–55.0pp; broken 率逐块 2/2/2 稳定。

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
| `graph.scene` | `SceneGraph` | 真 provider ×2(round 82): SimSceneGraph(robosuite obs)/WorldSceneGraph(机器人 `World.snapshot`, 走 `robot-world` bundle; 首个消费者 zos 已退役, 留给未来 actuation:real 真机卡, 见 docs/zos-salvage.md) |

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

## 现状(round 96 止)

**R 阶梯(robosuite 轨迁移):**

- R0/R1 ✅(round 77): 4090 冷启动全绿, 跨机 parity 实测成立(demo 与 Mac 封存数字逐位一致); 三个证据洞补齐, demo 逐位不变。
- R2 ✅(round 78-79 主体, 85 收尾): 阶段机、Stack 标定、长程 campaign `runs/stack-g1` 三区块复现 + 阶段归因(受治理残余放置反超抓取 ~45-47/200 = MSR place 原语定量进场配额)。
- R3 目标函数修复 ✅(round 88): 破案"修复价值随开火时机"——语义分裂(搜索评分带 reducer 而运行时不带)/峰值臂不可达/裁决盲修三件套齐修, 修好的搜索纯 dev 证据自选峰值臂, 追平天真挑法。
- R4 meta-RSI 种子已落(eval battery, round 94); Isaac 具身 gated; anygrasp **round 93 用户裁定弃用**, 换几何位姿抓取(见下)。

**M 阶梯(agentic OS 化):**

- M0 ✅(round 80): zos 经 `sim.*` 工具消费 harness。 M1 ≈(round 81): 三臂对比 harness 落地; qwen38 臂 round 96 包成模型卡。
- M2 ✅(rounds 82/87): graph.scene 真 provider、证据顾问层、`Session.evidence` 接进规划提示与授权面板(注记永不改裁决)。
- M3 ✅(rounds 83/86): `task.planner` 缝 + 规划 workload; `clear_table` 双节点图真闭环(排序/跳过已完成/arg threading)。

**修复库存 + 放置链(rounds 90-96):** `replace` 原语进场——首个放置形修复(释放前重放置), 对抓取程序严格增量(金哈希钉死); 双探针裁决: 放置失败对本体感受不可见(tell 1.9%)→放置治理必须特权。`runs/place-g1`(round 91) gen1 的 dev 拒绝 **round 92 破案为假阴性**: campaign 给新规则铸名与父代同名, `governed_rollout` 的调用预算按 rule_id 做键致两规则静默合并——结构修复改按链位置做键(对唯一 id 的封存 bundle 逐位不变, 决定论/影子重放作证)。重战 `runs/place-g2`(round 92 终章)首次续种进化长出两代放置链(gen1/gen2 皆晋级, 第一条特权晋级规则); round 96 复现两块后**放置链三块判定全确立**, 与抓取链并列第二个报告级技能。

**几何位姿抓取 ✅(round 93)→ 成卡(round 97):** 质心+PCA 顶抓, 走 vendored client 同一 transport 缝, 控制回路**零特权读**——首个零特权感知抓取闭环。60 席(90200-90259): 几何臂 100% / 位置误差均值 0.9cm, 与无噪声基线打平。诚实边界: 单物体假设(平面滤波+固定工作箱), 多物体前需加聚类。round 97 打包成技能卡 `plugins/skill_geometric_grasp`: `lift_geometric` 任务绑定(policy = GraspPoseDriver-on-Lift 工厂, planner 从零单节点), 带 gen-1 验货 `[claim]`(dev/held-out 48200-48899 预登记于 STATUS), 体检 GREEN; 底座零改动, 相机取云在 make_driver 里现开一间(治理环的冻结策略热路径不动)。

**主机底座(M4 + GOAL v4 宪章, rounds 94-96):** 项目升为"机器人 agent 的主机底座"——底座只焊死执行层(跑任务)与进化层(RSI 攒证据), 其余(技能/模型/软件包/机器人/界面)皆是随插随拔的卡。两层结构与 mode 机制见 [ARCHITECTURE.md](ARCHITECTURE.md)。

- **常驻运行时 ✅(M4, round 94):** `scripts/harness_runtime.py` boot 内核 → 挂技能目录+实测证据 → 接任务; 每任务治理全程(planner→validate→governed rollout→阶段评分→账本), 单任务失败不倒系统(逃生舱记 `runtime.task_error`, 循环续)。会话链跨任务连续、重启可续可验; RSI 作为一类系统任务被调度, 产出 SkillRecord 立即回流挂载目录。注错 soak: 50 brief × 6 故障类 + 中途 SIGKILL, 40 done / 10 failed / 恰 5 task_error / 零断链。
- **manifest 自注册 ✅(round 96):** 卡 = 带 `manifest.toml` 的目录(纯数据, tomllib 解析永不 import); `harness/manifest.py` 折叠出 {mounts, task_bindings, campaigns, third_party}, `base_profile()` 变成对已装 manifest 的折叠——**base plan sha 逐位不变**(b905a51…, round25-rerun 封存值), 重构顶着封存 parity 过关。运行时四张写死表退役; 丢卡目录进 `plugins/` → `git status` 只有那个目录、新任务被接受、base sha 不动。brief 仍只带任务字符串, 绑定在 boot 从已装 manifest 的并集解析(文件系统权威, 非 brief)。写卡说明书见下节。
- **两态铁律 ✅(mode 机制, round 96):** 会话默认 EXECUTION(fail-safe: 真任务永不触发 RSI)。`--mode {execution,evolution}` 写一次 `MODE` 文件(重启断言一致=进程间不可变), 封 `runtime.boot` 行 {mode, skills_manifest, mount_plan_sha} 为链 0 号(篡改即断链, 断链就是审计)。campaign 类 brief 只在进化态被接受(否则拒到 failed/ 记 `runtime.task_error`, 非中和); 每执行任务前重折技能目录摘要集断言等于 boot 清单。单向流: 只有进化态写封存记录, 执行态只挂已封 SkillRecord + 冻结配置、从不写。
- **装机规矩 ✅(体检 + 验货, round 96):** `scripts/plugin_doctor.py <卡目录>` 体检(Tier A 复用 `Kernel.provide` 的 isinstance 门 + 拒 actuation:real; Tier B 按能力类别一发假件冒烟, 确定性政策分流; `needs_sim` 卡真仿真层); `scripts/acceptance_campaign.py --claim <卡>` 验货(既有证据机器的参数化封装, 零新统计, 过关 = ≥1 条 `heldout_judgement_established=True` 的晋级 SkillRecord); `plugin_doctor --verify-claim` 对 `runs/` 核对卡的封存声明。体检 mode-agnostic, 验货 evolution-only——那道封条就是执行态准入票。
- **驾驶舱 = dsh ✅(round 95):** 骑标准 MCP 缝零 vendor 接 dsh(:3080), 七只只读工具逐字节等价 `board.store` + `submit_brief` 原子投递; 自建 web board 对齐后退役, 报告改由 `python -m board.report` headless 出。**PH 上牌**: `profiles/dsh/rebrand.sh` 对安装副本七处打补丁(标题/PWA/favicon/slogan), dsh 源码零改动、包名不动、MIT 署名保留; `scripts/cockpit` 原生启动器每次启动幂等重敷补丁(dsh 升级后自愈)。
- **一条命令起全部 ✅(round 98):** 操作员只用 UI, 不再开第二个终端。`scripts/cockpit` 一条命令同时拉起**常驻运行时**(runs/session-main)**和** dsh 驾驶舱(:3080), 起完就留着跑:

  ```bash
  scripts/cockpit          # 起运行时 + dsh UI @ :3080, 都留活
  scripts/cockpit --stop   # 停掉本次 cockpit 起的那两个(按 pidfile 里的精确 PID)
  # UI 聊天里: "跑一个 stack, seed 90000" → LLM 调 submit_brief 原子投递 →
  # 运行时认领 → 屏 2 窗里逐步看到这条 rollout(真 governed_rollout 加一扇窗, 零逻辑复制)
  ```

  运行时是 **adopt-or-spawn**: 已经有一只在跑 session-main 就认领(打印 PID、不重启、`--stop` 不碰它——不是 cockpit 起的), 否则新起一只(nohup, 日志进 `runs/session-main/runtime.log`)并记进 pidfile 供 `--stop` 精确回收(绝不按 pattern kill)。一个 session dir 上永不两只运行时。`--no-runtime` 只起 UI、`--no-render` 强制无头。

  **屏 2 活窗只在挂了显示器时出现**: `--render` 自动加 IFF `$DISPLAY` 已设(无头时运行时硬拒 `--render`, 绝不静默退回)。`--render` 是每次开机的运维选择(像 `--mode`), **不进 brief**(brief 仍只 selector+budgets)、与执行/进化态正交; `MUJOCO_GL=egl` 是无头 GL、开机自动卸掉换原生 GL(round-80 教训)。pacing 见 `--render-fps`(默认 50)。campaign 走子进程, 无论如何仍无头。
- **zos 退役 ✅(W4/R10, 2026-08-23):** 操作面归 dsh, 设计资本审计落 [docs/zos-salvage.md](docs/zos-salvage.md)(authority FSM / 风险派生树 / 权限阶梯 / verify 沙箱 / not-measured 纪律 / World 状态模型 / 真机 ACT 半的需求规格); zos 仓 README 立碑 + tag `zos-retirement-2026-08-23` 冻结、代码零删除(`gh repo archive` 属组织动作留用户)。真机不搁置——升为未来 `actuation:real` 具身卡(v4.2), 挂独立认证运行时, sim 运行时拒挂。

**Phase 1/2 已收口的头条**(Lift, 多区块合并): 脚本策略 held-out 三区块 **+32.2pp**(193 修 / 0 破, n=600), 对盲发孪生 +27.0pp(p=3e-32); 克隆策略三区块 +13.2pp。方法的实测下界: 失败不可被选择性检测的策略长不出规则, 决定性的不是成功率。

## 生态

```
                    MSR 研究模块(论文导向)
              后训练 VLM · 新技能(如 compliance 开门) · 新原语
                          │ 进门条件: provider + 过门禁的证据, 不是 demo
                          ▼
   ┌─────────────── physical-harness ───────────────┐
   │  agentic OS 骨架: 插件内核(capability 缝) +      │
   │  证据机器(配对门禁·盲孪生·held-out·消融·内容哈希) │
   │  task.planner → 受治理执行 → SkillRecord 技能证书 │
   └──┬───────────────────┬────────────────────┬─────┘
    操作面(经 MCP)      仿真具身卡            真机具身卡(未来)
      ▼                    ▼                     ▼
 ┌─ dsh 驾驶舱 ─┐   ┌─ robosuite ─┐   ┌ actuation:real(gated) ┐
 │ 现成开源控制台│   │ Panda/Sawyer │   │ Go2W+PiPER · 世界状态  │
 │ 提任务/看会话 │   │ Isaac gated  │   │ 安全门 · authority 互斥│
 │ 体检/验货/看链│   └─────────────┘   │ 独立认证运行时, sim 拒挂│
 └─────────────┘                     └───────────────────────┘

 zos 驾驶舱已退役(2026-08-23, 宪章 W4)→ 操作面归 dsh; 真机=同一 embodiment.env 缝下
 未来的 actuation:real 具身卡, 其设计输入见 docs/zos-salvage.md
```

关系一句话: **physical-harness 是 OS(证据与治理的内核), dsh 是驾驶舱(操作员经 MCP 的操作面),
真机是同一 `embodiment.env` 缝下未来的 actuation:real 具身卡(zos 已退役, 设计资本见
docs/zos-salvage.md), MSR 模块经门禁进入, 技能以实测 SkillRecord 流向消费者做顾问
(手写安全下界永不放宽)。**

## 写一张卡(说明书)

底座的终态验收: 实验室同学照这张说明书写个插件, 体检通过、验货通过, 就能进主线——底座一行不改, 全程在 dsh 里。卡 = `plugins/<名字>/` 一个目录, 核心是一份 `manifest.toml`(纯数据, 被解析永不 import, 所以底座保持声明式)。

**1. manifest.toml —— 卡的自述。** 参考卡 `plugins/skill_toy` 是最小任务卡: 丢进 `plugins/` 即令 `{"kind":"task","task":"toy"}` 被接受, 底座零改动。

```toml
# 一个新任务名, 全靠 manifest 登记 —— 不碰底座、不改 harness_runtime。
[task_bindings.toy]
policy    = "plugins.policies:stack_scripted_provider"   # 执行绑定可借他卡
planner   = "plugins.skill_toy.planner:provider"          # 本卡自持
catalogue = "plugins.skill_toy.planner:CATALOGUE"         # 技能作者的词表, 按 ref 挂
oracles   = "plugins.skill_toy.planner:ORACLES"
```

schema 全貌(各段皆可选, 装了才折叠进并集; 跨卡同名一律响亮):

- `[mounts.<capability>]` `ref=` / `params=` —— 把 provider 挂到能力缝(取代旧 base_profile 写死挂载);
- `[bundles.<name>]` —— 叠加层挂载(如 sawyer / robot-world), 从不进 base 折叠;
- `[task_bindings.<task>]` —— 任务字符串 → policy / planner / catalogue / oracles;
- `[campaigns.<name>]` —— 一个 campaign brief 可 spawn 的验货脚本(服务端 allowlist);
- `third_party = [...]` —— 卡的第三方依赖面(喂插件边界 AST 测试);
- `actuation = "sim" | "real"` / `needs_sim = true | false` —— sim 运行时拒挂 `real`;
- `[claim]` / `[claim.sealed]` —— 技能卡的封存声明(见第 3 步)。

**从零写 planner 的契约**(不借他卡时): `planner` ref 指向一个零参工厂
`provider() -> TaskPlanner`, 其 `plan(brief: Mapping) -> Mapping` 读
`brief["task"]` 返回纯 JSON 计划 `{"goal": str, "nodes": [{"id", "skill",
"args", "after"}, ...], "verify": [{"after", "predicate"}, ...]}`。每个节点带
**恰好**四个键 `id/skill/args/after`(`plugins.task.validate` 的 `_NODE_KEYS`
逐字校验): `id` 唯一非空, `after` 只列**更早**节点的 id(拓扑序=列表序=执行序),
`skill` 必在你声明的 catalogue 里, `args` 逐键按 catalogue 的类型校验。验收谓词不在
节点上, 而在**单独的** `verify` 列表: 每条 `{"after": <节点 id>, "predicate":
<谓词名>}`, `predicate` 必在你声明的 oracles 集合里, 且 `verify` 不可为空(无验收
的计划是空谈)。`catalogue` 是 `{技能名: {参数名: type}}` 的词表(`type` 对象, 所以
走 ref 不走 JSON), `oracles` 是谓词名集合——两者被 fail-first 验证器
(`validate_plan`)在执行前拒掉坏计划。契约即 `harness/contracts.py:TaskPlanner`,
样例即 `plugins/skill_toy/planner.py`(23 行, 借基座 `stack` 技能的执行绑定)与
`plugins/skill_geometric_grasp/planner.py`(单节点 `grasp`, 自持词表; 执行走验货
campaign 而非通用 task 环, 故其 `grasp` 是卡词表而非 SKILL_SPECS 执行绑定)。

**2. 体检(装机第一关, mode-agnostic):**

```bash
PYTHONPATH=. .venv/bin/python scripts/plugin_doctor.py plugins/skill_toy
```

Tier A 挂载时形状校验(复用 `Kernel.provide` 的 isinstance 门, 挂错形状当场红, 非任务中失败) + Tier B 按能力类别一发假件冒烟(确定性政策分流: percept/planner 必须确定, LLM reasoner 只验形状)。`needs_sim` 的卡在无仿真的底座机上跳过真仿真层, 不算失败。

**3. 验货(能力声称必须过对照实验, evolution-only):** 带 `[claim]` 的技能卡声明它凭什么算数——task / policy / dev+heldout 种子块 / stages, 逐位重建同一份 prereg:

```bash
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/acceptance_campaign.py \
    --claim plugins/task --out runs/accept-stack
```

这是既有证据机器的参数化封装, **零新统计**。过关 = 至少一条 `heldout_judgement_established=True` 的晋级 SkillRecord。封存后 `plugin_doctor --verify-claim` 拿 `[claim.sealed]`(store + 精确 SkillRecord 摘要 + 头条 rescore 块)对 `runs/` 核对——那道封条就是执行态挂载的准入票。种子块用 `scripts/alloc_seeds.py` 领未烧区间, 在 STATUS.md 预定(运行时的重叠守卫仍是最终执法)。

**4. 验证四件套(每条技能进库前都要过, GOAL v4.2 北极星):**

① 同条件与父代**配对比较**(精确 McNemar); ② 与不含判断的**盲孪生**对照(证明赢的是判断, 不是多跑的控制步); ③ 从未参与调试的 **held-out** 区块终评(头条至少三块); ④ 传感条件变差的**消融曲线**(= sim-to-real 保留度估计)。通过者连同全部实验数据进技能库。

## 复现已发布的结果(parity)

```bash
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/parity_check.py <archived_campaign_dir>
```

把封存的 campaign 经内核路径重跑, 逐位比对每代规则 canonical、bundle sha、dev/blind 门禁与 held-out 的全部配对字段。phase 2 以双策略(脚本/克隆)parity 四组 PASS 收口(对 Mac 封存的 `runs/campaign-pj-*`; 该归档尚未拷到本机, 对应上文 2 个 skip 测试)。跨机迁移的 parity 走另一条实测路: 4090 上 `runs/demo` 与 Mac 封存数字逐位一致(round 77)。

同机复现: `demo_campaign.py --out runs/demo-rN` 重跑后与 `runs/demo` 逐字段 diff —— round 77-79 三次重跑, 数字零移动, 差异仅限当轮新增字段(预期且诚实: 挂载与预注册进哈希, 加缝就换 sha)。
