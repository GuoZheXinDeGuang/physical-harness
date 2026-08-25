# physical-harness

**机器人 agent 的主机底座: 一个插件内核, 承载一台证据机器。**

底座只焊死两层——**执行层**(接任务、受治理地跑)与**进化层**(离线 RSI 攒证据)——
其余一切(技能、模型、软件包、机器人本体、操作界面)都是随插随拔的卡。三个思想来源, 一个自己的贡献:

- **插件内核**来自 dsh(deepseek-harness)的 everything-is-a-plugin: 能力是缝, provider 是插件, 换实现 = 改 mount, 不是改代码。
- **RSI workload** 来自 Zetta 的 physical RSI: 冻结策略不动, 演化的是 critic/recovery——何时介入、怎么恢复、能力边界。
- **自己的贡献是把特权预算机制化**: 特权特征读取(FeatureView)与特权能力解析(Kernel)全部记账吃预算, 每条技能附带特权消融曲线——sim-to-real gap 从一句担忧变成一条可测的曲线。

所有数字来自真实 robosuite/MuJoCo 仿真回合, 无 mock 验证, 无外部 API 调用。

## 两态铁律

会话默认 **EXECUTION**(fail-safe: 真任务永不触发 RSI)。`--mode evolution` 显式开进化态,
只有它能写封存记录; 执行态只挂已封存的 SkillRecord + 冻结配置、从不写。mode 写一次 `MODE`
文件, 重启断言一致(进程间不可变), 并封进链 0 号——篡改即断链, 断链就是审计。

## 一条命令起全部

操作员只用 UI, 不开第二个终端。`scripts/cockpit` 同时拉起**常驻运行时**和 **dsh 驾驶舱**, 起完留活:

```bash
scripts/cockpit          # 起运行时 + dsh UI @ :3080, 都留活
scripts/cockpit --stop   # 只停本次 cockpit 起的那两个(按 pidfile 里的精确 PID)
# UI 聊天里: "跑一个 stack, seed 90000" → LLM 调 submit_brief 原子投递 →
# 运行时认领 → 屏 2 窗里逐步看到这条 rollout(真 governed_rollout 加一扇渲染窗, 零逻辑复制)
```

运行时是 **adopt-or-spawn**: 已有一只在跑 session-main 就认领(打印 PID、不重启、`--stop` 不碰它),
否则新起一只并记进 pidfile 供精确回收(绝不按 pattern kill)。一个 session dir 上永不两只运行时。
`--no-runtime` 只起 UI; `--no-render` 强制无头。**屏 2 活窗只在挂了显示器时出现**:
`--render` 自动加 IFF `$DISPLAY` 已设(无头时硬拒, 绝不静默退回), 与执行/进化态正交、不进 brief。

驾驶舱 = **ph-station**(dsh 的 fork): 骑标准 MCP 缝零 vendor 接入, 七只只读工具逐字节等价
`board.store` + `submit_brief` 原子投递; 战报/演进/机箱/账本面板经 fork host 只读渲染, 报告
另由 `python -m board.report` headless 出。设计见 [docs/ph-station-design.md](docs/ph-station-design.md)。

## 测试

```bash
uv venv && uv pip install -e ".[dev]"
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python -m pytest
```

- **全量**(装了 robosuite 卡): **553 通过, 9 跳过**(跳过 = Mac 归档 campaign 与 BC 权重不在 git, 加 5 个 robocasa 卡自证只在 robocasa venv 跑, 预期)。
- **底座快道**(改底座前后各跑一遍、变差不许合入)= 隔离进程(robosuite 卡缺席)里
  `pytest -m "not robosuite"`: **522 通过, 12 跳过, 28 弃**。快照格式与隔离跑法见
  [docs/base-gate.md](docs/base-gate.md)。

`mujoco==3.3.7` + `robosuite==1.5.2` 是硬 pin(mujoco>=3.4 把 `qM` 改名 `M`, robosuite 1.5.2 会崩);
两者进 `[embodiment_robosuite]` 可选 extra——不装这块卡, 底座仍能开机、跑通自己的测试(纯假件)。
Linux 无头环境要 `MUJOCO_GL=egl`; 无 GPU 训练、无网络、无 API key 需求。

