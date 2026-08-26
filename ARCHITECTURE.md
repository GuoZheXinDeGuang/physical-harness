# Architecture

一句话定位：**一个让"机器人技能变好了"这句话没法骗人的系统**——冻结策略执行任务、
哈希链封存证据、预注册的统计流程演化技能。内核极小，机器人/仿真器/任务全是插拔卡片。

## 0. 三条设计铁律

整个架构由三条铁律推导而来：

1. **执行与演化分离（execution / evolution 两态）**。干活时策略是冻结的，一个字节不改；
   只有演化态才能产生新技能，且新技能必须通过统计检验才能上岗。
2. **证据先于结论**。每集实验封存成 hash chain 日志，改一字节链就断；实验设计（种子、
   假设、门槛）在跑之前预注册封存，跑完不许反悔。
3. **内核极小，其余皆卡**。内核不认识任何机器人、仿真器、任务——这些全部通过声明式
   manifest 插进来。

## 1. 目录结构

```
physical-harness/
├── harness/        内核：episode 循环、session log 哈希链、特权预算
├── plugins/        卡片：所有"具体"的东西
│   ├── embodiment_robosuite/   桌面机械臂卡（环境 + 感知 + 驱动 + 谓词）
│   ├── embodiment_robocasa/    厨房机器人卡（同上，另一套依赖）
│   ├── mission_*/              任务卡（任务图 + planner，纯数据 manifest）
│   ├── task/                   通用任务机器：图执行、验证、replan
│   └── rsi/                    技能演化引擎：门禁、配对检验、campaign
├── board/          对外唯一门面（"一个店三张脸"）
├── scripts/        常驻 runtime + 演化入口
├── runs/           证据库（gitignored，只在本机）
└── tests/          600+ 测试；base lane 零仿真依赖即可全绿
```

## 2. Kernel（harness/）——内核只提供三个保证

**Episode loop**。一个 episode = 世界从 reset 到关闭的一次完整经历。所有任务都是
这个循环的实例。

**Session log（hash chain）**。每发生一件事（挂载、执行、验证、campaign 结果）追加
一行，每行携带前一行的 hash。事后改任何一行，`verify()` 立刻抓到断链——这是"证据"
的技术基础。审计不是流程，是数学。

**Privilege budget（特权预算）**。仿真里可以作弊（直接读物体真实坐标，privileged /
上帝视角），真机上没有。内核对每次特权读取记账；每条上岗技能附带特权消融曲线
（ablation curve），靠作弊涨的分在消融里现形。

配置完整性：所有能改变实验结论的东西（挂载了什么、用什么参数）折成一个内容哈希
（`MountPlan.sha`）。配置变 = 哈希变 = 另一个实验身份，不存在"悄悄改了个参数"。

## 3. Cards（plugins/）——三类卡片

**Embodiment card（本体卡）**：一个机器人 + 仿真器的全套——环境构造、感知、驱动
（driver，如"导航到冰箱"怎么用轮子实现）、判定谓词（predicate，如"物体在微波炉
里吗"）。每张本体卡配独立 venv（robosuite 与 robocasa 的 numpy ABI 互斥，隔离比
调和便宜），互相不知道对方存在。

**Mission card（任务卡）**：一个任务的图定义——节点怎么连、每个节点用哪个驱动、用
哪个谓词验证。manifest.toml 是**纯数据**，没有逻辑，所以加任务不可能弄坏内核。

**RSI card（演化引擎）**：见 §6。

卡片间的边界规矩是 **provider by ref**：卡不许 import 另一张卡的代码，只能在
manifest 里写字符串引用（`"plugins.embodiment_robocasa:provider"`），由内核在挂载
时解析。挂载点做 Protocol isinstance 校验——形状不对当场报错，不流进 episode。
边界测试常驻，偷 import 隔壁卡直接红。

## 4. 任务图——为什么是图不是脚本

脚本写死"抓、走、放"，失败只能整个重来。我们把任务拆成节点图，节点四种 kind：

| kind | 干什么 | 例子 |
|---|---|---|
| perceive | 看一眼世界 | "厨房里有什么" |
| decide | 做决定 | "先拿哪个罐子" |
| segment | 执行一段动作（唯一动真格的节点） | "导航到冰箱" |
| verify | 读世界活状态核实，**不信 segment 的自我报告** | "真的到了吗" |

核心机制 **in-episode replan**：verify 失败时不重置世界，在同一个世界里重新规划
重试——就像抓杯子滑了一下是原地再抓，不是回家重新出门。一集里可以操作几十次、
错了就地补救，这就是长程任务能力的来源。长任务跑在一个持久 episode 里
（`episodic: true`），环境、观测、执行游标跨节点存活。

另一条铁律：**渲染是活状态不是证据**。取景窗帧、截图永远不进 session log 链。

## 5. Board 与 runtime——外界怎么用这个系统

