# progress

## Round 1 — 2026-08-19 — setup

### 做成了什么

- 实测确认 Mac 上可行的仿真底座：mujoco 3.3.7 + robosuite 1.5.2，212 episodes/min @ 10 workers
- 定下 GOAL.md：融合 dsh 的日志/不变量/接缝 + Zetta 的演化/门禁，加自己的特权预算
- 起了分析 workflow（wf_585c9df5-4ac）

### 什么没成

- mujoco 3.11 + robosuite 1.5.2 不兼容（qM→M 改名）。已 pin 3.3.7 解决。
- `MUJOCO_GL=osmesa` 在 mac 上是非法值。无头场景不要设这个变量。

### 下一轮种子

workflow 返回后，先落**最短真实链路**：跑一个 episode → 落一条事件日志 → 从日志重建出 critic 在第 t 步看到的东西 → 断言一致。
这条链路通了，其余全是往上叠。

## Frontier

**当前天花板：** 还没有代码。地板是 GOAL.md 的 5 条验收。

**下一个 frontier（按 ambition × feasibility 排）：**
1. 真正的沙箱 critic 代码执行（Zetta 停在门口的那一步）
2. 用 BC 训一个真的弱策略当冻结基线，让失败像真实 VLA 的复合误差而不是脚本 bug
3. 特权消融曲线：把 sim-to-real gap 变成一条可画的曲线而不是一句话
4. 多任务族迁移：证明 critic 能 zero-shot 迁移（Zetta 只在同族 PnP 内证明过）

**权衡过但暂不做：**
- 自己写 MuJoCo 场景而不是用 robosuite：控制力更强但失去「和 RoboCasa 同底座」这个迁移论据。不划算。
- 上 Ray：单机 18 核用 multiprocessing 足够，Ray 的价值在跨机，现在是纯复杂度。

## Round 1 结果 — 2026-08-19

### 做成了什么

1. **可行性闭环**：mac 上跑通 robosuite/Panda 无头仿真，212 episodes/min（docs/verified-environment.md）
2. **难度标定**：设计了一个「基于错误感知开环执行」的冻结策略，噪声 sd 可调，
   sd=0.020 → 基线 50%，落在 Zetta LIBERO-Pro 基线（31-38%）同一量级（docs/difficulty-calibration.md）
3. **零特权可检测性**：失败的手指开度和成功差 40 倍，且该量属于本体感受，真机可测
4. **上限验证 + 头号发现**：手写 critic+recovery 把 50% 打到 100%（p<1e-6，30 修复 0 破坏）；
   但把 recovery 的重读感知降到和策略同等噪声后，只剩 +13.3% 且不显著（docs/headline-finding.md）

### 什么没成（重要）

- **手定 critic 阈值失败。** 第一版用 episode 末尾统计量定 tau=0.005，60 个 episode 只触发 1 次，
  提升 +1.7% 不显著。必须先做 EOD 扫描才能定阈值和武装时机。这条弯路直接论证了自动搜索的必要性。
- **我自己漏了 recovery 的特权。** 只盯着 critic 是零特权就宣称结果成立，
  是消融实验把 `target = obs["cube_pos"]` 这句抓出来的。设计上特权预算必须覆盖 recovery。

### 下一轮种子

最短真实链路：跑 1 个 episode → 落事件日志 → 从日志重建第 t 步的 critic 视图 → 断言一致。
通了之后依次叠：feature contract → critic runtime → 配对门禁 → proposer → campaign。

## Frontier

**当前天花板：** 已知一对手写 critic-recovery 能到 100%（满特权）/ 63%（零特权）。
harness 要做到的是**自动**找到这对，并且在声明的特权预算下评估它。

**下一个 frontier（ambition × feasibility）：**
1. 自动 EOD + 阈值搜索（已证明人手会做错，价值明确）
2. 沙箱 critic 代码执行 —— Zetta 停在门口那一步
3. 特权消融曲线自动化：每个被提升的技能都自带一条曲线，而不是一句话
4. 让 recovery 也在预算内演化：现在最好的 recovery 靠特权感知，harness 应该能自己发现
   「用 wrist-camera 式的带噪感知 + 多次尝试」这类不依赖特权的策略
