# STATUS

**Goal:** 见 GOAL.md — Mac 上真跑仿真的具身 harness：冻结策略 + 演化 critic/recovery + 特权预算。
**Mode:** convergent（打 GOAL.md 的 5 条验收）→ 之后 evolving。
**Round:** 2 完成（特征契约 + 确定性环境 + 自动触发器搜索）
**Updated:** 2026-08-19

## 已确立的事实（实测，不要重新验证）

- 仿真底座：mujoco 3.3.7 + robosuite 1.5.2，10 worker = 212 episodes/min
- 可复现性：必须 `suite.make(seed=N)`，全局 np 种子无效
- 难度：感知噪声 sd=0.020 → 基线 50%（sd=0.025 → 37.5%）
- 失败可零特权检测：finger_gap，EOD 在 step 60，step 66 达 4.2σ
- **上限：一对手写 critic+recovery = 50%→100%（p<1e-6）；但去特权后只剩 +13.3% 且不显著**

## 现在在哪

- [x] 环境可行性 + 难度标定 + 上限验证（docs/ 四篇）
- [x] governor/features.py 特征契约（6 个特征，名字空间即声明，未知特征直接拒绝）
- [x] governor/env.py 确定性环境 + 冻结策略；4 个可复现性回归测试绿
- [x] governor/search.py 自动 EOD + 触发器搜索；已验证优于人手
- [~] 架构设计 workflow wf_585c9df5-4ac：7 map 完成，5 设计 agent 运行中
- [ ] governed rollout + recovery 执行器
- [ ] 配对显著性门禁 + 特权消融自动化
- [ ] 第一次端到端自动演化 campaign

## 下一步

把搜索出来的触发器接上 recovery，跑配对门禁，端到端复现 round-1 手写实验的数字。
即：search → trigger → governed rollout → paired McNemar → 特权消融曲线。
事件日志和不变量等 workflow 的 schema 设计返回后再落。

## 阻塞

无。

## 不要重做的事

- 不要试图跑 LIBERO / RoboCasa：flash-attn 仅 linux_x86_64 + 需 CUDA/EGL。已确认不可行。
- 不要用 mujoco>=3.4 配 robosuite 1.5.2：`MjData.qM` 改名 `M`。pin 3.3.7。
- 不要在 mac 上设 MUJOCO_GL=osmesa：非法值直接抛。无头不设。
- 不要用 `np.random.seed()` 给 robosuite 播种：环境自持 RNG，配对门禁会静默失效。
- 不要用 episode 末尾的统计量去定 critic 阈值：必须走 EOD 扫描。手定第一版只触发 1/60。
- 不要只给 critic 算特权预算：recovery 的感知同样要算，否则结论是假的。
