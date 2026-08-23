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

## 定位修正(2026-08-22 深夜, 用户裁定): physical-harness 是 agentic OS 本体

zos 不是 OS, 是**驾驶舱**(HRI shell)。它的内脏逐步成为 harness 插件, 用 phase 2
吃掉 governor 的同一套阶梯(适配器 → 变薄 → 删除, 每步带证据收口)。首回合实测支持
这个判断: sim 工具进 zos 第一回合就翻车(run 签名不匹配 executor 的 kwargs 展开),
根因是两个系统各有一套工具/调度/校验约定, 集成靠手写胶水——胶水永远会漏。
模块化的正确形态是共享同一个内核的契约, 而不是两个运行时互相 shell out。

**能力映射草图(待 workflow 深读细化):**

| zos 内脏 | harness 缝 | 备注 |
|---|---|---|
| World(pose/places/objects @50Hz) | graph.scene 首个真 provider | 现在是返回 {} 的 stub |
| brain(qwen3-vl transport) | reasoner.proposer transport | qwen38 本机 sglang 已部署 |
| skill tree 手写 precondition/risk | graph.skill 实测 SkillRecord | heldout_judgement_established 一等字段已备 |
| authority/gate(执行器互斥) | 特权预算的执行器侧同构 | resolve 记账思想的第三个粒度 |
| tools(nav/manip/see) | policy.driver / 技能 provider | 每个进门过配对门禁 |
| verify oracles | 门禁机器的在线半 | 离线配对证据 + 在线 oracle 谓词 |

**M 阶梯(agentic OS 化, 与 R 阶梯并行):**
- M0(已完成, round 80): zos 经 sim.* 工具消费 harness——胶水形态, 证明需求, 也证明胶水不行。
- M1(=R3): reasoner 接真模型(qwen38), round-25 对比重跑。大脑先进内核。
- M2: graph.scene 真 provider(zos World 桥) + zos 工具 precondition 改读 graph.skill。
- M3: agentic 主循环成为 harness workload——task.planner 缝(VLM 分解产出 StageSpec 链,
  round 78 的阶段机就是这个接口), zos 退化为薄驾驶舱。

## M4 + R4(2026-08-23 用户重锚, round 94 起): 稳定运行的系统层, RSI 进系统内部

用户原话重申目标: "一个可以稳定运行的模块 agentic 系统的系统层, 并且可以通过 rsi 去
提升系统的 harness 以及技能的能力"。M0-M3 交付了缝和闭环, 但系统层今天仍是
**一堆可跑脚本**: task_plan.py 跑完即退, campaign 手动挂后台, RSI 在系统旁边而非
系统里面。M4 把它变成常驻系统。

**M4 验收(系统层常驻化):**
1. 一条命令拉起常驻 harness 运行时: boot kernel → 挂载技能目录+实测证据 → 接受任务。
2. 每个任务全程治理: planner → validate → governed rollout → 阶段评分 → 账本;
   **单任务失败不倒系统**(fault 折回 replan 或如实记败, 循环继续)。
3. 会话账本链跨任务连续、重启后可续可验(链头衔接, 不是新开一条)。
4. **RSI 是系统内服务**: campaign 作为一类系统任务被调度(技能提升), 产出的
   SkillRecord 立即进挂载目录供后续任务消费——闭环不出系统。
5. board 可观测运行时会话(不只是封存 campaign)。
6. zos 经同一任务缝提交 brief(吸收路径不变: adapter → 变薄 → 删除)。
7. 稳定性实测: 注错 soak(连续 N 任务混入注入故障)零崩溃、账本零断链。

**R4 验收(meta-RSI, RSI 提升 harness 本体):** 架构/目标函数级变更(如 round 88 三件套
这类)由提案-评测电池-门禁的流程处理: 变更前后在固定 eval battery(demo parity +
stack 三块复现 + 闭环 soak)上配对跑, 无回归才准入——把"人肉 round 纪律"升格为
机器可执行的门禁。round 88/92 的目标函数修复系列就是 R4 的第一批人肉原型。

## GOAL v4 - 主机底座宪章(2026-08-23 用户裁定, round 96 起, 覆盖性目标)

**一句话**: 把项目做成"机器人 agent 的主机底座"——底座上只焊死两样: **跑任务的系统层**
和**做实验攒证据的进化层(RSI)**。其他一切——技能、模型、软件包、机器人、界面——都是
插上来的卡, 随插随拔。

**六个工作流(W1-W6):**

- **W1 盘点再拆解**: 把还焊死在底座里的东西全找出来(写死的任务清单、技能配置、只为
  某任务服务的代码), 改成"插件自己登记、底座只管接口"。**验收: 一块插件都不插,
  底座也能开机、也能全绿跑通自己的测试(纯假件, 不需要装 robosuite)。**
- **W2 装机规矩**: 新插件先跑一条命令体检(接口对不对、行为稳不稳定); 插件声称的能力
  必须过对照实验才算数——**进化层就是验货员, 谁来都得过这关**。