5. 更难的任务族（Stack / PickPlace）+ 跨任务 zero-shot 迁移

**权衡过但不做：** 自写 MuJoCo 场景（失去与 RoboCasa 同底座的迁移论据）；上 Ray（单机 18 核 multiprocessing 够）。

## Round 2 — 2026-08-19 — 特征契约 + 自动搜索

### 做成了什么

1. `governor/features.py`：**名字空间即声明**。`observable.*` / `privileged.*` 前缀和 `Privilege`
   枚举必须一致，否则构造时就抛。未知特征在 `privilege_cost` 处直接 KeyError ——
   proposer 无法凭空发明特征，也就无法绕过预算去摸原始观测。
2. `governor/env.py`：确定性环境 provider（`suite.make(seed=)`）+ 冻结策略。
   配 4 个回归测试，其中一个专门断言「改全局 numpy 种子不影响已播种的 episode」。
3. `governor/search.py`：EOD 扫描 + 触发器搜索，受特权预算约束。
4. **验证：搜索赢了人手**（docs/search-beats-hand.md）。0.1 秒内重现我试错才找到的
   `finger_gap` 规则，并找到更好的 `gripper_effort` 规则（同样 recall 1.00 / fp 0.00，但早 6 步触发）。
   零特权最优解得分 1.105 > 特权最优解 1.095 —— 这个任务上特权对检测没有帮助。

### 什么没成 / 注意

- `governor` 包没装进 venv（用的 `pip install -e .` 但 packages.find 在建包前就跑了），
  现在靠 `PYTHONPATH=.` 跑。下一轮 `uv pip install -e .` 重装一次即可。
- 搜索目标函数里 earliness 的权重（0.25）是拍的，还没做敏感性分析。
  它已经在改变排序（gripper_effort 靠 lead 赢过 finger_gap），所以值得单独验证。

### 下一轮种子

接上 recovery 执行器和配对门禁，端到端复现 round-1 的数字，然后把特权消融曲线自动化。
关键：round-1 已知满特权 recovery 能到 100%、零特权只剩 +13.3%，
所以门禁必须能同时报出这两个数，否则会误报成功。

## Frontier（更新）

**新增发现改变了排序：** 特权在「检测」上无用，但在「更早预警」上有用
（`privileged.grasp_error` 在 step 27 触发，比零特权早一倍多，代价是 recall 0.88 / fp 0.06）。
=> 新 frontier：**早期预警 + 廉价 recovery** 的组合，可能比「晚检测 + 昂贵 recovery」更省，
且能用误报成本来定价。这是两个源项目都没探索的方向。

## Round 3 — 2026-08-19 — 隔离边界 + 不变量 + 端到端门禁

### 做成了什么

1. `governor/percept.py`：**隔离**。原始 obs 永不进候选代码；`FeatureView` 继承 `Mapping`
   并记账每次 `__getitem__`；`digest()` 是方法不是缓存属性。
   `PrivilegePolicy` 把 critic 和 action 预算分开，默认 action 更严
   （trigger 里的特权是感知问题，可能被传感器替代；action 里的特权不可替代）。
2. `governor/invariant.py`：两条运行时不变量 —— I1 critic 可见 ⟺ 已记录；I2 特权预算按**实际读取**核算。
3. `governor/governed.py`：受治理 rollout。**感知模型即消融梯子** ——
   `estimate = true + N(0, sensor_sd)`，`sensor_sd=0` 自动被标记为 privilege=1。
   消融和被测量的东西是同一段代码，不可能漂移。
4. `governor/gate.py`：配对精确 McNemar + 自动消融曲线。
5. **VERIFY（真仿真）：零特权 bundle 在 held-out 上 +25.0%，p=0.00073**（docs/round3-result.md）。
   19 个测试全绿。**GOAL.md 五条验收全部达成。**

### 什么没成 / 修正

- **Round 1 的结论错了一半。** 「零特权只剩 +13.3% 不显著」是那条手写规则的上限，不是任务上限。
  搜出来的规则零特权下 +25.0% 显著。特权买的是幅度不是有无。已在 docs/round3-result.md 更正。
- critique agent 实测证伪了原设计三处：缓存 digest 循环、dict 子类漏记账、子进程特权复活。
  全部已修 + 加回归测试。
