# 操作员手册 — 给在这个仓库里干活的 agent

你在**主机底座**里。宪章是 `GOAL.md`，当前战况是 `STATUS.md`（只增不改的账本），
结构是 `ARCHITECTURE.md`。这三份是权威，本文只讲**你能做什么、什么绝对不能做**。

## 你的执行入口只有一个

`submit_brief(brief, session=...)` —— 往常驻 runtime 的 inbox 投一张单子。
brief 是**纯选择器 + 预算**，provider 由服务端按 manifest 选；多塞任何一个键都会被拒。

```
{"kind":"task", "task":"kitchen_thaw", "seed":420011, "max_replans":3, "max_actuations":40}
{"kind":"campaign", "campaign":"stack", "dev":[41000,41999], "heldout":[42000,42199]}
```

session 决定投给哪个机器人：`session-main`（robosuite）/ `session-robocasa`（厨房，
另一个解释器、另一套依赖）。不确定就先 `sessions()` 看一眼谁活着。

看结果用 `runtime_events` / `session_progress` / `store` / `heldout` / `vault_node`，
**不要**自己去 runs/ 里翻文件拼结论——那些封存件有链式校验，读法是走 board。

## 种子账本：这是不可逆资源

- 每一个种子块**只能烧一次**。烧过的块再当门禁 = 数据污染 = 整个结论作废。
- 投单前先 `ledger()` 查已烧区间。runtime 侧 `_declared_ranges` 会拦，但**别指望闸门替你思考**。
- **标定块是例外**：标定永不设门、永远可复测，随便重跑。
- **scratch 种子**（`< 542000` 且不在任何 STATUS 声明块内，习惯用 42xxxx/43xxxx）
  不烧账本，冒烟随便用。注意 `seed ≳ 542479` 会让 `seed*7919+11` 溢出崩。
- held-out **只评一次**，且只在真有晋级时才评。没晋级就别碰它。

## 两态铁律

**执行态**只挂冻结的 SkillRecord，一个字节都不写；**进化态**才写。
往活运行时的 skills-root 里加技能会触发审计——正路是归档旧 session-log + 全新 boot 封 row0，
照 `STATUS.md` round 108 的做法。别热塞文件然后假装没事。

同理：**封存过的东西不许改**。prereg、calibration、held-out 结果，落盘即定。
发现错了就开新一轮重测，不是回去编辑旧的。

## 诚实纪律（最重要的一条）

**诚实 null 和诚实 NO-GO 是合格产出，不是失败。**

这个系统的全部价值在于它报的数字是真的。所以：

- 门没过就停下，写清缺哪条能力。**不许调阈值/换门/挑种子来凑一个晋级**。
- 数字难看就如实写难看。`STATUS.md` 里满是诚实 null，那是资产不是污点。
- 改了触发语义之后**不许沿用旧数字**，必须重跑重赚。
- 用仿真器自带谓词当 oracle 前**先审它**——robocasa 的 `check_obj_grasped` 读的是镜像负
  关节，判据近乎永真，爪子合在空气上照样封"成功"。已经骗过两轮结论了。
- **渲染是活状态，不是证据**。frame/截图永远不进 session-log 链。

## 常见坑

- `python -m pytest`，**不要**用 `bin/pytest`（它不把 cwd 加进 sys.path，59 个错误全是假的）。
- cwd **永远**是仓库根。在能看见 `sims/robocasa/` 的地方 import robocasa 会命中 namespace
  package，374 个 kitchen env 静默不注册，然后你会 debug 一小时。
- base lane 计数变了，就在**同一个 commit** 里刷 `docs/base-gate.md` + `README.md`。
  这是重犯条款，分开提交必被打回。
- 不要为了一个 pipeline 去 hardcode harness。任务名的 if 分支出现在通用路径里 = 设计错了，
  正解是加一张卡（`plugins/<card>/manifest.toml` 是纯数据）。

## 动手之前

读 `STATUS.md` 最后几条 round 记录。这个仓库的历史里全是"看起来对但其实被某个假谓词骗了"的
教训，重复踩一遍的成本远高于读五分钟。