## 架构

能力缝是 `harness/definitions.py` 的清单, 契约是 `harness/contracts.py` 的 runtime_checkable
Protocol——挂错形状在 mount 时就报错, 不是 mid-episode:

| 能力 | 契约 | 当前 provider |
|---|---|---|
| `embodiment.env` | `EnvProvider` | robosuite Panda(`plugins/embodiment_robosuite`); Sawyer = 换 `sawyer_bundle` |
| `embodiment.ground_truth` | `GroundTruthState`(特权) | 无默认挂载——特权不进 base profile, 真机部署天然缺席 |
| `policy.driver` | `PolicyFactory` | Lift/Stack 脚本策略 + 行为克隆适配(`plugins/policies`) |
| `percept.model` | `PerceptModel` | 板载估计, 消融梯载体(`plugins/embodiment_robosuite/percept.py`) |
| `exec.rollouts` | `RolloutExecutor` | 本地进程池(`harness/executor.py`); 分布式 = 换一个 mount |
| `reasoner.proposer` | `Reasoner` | 确定性搜索(`plugins/reasoner`); qwen38/naive transport 已备, 真模型 = 换 mount |
| `task.planner` | `TaskPlanner` | 确定性 StackPlanner + fail-first 验证器(`plugins/task`); VLM = 换 mount |
| `graph.skill` | `SkillGraph` | 内容寻址技能库, `root=` 落盘可跨进程回读(`plugins/graphs.py`) |
| `graph.scene` | `SceneGraph` | SimSceneGraph(robosuite obs)/WorldSceneGraph(`robot-world` bundle, 留给未来真机卡) |

内核(`harness/`)做五件事: ① **解析记账**——每次 `resolve` 记录消费者/provider/是否特权, 特权解析吃预算;
② **契约 mount 校验**——Protocol 结构性检查在挂载点失败, 错误不流进实验; ③ **配置进哈希**——
Profile/Bundle/Patch → `MountPlan.sha`, 能改变结论的手调常数进预注册哈希, 挂载即实验身份;
④ **链式账本**——`SessionLog` 链式承诺, 挂载/解析/结果同链, 就地篡改被审计抓住; ⑤ **percept 隔离**——
critic/recovery 只能碰 `FeatureView`, 特权读取逐次记账。内核零插件依赖、插件互不 import(跨插件走
registry ref 字符串), 均由 AST 测试强制。细节见 [ARCHITECTURE.md](ARCHITECTURE.md), 目标与验收见 [GOAL.md](GOAL.md)。

## 证据纪律

一条技能想被"发布", 要过完整流水线, 每一环都有实测教训背书:

- **配对同种子门禁**: 候选 vs 父代, 精确 McNemar, 样本量由实测功效规划决定。
- **盲发孪生**: 同一恢复、无条件触发的孪生做成对对比——必须证明赢的是判断, 不是多跑的控制步(一条 fires≈100% 的"规则"曾合法通过旧门禁)。
- **held-out 烧一次**: 从未用于搜索的种子区块只评一次, 区块账本在 [STATUS.md](STATUS.md); 头条数字至少三区块。
- **消融曲线**: 每条技能带特权消融([docs/headline-finding.md](docs/headline-finding.md))——特权买到的是幅度不是有无, 这是策略的性质, 要逐策略重测。
- **链式账本 + 离线审计**: shadow replay 曾抓到 step 索引 off-by-one, 就地篡改会断链。

产出物是 `SkillRecord`: 前置条件 = 触发器, 效果 = 对父代配对增益, 失败模式 = broken 计数,
能力边界 = 特权声明 + 消融曲线。**集成判据 = provider + 过门禁的证据, 不是 demo。** 已收口的报告级技能:
抓取链 held-out 四区块 **+6.5 / +9.5 / +11.0 / +10.5pp**(75 修 / 0 破, n=800), 放置链三区块
**+9.5 / +11.0 / +10.5pp**(68 修 / 6 破, n=600); 几何位姿抓取零特权闭环 100% 成功、位置误差均值 0.9cm。

## 写一张卡