- 沙箱代码执行今晚不做：agent 实测 SBPL 那条 profile 不拦网络，且 10-way 并行下
  critic tick p99 达 108-169ms，500µs 硬预算会作废几乎所有 episode。保留接缝，标注为 interpreted。

### 下一轮种子

campaign 生命周期（多代原子增量 + preregistration + 内容哈希产物），并把 n 提到 200。

## Frontier（Round 3 后更新）

**当前天花板：** 单代、单 bundle、单任务，零特权 +25.0pp 显著。

**下一个 frontier：**
1. **多代演化**：每代只加一对、父规则逐字节冻结（Zetta 的 `preserve_parent_rules_byte_for_byte`）。
   现在只证明了「找得到一条好规则」，没证明「能持续累积」。
2. **持久事件日志 + 离线重放审计**：现在 trace 在内存里，不变量只在线检查。
   落盘后才能做 shadow replay 和事后审计。
3. **早期预警 × 廉价 recovery**：`privileged.grasp_error` 在 step 27 触发（比零特权早一倍），
   recall 0.88 / fp 0.06。用误报成本给早期预警定价，两个源项目都没探索。
4. **LLM proposer**：接口已备好（`Trigger` 就是 proposer 的输出类型）。
   用 dsh 那套 mock server 思路验证，零 API 成本。
5. **跨任务迁移**：stack / pickcan，证明 critic 能 zero-shot 迁移。

## Round 4 — 2026-08-19 — 多代 campaign + 盲对照

### 做成了什么

1. `Bundle` 从单规则改成**规则链** + `Rule`：`assert_atomic_child_of` 强制「每代只加一条、
   父规则逐字节冻结、子代记录父哈希」（Zetta `preserve_parent_rules_byte_for_byte` 的对应物）。
2. `governor/campaign.py`：preregistration（种子划分先冻结再跑）、
   内容哈希产物存储（`runs/*/artifacts/<sha>.json` + append-only index）、代际循环。
   **每代对父代做原子归因**，held-out 只在 campaign 结束时评一次（不当作训练信号）。
3. `paired_gate` 泛化成子代 vs 父代。`ablation_curve` 对链上**每条**规则降级感知。
4. **VERIFY：真跑 4000 个 episode，318 秒。** held-out n=200 上 +22.0pp p<1e-5 零特权。
   gen3 被拒绝（p=0.344），门禁不是橡皮图章。
5. **盲对照**（本轮最重要的一步）：同 recovery 无条件触发 = −6.5pp、破坏 51 个；
   演化 vs 盲发正面 +28.5pp。赢的是 critic 的判断。

### 什么没成 / 只有真跑才暴露

- 不定长 episode 打爆逐步散度分析（第 2 代才出现）。改成末值 hold 填充 + 有效样本掩码。
- 三条规则链超 horizon，robosuite 拒绝 step。horizon→900 且尊重 `done`。
- 两个都加了回归测试；21 个测试全绿。

### 下一轮种子

持久事件日志 + 离线重放审计。现在不变量只在线检查，落盘后才能事后审计和 shadow replay。

## Frontier（Round 4 后更新）

**当前天花板：** 零特权、两条规则、held-out n=200 上 +22.0pp，通过盲对照。

**下一个 frontier：**
1. **持久事件日志 + 离线审计**（Round 5 计划）：把 dsh 的「一切从日志重建」真正落地。
2. **早期预警 × 廉价 recovery**：`privileged.grasp_error` 在 step 27 触发，比零特权早一倍。
   现在 recovery 固定 112 步很贵；早期预警可能配更便宜的修正。用误报成本定价。
3. **跨任务迁移**：stack / pickcan，证明规则链能 zero-shot 迁移（Zetta 只在同族 PnP 内证明过）。
4. **LLM proposer**：`propose_rule` 的契约已经是 `(traces, labels) -> Rule`，
   换 provider 即可。用 dsh 的 mock server 思路验证，零 API 成本。
5. **recovery 也进演化**：现在 recovery 程序是手写常量，只有 trigger 在演化。
   让 proposer 同时搜索恢复程序（阶段与时长）是更大的搜索空间，也是更真的「自演化」。
