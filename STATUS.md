# STATUS

**Goal:** 主机底座宪章(GOAL.md v4.2): 执行 harness + 离线 RSI 两层, 其余皆插拔卡。(phase 1/2 记录不动, v2 全文在 git 历史)
**Mode:** **evolving**
**Round:** 96 完成(**主机底座宪章 W1-W6 全兑现, 终判 PASS**): 裸机全绿(隔离道 412)、
manifest 自注册(卡=目录, base sha 逐位不变)、体检+验货(含 task_binding refs 校验)、
两态铁律上链、dsh 驾驶舱(PH 上牌, MCP 工具 11ms 直达, 终态彩排双回合取证)、zos 落葬。
**双报告级技能**: 抓取四块(+6.5/+9.5/+11.0/+10.5, 75修0破 n=800) +
放置三块(+9.5/+11.0/+10.5, 68修6破 n=600), 判定全确立。
**W4/R10 已执行(2026-08-23)**: zos 退役——设计资本落 docs/zos-salvage.md, zos 仓 README
立碑 + tag `zos-retirement-2026-08-23` 冻结(代码零删除; 仓库 archive 属组织管理动作留用户);
真机 ACT 半升为未来 actuation:real 具身卡需求(v4.2), 非搁置。
**Updated:** 2026-08-23

## 种子区块账本

