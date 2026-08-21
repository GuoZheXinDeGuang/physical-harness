# Backbone Harness 架构

一句话: dsh 式插件内核承载 Zetta 式 physical RSI, 三层科研板块以契约接入, 一切能改变结论的东西(挂载、解析、特权、参数)都进内容哈希。

## 三层 OS 与契约的映射

| OS 板块 | 骨架契约(harness/contracts.py) | 第一个 provider |
|---|---|---|
| 1 Reasoning VLM | reasoner.proposer(Reasoner) | plugins/reasoner(SearchProposer 适配; 真模型走同一 transport, gated) |
| 2 Scene Graph | graph.scene(SceneGraph) | plugins/graphs.StaticSceneGraph(占位, 等图团队) |
| 2 Skill Graph | graph.skill(SkillGraph) | plugins/graphs.InMemorySkillGraph(内容寻址) |
| 3 具身/环境 | embodiment.env(EnvProvider), embodiment.ground_truth(privileged) | plugins/embodiment_robosuite |
| 3 策略/技能 | policy.driver(PolicyFactory) | plugins/policies(scripted / bc 适配) |
| 感知 | percept.model(PerceptModel) | plugins/embodiment_robosuite/percept.OnboardPercept(L1 rung 1 已迁入) |
| 执行织物 | exec.rollouts(RolloutExecutor) | harness/executor.LocalPoolExecutor(将来 Ray/远程同一契约) |

RSI workload(plugins/rsi)是 OS 主循环里 Verify -> Adapt 那条边的严格化:
它消费上述能力, 产出**带测量的技能**写回 Skill Graph - 图里的 failure modes 与 capability boundaries 不再是标注, 是配对实验的输出。

## 内核(harness/)

- capability.py: Definition(name, contract, privileged)。契约必须是 runtime_checkable Protocol。
- kernel.py: provide/resolve/mount。**每次 resolve 都被记账**(消费者、provider ref、是否特权), 特权解析吃预算 - FeatureView 的思想上提到系统层。
- config.py: Profile/Bundle/Patch -> resolve_plan -> MountPlan(带来源出处), canonical()+sha() 进内容哈希; round 29 的教训(能改变结论的常数必须可审计)在这里变成结构。
- events.py: 链式承诺的 SessionLog(内核事件与 workload 事件同一条链的构造)。
- registry.py: "module:attr" 字符串加载 provider 工厂。**provider 身份以字符串随 EpisodeSpec 传递**, 因为模块全局 hook 不能活过 multiprocessing spawn(phase 1 实测过并写进过文档), 而字符串可 pickle、可进哈希、可审计。
- definitions.py: 能力清单(上表)。
- executor.py: 本地进程池 provider。

## L0 迁移方式(适配器, 不动数值)

governor.env.make_env / governor.policy.make_driver 变为派发点:
spec 携带 env_provider / policy_provider ref 时经 registry 加载 provider, 缺省走原路径。
加字段只加在 dataclass 末尾(phase 1 的字段顺序教训)。
Preregistration 增加同名可选字段并由 _specs 下传; 由此 prereg sha 会变, 这是预期且诚实的(挂载进哈希)。
parity 协议: scripts/parity_check.py 读取 runs/campaign-pj-* 的存档 preregistration, 经 kernel 路径重跑, 比对每代规则 canonical + bundle sha、dev/blind 门禁与 held-out 的全部配对字段。
已验证: 脚本与克隆两个策略均四组 PASS(round 54/55)。

## L1 进度

- rung 3(round 58b): **事件链合一**。链原语(chain_start/chain_step)只在 harness.events
  存在一份, governor.episode_log 复用同一实现(种子不同, 数学逐位不变 -- 有 golden 值测试,
  归档的 episode log 仍可审计)。新增 `Kernel.note()`: workload 把 campaign 完成事件
  (prereg sha / 规则 / 技能摘要 / held-out)写进**同一条**内核会话链 --
  挂载、解析、campaign 结果如今在一本可验证的账里, note() 是证据不是控制流。
- rung 2(round 57): **executor 接管全部 rollout 执行**。governor 里 6 处散落的
  `multiprocessing.Pool` 块(gate / campaign / parallel / recovery_search / beam / demos)
  换成 `executor.map(fn, items, workers=...)`; 注入方式是**显式关键字参数**贯穿调用链
  (paired_gate / ablation_curve / run_campaign / rollout_many / _measure / _rate 均加
  `executor=None`), 缺省回落 `governor.parallel.default_executor()`(与原 Pool.map 逐位同义)。
  不用模块全局(round 29 的教训); executor 在父进程创建 pool, 无 spawn 问题。
  workload 从 kernel 解析 `exec.rollouts` 注入 -- **换分布式后端 = 换一个 mount**。
- rung 1(round 56): 恢复侧感知 `_percept_object` 的实现移入 embodiment 插件,
  governor 只留派发点与命名默认 `DEFAULT_PERCEPT_REF`。
  策略自身的 t=0 感知(`FrozenPolicy.observe_once`)是冻结策略本体的一部分, 不属于可挂载服务, 不迁。
  已知 caveat: `DEFAULT_PERCEPT_REF` 常量本身不进内容哈希(直接构造 EpisodeSpec 的老路径用它);
  kernel 挂载的运行会把解析出的 ref 盖进 preregistration, 那条路径是进哈希的。

## L0 已知限制(有意, 待 L1 清除)

- worker 里 provider 由裸 ref 字符串重建, Mount params 到不了 worker; workload 在 params 非空时拒绝启动(fail loud), L1 把 params 编进 spec。
- governor/demos.py 的演示采集不携带 provider ref, 永远走 legacy 路径; 采集是训练数据管线不是 campaign 路径, L1 一并迁移。
- provider 在 make_env/make_driver 里每 episode 重建一次; 对无状态适配器免费, L1 引入 per-worker 缓存。

## 不变量(从 phase 1 原样上提)

- 可见即已记录: 能力解析、挂载、特权使用全部落链式日志, 篡改可被审计抓住。
- canonical 完整性: 进哈希的 dataclass 字段必须全部出现在 canonical()(parent_sha 类自引用除外), 有常驻测试。
- 特权预算同时覆盖特征读取(FeatureView)与能力解析(Kernel)。
- 头条数字至少三区块; 判断主张走盲发孪生的 held-out 对比。
