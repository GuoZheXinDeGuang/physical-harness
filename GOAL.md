# GOAL v2 - Backbone Harness for the Agentic Robotics OS

(2026-08-20 重定向。phase 1 的 GOAL 与其 5 条验收已于 round 3 达成并在 rounds 4-53 深化, 记录在 progress.md 与 docs/report.html。)

## 背景

用户的 Agentic Robotics OS 总览分三层, 每层由科研团队实现:
1. Reasoning VLM(Qwen3-VL, 任务分解 / 技能组合 / 运行时验证 / 重规划)。
2. World & Capability Graphs(Scene Graph, Skill Graph, grounded skill binding)。
3. Low-level Policies & Control(VLA / 操作策略 / 技能执行运行时 / 硬件)。

本项目不实现这三层。
本项目是它们插进来的 **backbone harness**:
- 取 deepseek-harness 的 everything-is-a-plugin: 能力接缝(Definition/Provider/Consumer)、配置分层(profile/bundle/patch)、append-only 事件链、运行时不变量。
- 取 Zetta 的 physical RSI: 冻结策略 + 演化 critic/recovery + 配对显著性门禁, 作为骨架上的一个 workload。
- 保留我们自己的特权预算, 并把它从"特征读取记账"上提为"能力解析记账"。

Governor(rounds 1-53)整体成为治理/评测层; 它的证据纪律(preregistration、配对门禁、功效规划、盲发孪生、多区块复现)一条不丢。

## 验收(5 条)

1. **内核零插件依赖**: harness/ 不 import governor/ 或 plugins/, 由 AST 测试强制。
   插件只依赖内核契约(过渡期允许依赖 governor 作为 legacy 库, L2 清除)。
2. **挂载即配置**: profile/bundle/patch 解析为 MountPlan 并进内容哈希; 换 env / policy / executor / graph 是改配置不是改代码; 用 3 任务 x 2 策略的纯配置矩阵证明。
3. **迁移保真(parity)**: 走 kernel 路径重跑已封存的现门禁 campaign, 提升规则的 canonical 与 dev 门禁数字与 runs/campaign-pj-* 存档逐位一致。
4. **RSI 成为 workload 插件**: 每条被提升的技能以 SkillRecord 发布进 graph.skill 契约 - 前置条件=触发器, 效果=实测 delta, 失败模式=broken 计数, 能力边界=特权声明+消融曲线, 判断=对盲发孪生的 held-out 对比。
5. **全部测试绿**: 91 项 phase-1 测试迁移存活 + 内核新测试; "可见即已记录"、canonical 字段完整性、特权记账在内核层各有攻击测试。

## 迁移阶梯(每级都以 parity 收口)

- **L0(当前)**: 内核 + 适配器插件; EpisodeSpec 携带 provider ref 字符串(spawn 安全), 旧路径缺省不变。
- **L1**: 逐模块把实现移入插件, governor/ 变薄, 每步 parity。
- **L2**: 删除 governor 命名空间; 插件间零残留依赖。

## 硬约束

- 不改 RNG 推导与数值路径; 迁移不允许改变任何已封存实验的可复现结果。
- heavy sim 串行; 单机 multiprocessing 是 exec.rollouts 的第一个 provider, 不是它的定义。
- 零外部 API 仍是参考路径; reasoner 接真实模型是 gate, 等用户提供 key 与选型。