**PHASE 3 区块预算(round 78 起, 规划先行于任何真跑):** phase 1/2 已烧段——held-out
1000-1999 / 6000-6199 / 7000-7199 / 8000-8599 / 8800-8999 / 11200-11399 / 12000-12199 /
12400-12599 / 13000-13199; dev-选择-诊断 2000-2899 六片; demo 30000 段。phase 3 用 40000+:
**标定 40000-40999**(pass A 座高 40000-40199 / pass B sd 扫 40200-40599, 标定过的块永不再当
门禁/held-out); **Stack campaign dev 蓄水池 41000-41999**; **held-out 42000-42199 /
42200-42399 / 42400-42599**(头条至少三块); 选择 43000+ 需要时用; 44000+ 留后续 rung。
**已烧(round 78-79):** 标定 40000-40799; stack-g1 dev 用 41000-41580(余 419); held-out #1 = 42000-42199。
**已烧(round 81):** round25-rerun dev 44000-44059 + held-out 44200-44399。
**已烧(round 85):** stack held-out #2/#3 = 42200-42399 / 42400-42599(三块配额用毕)。
**已烧(round 86):** pickcan/pickmilk 探针 45000-45011。
**已烧(round 90, 诊断级):** 41581-41880(放置探针 A/B; bring-up 蓄水池按侦察规划使用,
永不作门禁/held-out); 41881-41999 留 smoke/后续 bring-up。
**已烧(M5 clear_build bring-up smoke):** 41881-41890(10 席, smoke 蓄水池内)——4 节点长链活跑首证, governed 臂(stack-g1+place-g2 挂载)链成功 4/10, 全 4 节点遍历 ~2.7s/链(单 worker), 首死直方图 grasp-cube 5 / none 4 / pick-milk 1。链死在未治理的首节点(scripted lift ~50%), 非 stack 节点 = 设计 §4.3 情形, 标定块正是要量 q_pre; 非缺陷, 待正式标定。
**预留(round 90 预注册):** place campaign dev 46000-46999; held-out 47000-47199 /
47200-47399 / 47400-47599(头条三块)。
**已烧(round 91):** place-g1 dev 用 46000-46266(267 席); held-out #1 = 47000-47199。
**已烧(round 92 终章):** place-g2 dev 46267-46999(733 全席); held-out = 47200-47399。
47400-47599 留 place 链复现块 #2; 头条化第三块从 48000+ 取。
**已烧(round 96 夜)**: place-g2 复现块 #2 = 47400-47599(+11.0pp 24修2破 判定确立),
#3 = 48000-48199(+10.5pp 23修2破 判定确立)。**放置链头条三块转正:
+9.5/+11.0/+10.5pp, 68 修 6 破 n=600, 判定三块全确立; broken 率逐块稳定 2/2/2。**
**已烧(round 97 r2, 活验货 FAIL):** 几何抓取卡 lift_geometric 验货 campaign 预登记块 dev 48200-48699 + held-out 48700-48899 经进化态运行时(runs/session-evolution)真跑烧掉, stamped prereg sha ff7c9f841348。
gen-1 dev 功效规划取 196 席(48200-48395), 实测 196/196 = 100.0% 基线零残余失败 → campaign 收敛零规则 → 零晋级, 验货 RED(0 published skill records)。held-out 从未评分(无晋级链可确认), 但预登记块整体随实验烧掉不复用(同 place-g2 733 全席规矩)。base rate 高致晋级空间为零 = r1 预判的诚实天花板, 门就是门不调阈值凑晋级。
**已烧(M5 clear_build 标定, round 97 后):** 标定块 48900-49049(150 席, baseline 臂 headless 探针 scripts/probe_clear_build.py, 10 worker): 链基率 44.7%(67/150), q_pre 65.3%(98/150 达 build-stack), 首死直方图 none 67 / grasp-cube 33 / build-stack 31 / pick-milk 14 / pick-can 5, ~3.76s/集; 标定块永不再当门禁/held-out。
**预留(M5 clear_build 预注册, runs/clear-build-cal, prereg sha 0f3de2e95e12):** dev 蓄水池 49050-49349(300, 功效缩放前缀); held-out #1/#2/#3 = 49350-49549 / 49550-49749 / 49750-49949(各 200, 头条三块, 各评分一次); reserve 50000+ 留 Phase-2 节点间。n4 stack 治理头条按标定 §4.3 推迟(先要几何抓取真晋货), dev/held-out 未烧。
**已烧(M5 clear_build v2 stack-first 再预注册 + dev campaign, round 101):** v1(stack-last)标定触 §4.3(未治理死 52 > n4 治理死 31)→ proceed=false。设计 §1/§4.1 节序杠杆把治理节点 build-stack 排首(planner @v1→@v2, base sha b905a511 未动, 测数不变)。v2 标定(runs/clear-build-cal-v2, 48900-49049 重测——标定块永不门禁, 可复测: 治理死 71 > 未治理死 12, build-stack 150/150 达, 链基率仍 44.7%, q_pre_rate 0.4467>0.30)清所有 §4 门 → proceed=true; 再封 prereg(sha 逐位仍 0f3de2e95e12, stack 任务不随链序变)+ chain_battery_plan 303e5dfb + calibration 684443d9。dev 蓄水池 49050-49349 由 v2 dev campaign 真跑烧掉(runs/clear-build-g1, scripts/clear_build_campaign.py, 从 place-g2 both-families bundle b026831c 种起): gen-1 功效前缀 267 席(49050-49316), 种子 bundle 治理 dev 率 66.3%(177/267), 候选 g4(privileged.stack_xy_residual gt 0.0387 @arm106 replace)fires 130 但对父 fixed 0/broken 0/p=1.0 → 拒(min_fixed 3 未达)→ gen-1 收敛, 0 晋级 = 诚实 null(place-g2 三规则已吃尽 stack 可治理残余; final_sha 仍 b026831c, 0 published skill)。held-out 49350-49549 本相未烧(heldout=() 延迟, 下一相评 3 臂链电池/冻结终 bundle)。

