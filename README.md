# physical-harness

Agentic Robotics OS 的 backbone harness。
一个插件化内核(deepseek-harness 的 everything-is-a-plugin 思想)承载一个 physical RSI 工作负载(Zetta 的冻结策略 + 演化 critic/recovery + 配对显著性门禁)。
所有数字来自真实 robosuite/MuJoCo 仿真回合, 无 mock 验证, 无外部 API 调用。

## Pipeline 一条命令跑通

```bash
PYTHONPATH=. .venv/bin/python scripts/demo_campaign.py
```

这条命令端到端地做完整个循环(M 系列 Mac 约 10-15 分钟, 约 2500 个仿真回合):

1. **挂载**: 从 base profile 解析 MountPlan(内容哈希), 在内核上挂载 7 个能力 provider。
2. **基线**: 在 dev 种子上真跑冻结策略, 测出残余失败。
3. **提议**: 搜索在零特权特征上提出一条 critic 规则(触发器 + 恢复程序)。
4. **门禁**: 候选必须同时过两道配对检验 -- 对父代的精确 McNemar 显著提升, 以及显著胜过自己的"盲发孪生"(同一恢复、无条件触发) -- 样本量由实测功效规划决定。
5. **held-out**: 通过的规则链在从未用于搜索的种子区块上评分一次, 附带特权消融曲线。
6. **发布**: 每条被提升的规则作为 SkillRecord 写入 Skill Graph -- 前置条件 = 触发器, 效果 = 对父代的配对增益, 失败模式 = broken 计数, 能力边界 = 特权声明 + 消融曲线。

产物落在 `runs/demo/`: 内容寻址的 campaign store、链式承诺的 session log、技能记录。

一次真实运行的结尾(2026-08-20, 数字随 demo 种子块而定):

```text
=== published skills ===
  a620504ae53b  gen1  trigger observable.finger_gap lt 0.001787  dev 42.9% -> 76.4% (47 fixed / 0 broken)

held-out (n=100): 53.0% -> 73.0%, 20 fixed / 0 broken, p=1.9e-06
held-out vs blind twin: +26.0pp, p=1e-05  -> judgement established
ablation: ground truth 100% / sd=0.010 96% / 0.020 73% / 0.030 59%
```

## 复现已发布的结果(parity)

```bash
PYTHONPATH=. .venv/bin/python scripts/parity_check.py runs/campaign-pj-scripted
```

把封存的 campaign 经内核路径重跑, 逐位比对每代规则 canonical、bundle sha、dev/blind 门禁与 held-out 的全部配对字段。
迁移阶梯的每一级都以双策略 parity 四组 PASS 收口。

## 架构

三层科研板块以契约接入(`harness/contracts.py`, 全部是 runtime_checkable Protocol, 挂错形状在 mount 时就报错):

| OS 板块 | 契约 | 当前 provider |
|---|---|---|
| 1 Reasoning VLM | `reasoner.proposer` | 确定性搜索适配器(真模型走同一 transport, 待接) |
| 2 Scene / Skill Graph | `graph.scene`, `graph.skill` | 占位 / 内容寻址内存实现 |
| 3 具身与环境 | `embodiment.env`, `embodiment.ground_truth`(特权) | robosuite Panda |
| 3 策略 | `policy.driver` | 脚本策略 / 行为克隆 |
| 感知 | `percept.model` | 板载估计(消融梯载体) |
| 执行织物 | `exec.rollouts` | 本地进程池(分布式 = 换一个 mount) |

内核(`harness/`)提供: 能力解析记账(每次 resolve 都被记录, 特权解析吃预算)、profile/bundle/patch 配置分层(解析结果进内容哈希)、链式 session log(就地篡改可被审计抓住)。
内核零插件依赖, 由 AST 测试强制; 插件互不 import, 同样由测试强制。

细节见 [ARCHITECTURE.md](ARCHITECTURE.md), 目标与验收见 [GOAL.md](GOAL.md)。

## 第二具身(Sawyer): 一个 bundle 换机器人

```python
plan = resolve_plan(base_profile(), bundles=(sawyer_bundle(),))
```

一个 bundle 同时换掉 embodiment 与 policy 两个 mount, 别处零代码改动。
在它上面真跑 campaign 得到的第一条跨具身结论(round 60/61):
**检测跨具身迁移, 修复不迁移** -- 零特权触发器在 Sawyer 上判断成立
(对盲发孪生 +20.7pp, p<1e-5), 但恢复程序 0/103 次改变结局:
高度修正后终态显示它确实重新抓到了, 只是和基础策略一样握不牢。
修复的天花板就是策略能力的天花板, 这把 "Sawyer 抓取能力" 变成 layer-3 的一个干净工位。

## Phase 1 的科学结论(本骨架承载的证据)

53 轮自主实验循环建立(全部真仿真、预注册、配对门禁、多区块复现):

- 脚本策略: held-out 三区块合并 **+32.2pp**(193 修复 / 0 破坏, n=600), 对盲发孪生 +27.0pp(p=3e-32)。
- 行为克隆策略(失败不是任何人设计的): 三区块合并 **+13.2pp**, 对盲发孪生合并显著但贴边界。
- **特权买到什么是策略的性质**: 克隆上真值感知与板载无差, 脚本上特权值 +26pp。
- **方法有实测下界**: 失败不可被选择性检测的策略(三个 DART 克隆, 基线最高 31.7%)长不出任何规则, 而基线几乎相同(32.5%)的另一个克隆可以 -- 决定性的不是成功率。
- 23 个被自己抓住的错误、若干测过即关的方向(beam、样本外预筛、合取语言、修复原语), 全部在 `docs/report.html` 与 `progress.md`。

## 仓库结构

```
harness/      内核: 能力接缝、配置分层、事件链、执行织物 (零插件依赖)
plugins/      provider 包: embodiment_robosuite / policies / reasoner / rsi / graphs
profiles/     声明式挂载配置 (3 任务 x 2 策略矩阵)
governor/     phase 1 遗留库, 正按迁移阶梯逐级搬入插件 (L2 时删除)
scripts/      demo_campaign.py / parity_check.py
tests/        165+ 项 (内核不变量、边界、seam、workload、统计)
docs/         phase 1 报告与逐轮记录
runs/         内容寻址的 campaign 存档
```

## 测试

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
```

## 环境

macOS arm64, Python 3.12, `mujoco==3.3.7` + `robosuite==1.5.2`(勿升 mujoco>=3.4, `qM` 改名)。
依赖在 `.venv` 中; 无 GPU、无网络、无 API key 需求。
