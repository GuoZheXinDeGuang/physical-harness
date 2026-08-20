# STATUS

**Goal:** 见 GOAL.md — Mac 上真跑仿真的具身 harness：冻结策略 + 演化 critic/recovery + 特权预算。
**Mode:** **evolving**（GOAL.md 五条验收已于 Round 3 全部达成，见 docs/round3-result.md）
**Round:** 26 完成
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
- [x] Round 8 recovery-search campaign 实跑；发现贪心收敛更差；修掉半样本内的 recovery 门禁
- [x] Round 9 干净切分下 recovery 每代被正确拒绝；收敛回 +27.5pp；泄漏代价量化为 4.5pp
- [x] Round 10 跨任务零退化迁移（stack +28.5% / pickcan +24.0%）；特权触发器在 pickcan 上静默失效（0 触发）
- [x] Round 11 盲对照在修正后代码上重测（+31.5pp 正面对比）；出总报告 `docs/report.html`
      （已发布为 artifact: https://claude.ai/code/artifact/a1cae211-14e5-4de8-b0f1-6024a3cc532b ）
- [x] Round 12→13 行为克隆**跑通了**（h=256/3000ep，闭环 47.5%）—— round 12「需要超出一轮」的判断是错的
- [x] Round 13 策略接缝 + 交还契约；合并成一条 rollout 实现；三个迁移数字逐位复现
- [x] Round 13 关键负面结果：克隆策略上搜索零候选 —— **归因只对了一半，见 round 14**
- [x] **Round 14 验收达成**：克隆策略（没人设计的失败）上找到零特权 critic，
      held-out **35.5% → 47.5%，+12.0pp，p=1e-5**。挡路的是两个我自己设的约束：
      触发器语言是瞬时的（加了运行时归约 min/max/range）、生成阶段 2σ 门槛未标定（降到 1.0 并标为启发式）。
      详见 docs/round14-expressiveness.md
- [x] Round 15 新语言下重跑 campaign：**+29.0pp**（旧语言 +27.5pp），58 修复 / 0 破坏；
      第 3 代因**主动有害**被拒（−21.7%，修 6 破 32）；补 11 个回归测试（共 41 绿）；报告更新重发
- [x] Round 16 克隆策略完整 campaign：**只长出一条规则**（held-out +12.0pp，与手跑逐位一致）。
      原因查明：54 个残余失败里 **42 个是「检测到了但修不好」**，只有 12 个漏检。
      **瓶颈是修复不是检测。** 详见 docs/round16-repair-bottleneck.md
- [x] Round 17 critic 改为只在策略拥有控制权时求值（`arm_after` = 策略时钟）。
      脚本策略 **+22.5pp**（旧语义 +29.0pp）。查明差距原因：**门禁的一次第二类错误** ——
      被拒的 gen2 候选在 held-out 上值 +6.5pp，p=0.065 vs α=0.05。
      **不提升它**（那是用测试集选择）。教训是统计功效：后期世代需要更多 dev 种子。
      详见 docs/round17-type-ii.md
- [x] Round 18 **按世代递增样本量**（精确 McNemar 功效计算，从上一代残余率定，非 optional stopping）。
      60→90→112，长出**两条规则**（固定样本量只有一条），
      held-out（**全新区块**）**+17.5pp**，且总成本 262 < 360 个种子。
      详见 docs/round18-power.md
- [x] Round 19 recovery 库（4 种不同**形状**）：**假设被否定**，现役 regrasp 赢（+11.1% vs 7.8/4.4/2.2）。
      查明真正的天花板：42 个修不好的失败里 39 个够得着物体但抓不牢，
      **即使完美感知也只到 15.6%** —— 差距在**抓取原语本身**，不在接近形状也不在感知。
      详见 docs/round19-repair-ceiling.md
- [x] Round 20 报告更新重发：新增第 0 节**数字轨迹**（+50.0 → +17.5，每次下修对应拆掉一个机制）、
      错误清单 8 → 11 条、局限一节以「修复原语天花板」为首条
- [x] Round 21 接触反馈原语（ServoDescend / ServoClose）+ 段式 RecoveryActor。
      **确认性测试 n=450：+1.8%，p=0.20，不采纳。**
      而且查出**前提是错的**：感知只扰动 xy，z 恒为真值，ServoDescend 消除的误差不存在。
      详见 docs/round21-wrong-premise.md
- [x] Round 22 横向接触搜索原语。**先验证前提**（平面误差 1.24cm，与失配偏移相关 +0.67，成立），
      造、测：选择切片 +8.9% **比现役 +11.1% 还差**。查出实现 bug（固定步数 vs settle 判据）并修好，
      修好后搜索启动 14/60 次、**成功 0/14**。
      **连续两轮抬天花板失败，大幅加强了 round 19 的结论。** 详见 docs/round22-ceiling-holds.md
- [x] Round 23 报告更新重发：天花板从「局限清单里的一条」升格为**独立一节（第 8 节）**，
      含两次失败尝试的完整证据；错误清单 11 → 13 条
- [x] Round 24 beam 搜索（三路划分：dev 门禁 / 选择区块 / held-out）。
      **负面但有信息量**：三条分支全部止步深度 1，选择区块选出的就是贪心 top-1 会选的那条。
      串起来看：链的深度受限于**修复天花板**，不是搜索贪心。**这个方向关闭。**
      held-out（全新区块）+22.0pp。详见 docs/round24-beam.md
