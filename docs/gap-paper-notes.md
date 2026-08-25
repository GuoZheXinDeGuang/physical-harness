# GaP (Graph-as-Policy) 深读笔记 — arXiv 2607.05369

单代理聚焦深读（2026-08-25 夜）。原文: https://arxiv.org/abs/2607.05369 ·
项目页 graph-robots.github.io/gap · 代码 github.com/graph-robots/graph-as-policy。

GaP: 多代理编码 harness 把 VA(变差自动化)任务编译成**类型化、静态验证的计算图**
(感知/规划/控制节点, 来自 51 技能的 MORSL 库), 用 Isaac 仿真自学习迭代精化图,
最终**无代理**地在边缘设备上冻结执行。Make Popcorn: 初始图 33% → 自学习 8 轮 94%
(仿真) / 90% (真机 18/20)。LIBERO-PRO mixed_all: π0.5=0.20, GaP=0.97。

## 我们已经收敛到的(相互印证, 不用抄)

- 类型化图 + 静态验证 = 我们的 validate_plan + doctor + runtime_checkable 契约。
  GaP 消融同款结论: 去掉图/验证 → 成功率归零("裸 Python 脚本必死于接口错配")
  ——正是我们"挂错形状 mount 时报错"的实证孪生。
- 冻结执行 = 我们的两态铁律(进化学、执行只跑冻结图, GaP 的 agent-free 边缘执行同构)。
- 仿真自学习 = 我们的 RSI campaign; 技能库带类型元数据 = 我们的 vault/manifest。
- 单 LLM 合并多角色 → 归零(静态验证全挂)——多代理分工(编排/技能/验证)有实证背书。

## 值得偷的(按行动优先级)

1. **图编辑作为 RSI 候选(最大的一个)**: GaP 自学习变异的是**图本身**——
   (a) 节点替换(GraspGen 换成 GraspGen+OBB 混合); (b) 节点参数微调(放置偏移);
   (c) 感知提示词调整(按把手抓锅)。我们的 RSI 只演化 trigger/recovery 规则;
   **把"图编辑"(换某节点的技能绑定/调节点参数)作为 campaign 候选**, 走同一套配对
   门禁/盲孪生/held-out——直接回应 build-stack 饱和墙: 下一杠杆可能不是再学规则,
   而是**换掉该节点的策略绑定**。证据机器零新统计, 候选空间换了个轴。
2. **数据边 + 谓词控制边进 plan schema**: GaP 图有两类边——数据边(生产者输出→
   消费者输入, 公共类型系统 Se3Pose/PointCloud/Mask/Trajectory 校验)和控制边
   (带节点输出谓词的条件分支, 如"视觉插入候选失败→1×1cm² 2mm 步进接触搜索"的
   **预编 fallback 分支**)。我们的图只有控制流+松散 args; 给 NODE_KINDS 加类型化
   端口(validate 时校验管路)和 fallback 谓词边 = "不用 replan 的 replan"(预案分支),
   与 M7 回合内 runner 正交可组合。
3. **信念空间实例采样**: 任务实例从 𝓑(物体位姿/初态/运动学配置的分布)采样——
   这正是几何抓取卡缺的难度轴(我们只有 percept-noise 一维)。标定/campaign 的
   difficulty axis 应升级为 belief-space 采样(位姿变差优先)。
4. **逐节点前后状态注册做失败归因**: GaP 在每个排练节点前后注册机器人+物体状态,
   diff 推断动作结局("gripper 未接触 pan")。我们的 first-death 归因是布尔级;
   给 runtime_events/oracle 加**状态 diff 摘要** = 给 reasoner 更肥的提案上下文。
5. **复合技能**: composite skill = 可物化的子图(混合抓取器)。vault 可挂
   composite 节点(子图引用), 血统边天然表达组成。
6. 收敛节奏参考: N 并行实例 → 失败分析 → LLM 图更新 → 迭代, 按"编辑类别"
   着色追踪(我们的 rounds/编辑记录可借鉴此可视化)。

## 与 agent-loop-design 的钩子

Rung C/D(SkillRecord 先验 + mission 图一等化)可直接引用本文机制; Rung E 的
LLM 编排席位 = GaP 的 Orchestration Agent, 我们的 doctor = 其 Validation Agent
的常驻化。差异守住: GaP 的成功信号是仿真判定, 我们额外要求配对证据与封存——
GaP 没有 held-out/盲孪生纪律, 这是我们的加严, 不放松。