底座的终态验收: 实验室同学照说明书写个插件, 体检通过、验货通过, 就能进主线——底座一行不改, 全程在 dsh 里。
卡 = `plugins/<名字>/` 一个目录, 核心是一份 `manifest.toml`(纯数据, 被解析永不 import, 所以底座保持声明式)。
参考最小卡 `plugins/skill_toy`: 丢进 `plugins/` 即令 `{"kind":"task","task":"toy"}` 被接受, 底座零改动。

manifest schema(各段皆可选, 装了才折叠进并集; 跨卡同名一律响亮报错):

- `[mounts.<capability>]` `ref=`/`params=`——把 provider 挂到能力缝;
- `[bundles.<name>]`——叠加层挂载(如 sawyer / robot-world), 从不进 base 折叠;
- `[task_bindings.<task>]`——任务字符串 → policy / planner / catalogue / oracles;
- `[campaigns.<name>]`——一个 campaign brief 可 spawn 的验货脚本(服务端 allowlist);
- `third_party = [...]`——卡的第三方依赖面(喂插件边界 AST 测试);
- `actuation = "sim"|"real"` / `needs_sim = true|false`——sim 运行时拒挂 `real`;
- `[claim]` / `[claim.sealed]`——技能卡的封存声明。

**装机两关:**

```bash
# 体检(mode-agnostic): Tier A 挂载形状校验 + Tier B 按能力类别一发假件冒烟
PYTHONPATH=. .venv/bin/python scripts/plugin_doctor.py plugins/skill_toy

# 验货(evolution-only): 既有证据机器的参数化封装, 零新统计
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/acceptance_campaign.py \
    --claim plugins/task --out runs/accept-stack
```

验货过关 = 至少一条 `heldout_judgement_established=True` 的晋级 SkillRecord。封存后
`plugin_doctor --verify-claim` 拿 `[claim.sealed]` 对 `runs/` 核对——那道封条就是执行态挂载的准入票。
种子块用 `scripts/alloc_seeds.py` 领未烧区间, 在 STATUS.md 预定(运行时的重叠守卫是最终执法)。

**验证四件套**(每条技能进库前都要过): ① 同条件与父代**配对比较**(精确 McNemar);
② 与不含判断的**盲孪生**对照; ③ 从未参与调试的 **held-out** 区块终评(头条至少三块);
④ 传感条件变差的**消融曲线**(= sim-to-real 保留度估计)。通过者连同全部实验数据进技能库。

## 生态

```
                    MSR 研究模块(论文导向)
              后训练 VLM · 新技能 · 新原语
                          │ 进门条件: provider + 过门禁的证据, 不是 demo
                          ▼
   ┌─────────────── physical-harness ───────────────┐
   │  插件内核(capability 缝) + 证据机器             │
   │  (配对门禁·盲孪生·held-out·消融·内容哈希)        │
   │  task.planner → 受治理执行 → SkillRecord 技能证书 │
   └──┬───────────────────┬────────────────────┬─────┘
    操作面(经 MCP)      仿真具身卡            真机具身卡(未来)
      ▼                    ▼                     ▼
   dsh 驾驶舱          robosuite            actuation:real(gated)
   提任务/看会话       Panda/Sawyer         独立认证运行时, sim 拒挂
   体检/验货/看链      Isaac gated          设计输入见 docs/zos-salvage.md
```

关系一句话: **physical-harness 是 OS(证据与治理的内核), dsh 是驾驶舱(操作员经 MCP 的操作面),
真机是同一 `embodiment.env` 缝下未来的 actuation:real 具身卡(设计资本见
[docs/zos-salvage.md](docs/zos-salvage.md)), MSR 模块经门禁进入, 技能以实测 SkillRecord 流向消费者做顾问
(手写安全下界永不放宽)。**

## 复现已发布的结果(parity)

```bash
PYTHONPATH=. MUJOCO_GL=egl .venv/bin/python scripts/parity_check.py <archived_campaign_dir>
```

把封存的 campaign 经内核路径重跑, 逐位比对每代规则 canonical、bundle sha、dev/blind 门禁与
held-out 的全部配对字段。同机复现: `demo_campaign.py --out runs/demo-rN` 重跑后与 `runs/demo`
逐字段 diff, 数字零移动, 差异仅限当轮新增字段(挂载与预注册进哈希, 加缝就换 sha)。
