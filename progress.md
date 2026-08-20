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