**Board 是唯一门面**，同一套函数暴露三张脸：Python API（`board/store.py`）、
CLI（`storecli`）、MCP server（给 AI agent）。三脸必须同步改，漏一处测试抓。
读随便读；写只有一个口：`submit_brief`。

**brief 是纯选择器 + 预算**，不含实现：

```json
{"kind":"task",     "task":"kitchen_thaw", "seed":11, "max_replans":3}
{"kind":"campaign", "campaign":"stack", "dev":[41000,41999], "heldout":[42000,42199]}
{"kind":"rsi",      "task":"kitchen_thaw"}
```

用什么代码由服务端按 manifest 决定，brief 里多塞任何键都被拒——执行入口不可注入。

**Runtime 是常驻进程**，逻辑极简：盯一个 inbox 文件夹，有文件就认领（原子 rename，
天然防抢单），干完移到 done/ 或 failed/。没有消息队列、没有数据库，文件系统的原子
性够用。每个仿真器一个 runtime、一个 venv、一个 session 目录。

**两态的硬边界**：mode 是每 session 属性，默认 EXECUTION（fail-safe：真任务永不触发
演化）。boot 时把 mode + 技能清单 + 挂载哈希封成链的第 0 行；执行态收到 campaign/rsi
brief 直接拒；每次执行前重折技能目录摘要与 boot 清单比对——带外塞文件也抓得住。
只有演化态写技能库；执行态只挂已确立（held-out 通过）的冻结 SkillRecord。

## 6. RSI——技能怎么科学地变好

演化的对象是**规则（rule）**："观测特征 X 越过阈值 T 时，执行恢复动作 R"。
例：`finger_gap < 0.0096 → regrasp`（手指闭合后间隙过小说明没夹住，重抓）。

一句 `{"kind":"rsi","task":X}` 触发七步链，每步防一种自欺：

| 步 | 干什么 | 防什么 |
|---|---|---|
| 1 calibration | 跑 150 集 baseline，统计逐节点首死分布 | 凭感觉判断瓶颈 |
| 2 mechanical gates | 六条硬标准逐条打分，不过就停 | 想开工就放宽标准 |
| 3 prereg | 种子/假设/门槛先封存（content hash）再跑 | 跑完改口 |
| 4 dev campaign | 搜候选规则，配对同种子 McNemar（对父版本数"修好/弄坏"） | 把运气当提升 |
| 5 blind twin | 同样的恢复动作但无条件每次做，新规则必须打赢它 | "判断时机"没价值却邀功 |
| 6 held-out | 全新种子只考一次，考完作废 | 反复刷题刷到及格 |
| 7 fold-in | 确立的规则折进运行时技能库 | —— |

三个不许 agent 插手的点：治理节点由首死归因选（不是 agent 挑好看的）；阈值由搜索
算法定；本体没注册恢复原语就诚实报"无从下手"，不现编动作。

**诚实 null / 诚实 NO-GO 是合格产出**。门禁说"失败是能力缺口不是可治理失误"时，
正确动作是去补能力，不是调阈值凑一个晋级。账本里的 null 结论和成功一样值钱——
它们标出了能力边界。

**Seed 即考题**：仿真确定性，同 seed 同世界。考过的种子块永久作废（一次性账本），
runtime 在调度边界强制查重。标定块例外：标定永不设门，永远可复测。

## 7. Evidence（runs/）

每个 session 的封存日志、SkillRecord、campaign 产物，全部 content-hash 命名
（文件名 = 内容 hash，改内容 = 换名 = 断链）。不进 git：它是这台机器的实验记录，
代码库交付的是"能产生证据的机器"，不是证据本身。外界读证据一律走 board，
不直接翻文件。

## 8. UI（ph-station，独立仓库）

红线一条：**前端零业务逻辑**。浏览器里的面板（执行图谱、过程流、轨迹、仿真取景窗、
技能谱系、演进进度）全是纯视图，所有状态经唯一一条 HTTP 面
（`POST /api/board/<fn>`）从 board 读。前端不算任何数——所以它不可能算错，
所有数字都能追溯到 hash 链。

## 9. 不变量（可验，测试常驻）

- 可见即已记录：能力解析、挂载、特权使用全部落链式日志，篡改被 `verify()` 抓住。
- 进哈希的配置字段必须完整出现在 canonical 序列化里（有常驻测试）。
- 零插件可启动：`plugins/` 为空时 base lane 全绿——内核真正 sim-agnostic。
- 判断类主张必须过 blind twin + held-out；头条数字至少跨三个种子块。
- 执行态永不写技能库；真机卡（`actuation:real`）被 sim runtime 拒挂。

```
你的一句话 → brief → inbox → runtime 挂卡执行任务图
                                  ↓
                        每步 verify，失败就地 replan
                                  ↓
                        全程封进 hash chain（runs/）
                                  ↓
            UI 只读 board 画出来；RSI 只从证据里长出新技能
```

**执行不写、演化不骗、前端不算、证据不改。**
