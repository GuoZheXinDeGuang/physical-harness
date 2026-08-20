# Governor - 目标

## 名字

机械调速器（centrifugal governor）：一个持续以机器自身频率运行、调节它并不直接控制的机器的反馈装置。
这正是本项目 Loop 1 的形态 —— 在冻结策略之外，以动作频率做治理。
Zetta 论文自己用的词就是 "action-frequency governance"。

## 一句话

一个**具身 agent harness**：把基础策略当黑盒冻结，在它外面演化出运行时 critic 和 recovery 技能，
全程记录在可重建的 episode 事件日志上，并且**强制声明每条 critic 依赖了多少特权信息**。

## 来源与取舍

| 来自 DeepSeek Harness | 来自 Zetta | 我们自己的 |
|---|---|---|
| append-only 事件日志 = 唯一真相源 | 冻结策略 + 演化 critic/recovery | **特权预算（privilege budget）** |
| 运行时不变量断言（可见 ⟺ 已记录） | 三时间尺度解耦循环 | 零外部 API 也能跑完整循环 |
| 能力接缝 Definition/Provider/Consumer | dev/held-out 种子隔离 + 配对显著性门禁 | sim-to-real gap 量化为一个数字 |
| 沙箱 fail-closed + 隔离代码运行时 | 每代只加一条、父规则冻结 | Mac 原生、CPU-only、无 GPU |
| 分层配置组合 | 内容哈希产物 + preregistration | |

**明确不抄的：**
- Zetta 的 critic 只是「单特征 + 比较符 + 阈值」的 DSL，真代码通路（tool_plugin）是关掉的。我们从第一天就要能跑沙箱代码。
- Zetta 的 critic 直接读 `privileged.*.residual_to_success` 这类仿真器内部量，真机上不存在，且它自己只用散文规定「不许当隐藏控制」，没有机制。我们用类型系统 + 门禁强制。
- dsh 的 Cordis 全套仪式（fiber、effect 树、typert RPC）在 Python 里不值这个复杂度，只取接缝模式本身。

## 硬约束（已实测，非假设）

- macOS arm64 / 18 核 / 64GB / **无 NVIDIA GPU**
- MuJoCo 3.3.7 原生 arm64：简单场景 352k steps/s
- robosuite 1.5.2 + Panda 无头跑通：单进程 96 control steps/s
- **10 worker 并行 = 212 episodes/min**（对照：Zetta 论文 8×A100 上 35.1 episodes/min）
- 演化循环**必须**能在零外部 API 调用下跑完；LLM proposer 是可插拔 provider，不是必需品

## 验收（这一轮的地板，不是天花板）

1. `governor run` 能端到端跑完一次真实演化 campaign，不是 mock
2. 至少一个任务上，**held-out 种子**成功率相对冻结基线有统计显著提升
3. 每条被提升的 critic 都带一份声明的特权预算，并报告零特权消融后的成功率
4. 重建不变量真的会炸：有一个测试故意违反它并被抓住
5. 全部测试绿；任何「验证」都必须跑真仿真，不接受 mock 代替

## 模式

**先收敛后演化。** 上面 5 条是地板。达成后进入 frontier 轮：更强的 critic 表达力、
真正的 BC 基础策略（让失败更像真实 VLA 的复合误差）、更深的 sim-to-real 消融、更多任务族。
