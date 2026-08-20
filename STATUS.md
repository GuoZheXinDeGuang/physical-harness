# STATUS

**Goal:** 见 GOAL.md — Mac 上真跑仿真的具身 harness：冻结策略 + 演化 critic/recovery + 特权预算。
**Mode:** **evolving**（GOAL.md 五条验收已于 Round 3 全部达成，见 docs/round3-result.md）
**Round:** 4 完成
**Updated:** 2026-08-19

## 已达成（不要重新验证）

五条验收全绿（Round 3）。Round 4 加上多代 campaign：

- **held-out n=200：48.5% → 70.5%，+22.0pp，p<1e-5，零特权**（docs/round4-campaign.md）
- 代际累积成立：gen1 +9.2%、gen2 +7.5%（均 vs 父代，原子归因），gen3 p=0.344 被**拒绝**
- **盲对照通过**：同样 recovery 无条件触发只有 −6.5pp（破坏 51 个），
  演化 vs 盲发正面对比 +28.5pp p<1e-5 —— 赢的是判断不是时间
- n=200 下消融曲线修正了 round 1：sd=0.010 的板载感知已能拿到真值的全部收益

## 已确立的事实（实测）

- 仿真底座 mujoco 3.3.7 + robosuite 1.5.2，10 worker = 212 episodes/min
- 可复现性必须走 `suite.make(seed=N)`；全局 np 种子无效（有回归测试）
- 难度：感知噪声 sd=0.020 → 基线 50%
- 自动搜索 > 人手：`gripper_effort` 比手写的 `finger_gap` 早 6 步触发，把 p=0.057 推到 0.00073
- **特权买到的是幅度（+40% vs +25%），不是有无效果** —— 修正了 round 1 的结论
- 子进程会复活特权特征，但边界是 view 不是 registry，隔离已中和（有回归测试）

## 现在在哪

- [x] Round 1 可行性 + 标定 + 上限
- [x] Round 2 特征契约 + 确定性环境 + 自动搜索
- [x] Round 3 隔离边界 + 运行时不变量 + 受治理 rollout + 配对门禁 + 消融曲线
- [x] Round 4 campaign 生命周期：多代原子增量 + preregistration + 内容哈希产物 + 盲对照
- [ ] 持久 episode 事件日志（行日志 + 列存），当前 trace 只在内存
- [ ] LLM proposer（用 mock server 验证，零 API 成本）
- [ ] 多任务（stack / pickcan）+ 跨任务迁移

## 下一步

Round 5：持久 episode 事件日志（行日志 + 列存）+ 离线重放审计。
现在 trace 只在内存，不变量只在线检查；落盘后才能做 shadow replay 和事后审计，
这也是 dsh 那套「一切从日志重建」真正落地的部分。

## 阻塞

无。

## 不要重做的事

- 不要试图跑 LIBERO / RoboCasa：flash-attn 仅 linux_x86_64 + 需 CUDA/EGL。
- 不要用 mujoco>=3.4 配 robosuite 1.5.2（`qM`→`M`）。pin 3.3.7。
- 不要在 mac 上设 MUJOCO_GL=osmesa。
- 不要用 `np.random.seed()` 给 robosuite 播种。
- 不要把 view digest 缓存成属性：那样断言是 `f(x)==f(x)`，永不失败。
- 不要让 `FeatureView` 继承 `dict`：`.get()`/`{**v}` 会绕过 `__getitem__`，特权读取不被记账。
- 不要让 critic 或 recovery 碰原始 obs：边界是 view。真实泄漏就发生在 recovery 的感知里。
- 不要用 n=60 报迁移分：CI 宽 1.20（实测），至少 200。round 1 的「零特权不显著」就是 n 不够。
- 不要忘了盲对照：受治理 episode 比对照多跑控制步，必须证明赢的是判断不是时间。
- 不要假设 episode 定长：recovery 会插入阶段，第 2 代种群混着 100 步和 212 步。
- 不要让规则链超 horizon：robosuite 到点拒绝 step。horizon=900 且尊重 `done`。
- 不要今晚做沙箱代码执行：SBPL `(allow default)(deny file-write*)` 不拦网络（实测），
  且 10-way 并行下 critic tick p99 = 108-169ms，500µs 硬预算会作废几乎全部 episode。
