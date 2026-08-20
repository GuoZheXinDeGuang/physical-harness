# STATUS

**Goal:** 见 GOAL.md — Mac 上真跑仿真的具身 harness：冻结策略 + 演化 critic/recovery + 特权预算。
**Mode:** **evolving**（GOAL.md 五条验收已于 Round 3 全部达成，见 docs/round3-result.md）
**Round:** 7 完成（campaign 跨轮运行中）
**Updated:** 2026-08-19

## 已达成（不要重新验证）

五条验收全绿（Round 3）。Round 4 加上多代 campaign：

- **held-out n=200：48.5% → 76.0%，+27.5pp，p<1e-5，零特权，每档消融 0 破坏**
  （docs/round5-log-and-audit.md；round 4 的 +22.0pp 是 off-by-one 下的数字，已作废）
- 代际累积成立：gen1 +12.5%、gen2 +10.8%（均 vs 父代、0 破坏），gen3 +0.0% 被**拒绝**
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
- [x] Round 5 持久事件日志 + 链式承诺 + 离线审计 + shadow replay（抓到 step 索引 off-by-one）
- [x] Round 6 recovery 程序进搜索空间（坐标下降）+ 门禁化采纳；实测被门禁拒绝一次
- [x] Round 7 触发器样本外预筛（抓到 shrinkage +0.45 的过拟合候选）
- [ ] 持久 episode 事件日志（行日志 + 列存），当前 trace 只在内存
- [ ] LLM proposer（用 mock server 验证，零 API 成本）
- [ ] 多任务（stack / pickcan）+ 跨任务迁移

## 下一步

**后台有一个 campaign 在跑**（`runs/campaign-recovery-search`，`search_recovery=True`，
约 40 分钟，日志 `/tmp/campaign_rs.log`）。下一轮第一件事是收它的结果并记录。

之后：`screen_triggers=True` + `search_recovery=True` 一起跑一次，
再往前是跨任务迁移（stack / pickcan）。

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
- 不要混用步索引约定：trace / search / shadow / governed_rollout 全部 **0-based**。
  1-based 会让触发器早武装一步，单元测试和成功率都抓不到（数字还更好看）。
- 不要在改了触发语义之后沿用旧数字：必须重跑 campaign 重新赚。
- 不要在看到 p=0.096 之后加大 held-out 样本量重测：那是 p-hacking。
- 不要用 shadow replay 预筛 recovery 候选：换修复动作就换轨迹，录像不再描述它。只能真跑。
- 不要今晚做沙箱代码执行：SBPL `(allow default)(deny file-write*)` 不拦网络（实测），
  且 10-way 并行下 critic tick p99 = 108-169ms，500µs 硬预算会作废几乎全部 episode。
