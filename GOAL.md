# GOAL v3 - 从单技能治理到 agentic 骨架

(2026-08-22 重定向, 4090 冷启动后。phase 2 的 GOAL v2 与其 5 条验收已达成并以双策略
parity 收口, 记录在 STATUS.md 与 docs/l2-inventory.md; v2 全文在 git 历史。)

## 背景变化

- 循环已从 Mac 交接到 4090。R0 收口: 本机 venv(mujoco 3.3.7 + robosuite 1.5.2 按 pin,
  MUJOCO_GL=egl)全量测试 161 通过 3 跳过 0 失败; 跳过 = Mac 归档与 BC 权重不在 git, 预期。
- 本机已有完整生态: `Z-Robotics-Lab/go2W_Sim`(Isaac Sim 5.1 Docker + Isaac Lab v2.3.2,
  带传感器 Go2W+PiPER 数字孪生, 46 关节已验证)、`zos`(3.5k 行 agentic runtime, VLM 大脑,
  skill tree 带 authority/risk/precondition 继承)、技能存量(anygrasp 抓取 /
  Z-Navigation-Stack-go2w 导航 / FoundationStereo 感知)、`envs/qwen38`(顶层 VLM)。
- 组织形态: MSR 出论文导向的模块(新技能/后训练 VLM), zlab 维护骨架并集成。
  **集成判据 = provider + 过门禁的证据, 不是 demo。**

## 定位

physical-harness 内核 = zlab 主线 agentic system 的骨架。zos 是第一个消费者(把它的
skill tree 手写 precondition 换成 harness 实测的 SkillRecord), go2W_Sim 是第一个
非 robosuite 具身 provider, qwen38 是第一个真实 reasoner provider。

## 验收(5 条)

1. **内核通用化**: harness/ 核心不再绑定 manipulation 特定词汇(PHASE_HEIGHT、
   grasp_height_offset 等收进显式标注的领域词汇模块或插件); EpisodeSpec 泛化出任务无关核心。
   AST 测试继续强制零插件依赖。**前置: 先补三个证据洞**(beam 路径缺盲孪生门禁、发布不把
   held-out 判定作为一等字段、DEFAULT_PERCEPT_REF 不进内容哈希) —— 多方集成前证据门槛
   必须各路径一致。
2. **Isaac 具身插件**: plugins/embodiment_isaac 实现 embodiment.env 契约对接 go2W_Sim,
   一条命令跑通一个 episode; robosuite 与 Isaac 是同一契约的两个 provider, 换 = 改 mount。
3. **技能电池**: 感知/抓取/导航各包成 provider, 共享子目标判据(round 10 教训:
   不用各任务自己的成功判据), 每个技能有实测基线成功率 + SkillRecord 发布路径。
4. **真实 VLM 接入**: qwen38 经现有 reasoner transport + parse_proposal 接入,
   重跑 round-25 对比(mock vs 确定性搜索 vs 真模型), prompt/response 落内容寻址 store。
5. **双轨证据纪律成文并进预注册**: mujoco 轨保留逐位 parity + 全套统计门禁;
   Isaac 轨为配对初始条件 + 大 n 统计门禁, **不承诺 bit-parity**(PhysX GPU 非位确定),
   审计链降级为记录。哪条结论出自哪轨必须可查, 不许混轨报数。

## 迁移阶梯(每级收口; 2026-08-22 用户裁定: robosuite 先行, Isaac 降为 gated)

- R0(已完成): 4090 冷启动, 测试全绿, 跨机 parity 实测成立。
- R1(已完成, round 77): 三个证据洞 + 词汇拆分, demo 逐位不变。
- R2: 技能电池 robosuite 轨(验收#3 的前半) —— anygrasp 包成 provider 接进骨架,
  抓取技能过门禁闭环。**诚实边界**: MuJoCo 干净渲染下感知技能测的是"接入+门禁机制",
  不是感知本身的真机表现(那要 Isaac RTX 或真机数据)。
- R3: qwen38 接 reasoner transport(验收#4), 重跑 round-25 对比。
- R4: meta-RSI: 骨架架构改动本身走证据门禁(eval 电池上的配对提升), 与技能演化分开记账。
- gate(需要时解锁): Isaac 具身插件(验收#2) —— 触发条件: 需要导航技能电池或房间尺度
  长程场景。robosuite 无法做导航; 导航证据暂由真机(zos + Z-Navigation-Stack-go2w)承担。
  桥接成本侦察报告备用。

## 硬约束(继承 + 新增)

- 不改 RNG 推导与数值路径; mujoco 轨已封存实验的可复现结果不许变。
- Mac 归档(runs/campaign-pj-*)需要拷过来; 跨机 bit-parity 本就不保证(浮点/编译器),
  在 4090 重跑重封存新基线, 与 Mac 数字对比作为信息而非验收。
- RSI 的边界照旧: 它优化判断(何时调用/怎么恢复/能力边界), 不发明能力。
  抬天花板的是 MSR 的新模块, 进门要过盲对照。
- heavy sim 串行; 单机 multiprocessing 是 exec.rollouts 的第一个 provider, 不是它的定义。