- [x] Round 25 proposer 接缝 + LLM provider（零 API，脚本化 stand-in 验证，74 测试绿）。
      **端到端跑通，并暴露一个问题**：mock 模型的天真挑法（取最强分离）在 held-out 上
      **+31.5pp 打赢确定性搜索的 +26.0pp** —— 差距来自我手写的 earliness 权重 0.25，
      **它从没被标定过**。详见 docs/round25-llm-proposer.md
- [x] Round 26 扫 earliness 权重（三路划分）：**W=0 最优**，held-out +23.5pp vs 原默认 0.25 的 +18.5pp。
      **权重值 −5.0pp**，已改默认并加回归测试。机制查明：它编码的假设在 round 17 改成
      控制权归属之后就不成立了（recovery 拥有自己的步数，实测总步数 209 / horizon 900，从未截断）。
      并更正了 round 3 的一处说法。详见 docs/round26-earliness.md
- [ ] 持久 episode 事件日志（行日志 + 列存），当前 trace 只在内存
- [ ] LLM proposer（用 mock server 验证，零 API 成本）
- [ ] 多任务（stack / pickcan）+ 跨任务迁移

## 下一步

Round 27：**扫 `fp_penalty`**（同样手调、同样没标定过，现在已暴露成参数）。
同一套三路划分。注意区块预算：已用掉的选择区块 2400-2549 / 2600-2749，
held-out 1000-1199 / 1200-1399 / 1400-1599 / 1600-1799 / 1800-1999。下一个用 2000+ 之外的新段。

之后：**重跑一次完整 campaign 拿新默认值下的数字**，并更新报告
（报告里的 +17.5pp 是 W=0.25 时代的）。

**已关闭的方向：** 修复原语（round 21/22）、beam 搜索（round 24）。

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
- 不要在搜索过的种子上给同一个改动过门禁：round 8 实测同一改动在半样本内切分上 p=0.039（过），
  在干净切分上 p=0.096（不过）。
- 不要用 `privileged.object_z` 当 critic：它就是成功判据本身，同义反复不是预警。
- 不要假设瞬时触发器够用：没有固定日程的策略，失败在时间上错位，逐步散度会把信号抹平。
  用运行时归约（min/max/range）。
- 不要在生成阶段设严格的 σ 门槛：那会在候选被评判之前静默压掉它们。生成宽松、验证严格。
- 不要对所有世代用固定的 dev 样本量：残余效应量随世代变小，后期会系统性功效不足。
  用 `scale_dev_by_power=True`（精确 McNemar 功效计算）。
- **手调常数会随设计漂移而失效且不报错**：earliness 权重在 round 2 合理，
  round 17 重构后有害，中间零信号。抓住它的是一个不共享我假设的 proposer。
- 不要把手调的目标函数权重当成已验证的：round 25 实测 earliness 权重 0.25 值 −5.5pp。
- 不要用「模型说的」缩短校验路径：proposer 输出是不可信输入，边界是 schema 不是作者。
- **前提正确也不代表干预有效**：round 22 前提验证过、实现修对了，横向搜索启动 14 次成功 0 次。
- 判断「握住了没有」不要用固定步数：前一段可能让夹爪张着，固定步数会把还在合拢的读成握住。
  用 settle 判据（开度不再变化）。
- **不要在造一个原语之前不验证它要解决的问题存在**：round 21 花了 15 分钟造 ServoDescend，
  而验证前提（`percept - true` 的 z 分量）只要 30 秒 —— 它恒为 0。
- 不要把小样本的「零破坏」当成证据：servo 在 n=90 上是修 3 破 0，n=450 上是修 19 破 11。
- 不要指望更多恢复策略能补上修复能力的差距：round 19 实测四种形状，现役最好，
  且完美感知下也只到 15.6%。天花板在原语能力，不在搜索。
- 不要把样本量规划放在残余测量之后：那样搜索和门禁会用不同的种群，
  候选在它没被拟合过的种群上受审。规划先行，三者共用一个切片。
- 不要事后用 held-out 去救一个被门禁拒绝的候选：那是用测试集做选择。
  round 17 花了一次 held-out 才看到那是第二类错误，那个区块就为这个问题烧掉了。
- 不要在混合了恢复步的轨迹上直接搜索：第二代之后轨迹含脚本恢复驱动的步，
  那里的散度反映恢复动作的行为不是策略的失败（round 16 的 gen2 武装在 t=129）。
- 不要在「检测到但修不好」的失败集上加检测器：round 16 实测 42/54 属于这一类，
  门禁会正确地拒绝，但那是在浪费 rollout。
- 不要让交还后的策略回到「接近」阶段：它会撤销刚做完的恢复（实测 −2.0pp，修 3 破 7）。
- 不要在改了 rollout 语义之后沿用旧数字：round 13 改交还语义时 +27.5 变 +14.0，必须重新赚。
- 不要假设「每步都通过门禁」等于「终点更好」：round 8 每步都合理，终点比 round 5 低 4.5pp。
  （round 9 查明触发它的是泄漏的门禁；贪心的结构性风险仍然成立，但那次是泄漏造成的。）
- 一个泄漏的门禁不表现为「结果变差」，而是「结果变好然后提前收敛」——
  代价藏在没长出来的那条规则里，单看那一代看不见。
- 不要用各任务自己的成功判据做迁移实验：冻结策略在 Stack/PickPlace 上是 0%，
  那测的是策略缺的技能不是 critic。用共享子目标（抓起并举离桌面）。
- 特权触发器的阈值是关于世界的事实（0.8215 是桌高），换场景会**静默**变成 no-op，不报错。
- 不要今晚做沙箱代码执行：SBPL `(allow default)(deny file-write*)` 不拦网络（实测），
  且 10-way 并行下 critic tick p99 = 108-169ms，500µs 硬预算会作废几乎全部 episode。