- **W3 界面=dsh, 不做任何新界面**: 原样运行、锁死版本, 经 MCP 对接底座; dsh 代码
  一行不改, 除非发布版有坑挡路才轻 fork 打最小补丁(不改名、不换身份)。旧自建仪表盘
  等新界面功能全对齐后再删(round 95 已执行)。
- **W4 zos 整个退役**: 活儿由 dsh 接管; 审计确认没漏有用的东西后仓库归档、停止维护;
  所有 zos 对接计划取消。**(R10 已执行 2026-08-23: 设计资本审计落 docs/zos-salvage.md,
  zos 仓 README 立碑 + tag `zos-retirement-2026-08-23` 冻结、代码零删除; `gh repo archive`
  属组织管理动作留用户执行。M4#6 作废, M4 计 6/6。真机 ACT 半不搁置——按 v4.2 升为未来
  actuation:real 具身卡的需求规格, 详见 salvage 文档。)**
- **W5 狗粮重装**: 现有资产全按新规矩重新当插件装一遍——堆叠/抓取/放置修复/几何抓取
  是技能插件, qwen 是模型插件, robosuite 是具身插件。**哪个装不上 = 底座有病,
  修底座, 不给插件开后门。**
- **W6 双层测试**: 底座测试(快, 每次改动必跑; 改底座前后各跑一遍, 变差不许合入) +
  插件测试(装了才跑)。

**老规矩不动摇**: 每步提交、实时推送、工作日志(progress.md RECORD)、账本与实验种子纪律。

**终态验收**: 实验室同学拿着新模块来, 照说明书写个插件、体检通过、验货通过, 就能进
主线——底座一行不用改, 所有操作都在 dsh 界面里完成。

### v4.1 收敛细化(2026-08-23 用户裁定, 两层结构 + 执行/进化硬分离)

**两层收敛**:
- **第一层 执行 harness**: 接大任务 → 拆解 → 匹配技能 → 调度执行 → 验证状态 → 失败重排。
  本体只有骨架逻辑: 拆解规则、JSON 传参格式、tool call 约定、执行验证循环(参考 dsh 做法)。
  其余一切(顶层 VLM、导航、SLAM、感知、抓取、RL 动作)都是即插即用的模块和技能。
- **第二层 RSI**: 完全独立, **离线运行**, 把执行 harness 当**实验设备**调用。两种活:
  (a) 冻住技能库和模块, 迭代 harness 自身的配置/参数/规则;
  (b) 仿真里自动做实验学新技能(调参、跑 rollout、看日志曲线), 达标的技能**带着实测
  数据**沉淀进技能库。

**硬规矩(进架构文档和验收)**: 执行真任务时不跑 RSI、不在线试错, 方向单向——
**执行态挂载的技能和配置全部是冻结的存档, 进化态才允许实验。** 对 M4 的修正:
campaign 类 brief 只在进化态会话被接受; 执行态会话的挂载全部来自封存 SkillRecord
与冻结配置, 运行中不可变。

W1-W6 六件事、老规矩、终态验收不变(见上节)。

### v4.2 愿景定稿(2026-08-23 用户原文, 本项目的北极星)

实验室维护一个底座, 机器人在真实环境里执行长链路任务的能力从这个底座上长出来,
而且**能力的每一次增长都有实验证据支撑, 不依赖演示和人的判断**。研究者产出的新模型、
新技能、新算法以插件形式接入, 底座本身不需要为任何一个插件修改。

**第一层 执行 harness**: 接大任务 → 拆成小任务串 → 逐个匹配技能 → 按依赖调度 →
每步验证状态 → 失败重规划。本体只有编排逻辑(拆解规则/传参格式/工具调用/执行验证循环)。
顶层 VLM、导航、建图、感知、抓取、预训练动作控制全部可插拔;
**仿真与真机是同一接口下的两种具身, 都是模块**(修正 round 96 设计的"真机搁置"裁决:
真机=未来 embodiment 卡, zos salvage 文档是它的设计输入); 界面用现成开源控制台走
标准协议。执行真任务只用已入库技能与固化配置, 不在线试错。

**第二层 RSI**: 独立、离线, 把执行 harness 当实验设备。两种实验:
(a) 冻结全部技能模块, 只改 harness 编排配置/参数, 仿真批量执行对比前后;
(b) 仿真反复试验新技能(自动调参/观察执行与日志)到成功率达标。
**两种实验的产出走同一套验证四件套**: ① 同条件与旧版配对比较 ② 与不含判断的对照版
比较(盲孪生) ③ 从未参与调试的测试集终评(held-out) ④ 传感条件变差时的衰减测量
(消融曲线 = sim-to-real 保留度估计)。通过者连同全部实验数据进技能库。

**固定原则(逐条可验)**: 零插件可启动且自测全绿 / 接口挂载时校验、不合格在 mount 报错
而非任务中失败 / 完整配置进内容哈希、配置变=另一个实验身份 / 运行期事件链式日志、
事后不可篡改、重启延续 / 特权信息每次使用被记录、每技能带特权依赖测量 /
两层单向: 进化调用执行做实验, 执行消费进化的沉淀, 执行时刻不反向调用。
