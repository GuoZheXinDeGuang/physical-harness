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