**frontier(round 77 新发现) → round 87 三件全清:** 1. beam gen-1 泄漏已回滚(被拒分支归还
父 bundle, 幸存池只收有封存规则的分支, 红绿钉死) 2. record 路径迁移已修(config.
_STORAGE_PARAMS 在哈希收口点豁免 root——round 29 法则原文留在豁免现场; round 78 的容忍
收紧成钉; 已知天花板: 豁免按键名全局生效, 现仅 graph.skill 用 root, 注释已声明)
3. ruff==0.16.4 已 pin(zos 同版对齐, 存量 lint 同轮清零)。
**frontier(现存, 按顺位, M5 clear_build 落地后):**
0. clear_build 长链卡已落 + 标定 + v2 再预注册 + dev campaign 全跑完(round 100-101)。v1 标定 §4.3 no-go(链死未治理 grasp) → 设计 §1/§4.1 节序杠杆 stack-first(planner @v2) → v2 标定 proceed=true(治理死 71 vs 未治理 12, build-stack 100% 达) → dev campaign(runs/clear-build-g1)gen-1 收敛 0 晋级 = **诚实 null**(place-g2 both-families bundle 已吃尽 stack 可治理残余, 候选 g4 对父 fixed 0)。**round 102 终判已审计封存**: 0 晋级 → held-out 49350-49549 按书未烧(判据本身); verify-claim RED(卡未挂 [claim], 属正确——诚实-null 挣不到封条); vault 干净(clear-build-g1 skills 根空不进索引, 0 新节点, 既有 57162e40/adc55789/eb46481a 血统+GOVERNS→session-main/stack-0 完好)。**⚠ 但基座门禁当前 RED(继承态, 非本相)**: 3 个 vault golden 翻车(test_fold_over_real_runs / test_node_page_has_both_directions / test_faces_byte_equivalent)——round 100/101 往 clear-build-cal(-v2)/skills 塞种子 bundle 技能拷贝供治理臂复现, 但 vault fold 按 store 名序把 STACK 技能 evidenced_by shadow 成 clear-build-cal(应为 stack-g1); 隔离道 3 failed/476 passed/6 skipped, 全量 3 failed/507 passed/3 skipped(文档快照 479/510)。正解 = board/vault.py evidenced_by 取原始 sealed store, 独立相修 + 刷快照, 已开 task。**下一步(下一相, 拥 held-out 烧权)**: 先修 vault fold 拉回门禁 green; 再 3 臂链电池(baseline vs governed=place-g2 bundle)配对同种子 McNemar 于 held-out #1 49350-49549——claim (a)+(b) 用现存规则即成头条, 零新晋级需求; 或按 frontier #3 换难度轴让 stack/grasp 现新残余再谈进化。Phase-2 节点间规则仍 gated(§6.3, 归因未证 n4 节点间路由为主失败)。
1. R4 配对前后门禁(评测电池已可跑, 差 before/after 配对机器门)
2. qwen38 活跑 gate(GPU 被 rynnbrain/Glass_killer 占, 16.7GB<21.6GB; 腾出后
   round25_rerun 补跑 + qwen 卡活验; VLM planner=换 mount)
3. 几何抓取真晋货 gate(触发: 换一条几何抓取真敏感的难度轴——相机噪声/遮挡/多物体——再验货;
   privileged-percept 噪声轴上它零特权不失败, r2 实测 100% 零晋级; 单物体假设仍是最大集成风险, 聚类先行)
4. 目标函数第三假设候补(fp_penalty 1.2 / recall>0.5 未标定; 检测不饱和任务才咬人)
5. gh repo archive Z-Robotics-Lab/zos(用户/组织管理动作)
6. 真机 actuation:real 具身卡(未来, 需求书 docs/zos-salvage.md §7)
已清结项(史录): GUI/CLI=dsh ✓(round 95-96) / chip 调和 ✓(T1 R0) / place 复现 ✓(三块转正) /
M4#6 作废(zos 退役) / anygrasp→几何位姿 ✓(round 93) / 裁决 C 进 propose_rule ✓(T1 R0) /
几何抓取卡活验货 ✓(round 97 r2, 判定 FAIL: 100% 基线零残余失败→零晋级, M4#4 系统内路径全通+链 verify=True, 体检 GREEN; 门就是门)。
**待办:** Mac 归档 runs/campaign-pj-* 拷来解锁两个 skip 测试。

## 已确立的事实（实测）

- 仿真底座 mujoco 3.3.7 + robosuite 1.5.2，10 worker = 212 episodes/min
- 可复现性必须走 `suite.make(seed=N)`；全局 np 种子无效（有回归测试）
- 难度：感知噪声 sd=0.020 → 基线 50%
- 自动搜索 > 人手：`gripper_effort` 比手写的 `finger_gap` 早 6 步触发，把 p=0.057 推到 0.00073
- **特权买到的是幅度（+40% vs +25%），不是有无效果** —— 修正了 round 1 的结论
- 子进程会复活特权特征，但边界是 view 不是 registry，隔离已中和（有回归测试）

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
- **头条数字一律两区块复现再进报告**：区块噪声实测 ±7pp（round 32），单区块不够。
- **序列化形式漏一个字段 = 两道检查同时失明**：`parent_sha` 是从 canonical 算的，
  不能当 canonical 的 backstop。加字段到 Trigger/Rule 时必须同步 `canonical()`。
- 不要用 `bc_h96.npz` 当「更弱的策略」：它是 2.5%，不工作，不是更弱。
- **干净演示克隆恒为 0.0%**，与宽度/epoch/数据量无关（round 12 已记录，round 44 又复现一遍）。
  能用的克隆必须走 DART：`governor/demos.py`，**σ 是旋钮**，0.15 → 0.30 之间有陡坎。
- **冷启动不能只读 STATUS 的下一步**：round 12 的表里就有答案，我花了一整轮重新发现它。
- **`finger_gap < 0.005` 是「夹空」不是「握住」**：方块把手指撑开到 ~0.043 才是握住。
  round 22 把还在合拢的读成握住，round 35 把夹空的读成握住 —— 同一个坑的两面。
- dev 种子 2200-2399 已用于诊断+选择，不能再当门禁切片。
- **测试计数只准报 pytest summary 行, 不准增量推算**(round 63 抓到 61/62 两轮虚记)。
- **两个区块估不出离散度。** round 39 从 n=2 得出「克隆复现更紧」，第三个区块就推翻了。
  要谈稳定性至少三个区块。
- **搜索得分是干预价值的坏代理**（三条证据：gripper_effort 24 修 13 破、
  beam 的 b1/b2 主动有害、round 22 的探针）。样本内分离度高不代表干预有用。
- **写结论时先问作用域**：一个策略的？一个 bundle 的？一个区块的？
  已犯三次（round 31 归因 / round 41 离散度 / round 42 感知侧）。
- **「瓶颈在哪一侧」是策略的性质**：克隆上特权买不到东西，脚本上特权值 +25.0pp。
- **一条 fires≈100% 的规则不是 critic**，是无条件恢复的伪装。门禁已强制成对对比盲发孪生。
- **判据要用成对显著性，不要用净数比大小**：净 35 对净 34 会被当成「有判断」。
- **加一道决定提升的测试，就必须同时给它功效规划**（round 47：0.70 分裂比需 49 对不是 20 对）。
- **「governor 何时有价值」不看成功率**：dart40 31.7% 零规则 vs h256 32.5% 有规则。
  看的是失败能否被选择性检测。
- **攻击自己新加的机制，要挑它最可能失效处打**，不是最容易赢的地方（round 50 第一次攻击白打）。
- **「收缩很大」≠「优势不存在」**：克隆对盲发样本内 +21.4%、不相交切片 +5.7%(n.s.)、
  全新区块 n=200 +12.0%(p<0.001)。小样本的不显著不是反驳。
- **不要靠 import 后改模块属性来切换目标函数**：`def f(..., w=DEFAULT_W)` 的默认值在 def 时
  就绑定了，改 `mod.DEFAULT_W` 够不到它，两臂会跑成同一臂。走参数或预注册字段。
- **两臂 A/B 跑出逐位相同的结果，先怀疑实验没成立，不要当成「无差异」的证据。**
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
