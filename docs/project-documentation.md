# Project Documentation

物理测试台的官方文档。本文回答"东西在哪、谁说了算"；机制细节见
[ARCHITECTURE.md](../ARCHITECTURE.md)，项目方向见 [GOAL.md](../GOAL.md)。

## 项目结构与仓库分工

### 一句话定位

| 仓库 | 语言 | 是什么 |
|---|---|---|
| physical-harness | Python | 机器本身。所有能力、所有判断、所有证据 |
| ph-station | TypeScript | 控制台。显示和输入，没有任何判断 |

分界红线只有一条：**TypeScript 里不许有业务逻辑、不许有统计、不许有 gate
决策。** 面板上显示的每一个数字，都是 Python 算好后逐字渲染的。

### physical-harness 里有什么

```
physical-harness/
├── harness/          kernel —— 不认识任何机器人、仿真器、任务
│     contracts.py      所有插槽的 Protocol（mount 时校验形状）
│     kernel.py         episode loop、privilege budget 计量
│     events.py         SessionLog：哈希链，篡改即断
│     opstream.py       活状态事件流（永不进链）
│     manifest.py       discover()：折叠所有 card 的 manifest
│     config.py         MountPlan.sha —— 配置即实验身份
│
├── plugins/          card —— 所有"具体"的东西
│     embodiment_robosuite/    机械臂 + robosuite
│     embodiment_robocasa/     厨房机器人（独立 venv）
│     embodiment_libero/       LIBERO（脚手架）
│     mission_*/               任务图 + planner（纯数据 manifest）
│     task/                    通用任务机器：workload、validate
│     rsi/                     进化引擎：gate、配对检验、campaign
│     policies/                策略驱动
│     planner_vlm/             VLM 生成 node graph
│     policy_vla_remote/       VLA 策略走 websocket
│     model_endpoint/          OpenAI 兼容 chat 客户端
│
├── board/            对 runs/ 的唯一 API，三个 face 字节等价
│     store.py          实现
│     storecli.py       CLI face
│     mcp_server.py     MCP face（agent 用的就是这个）
│
├── scripts/          常驻进程和入口
│     harness_runtime.py   常驻 runtime，盯 inbox
│     cockpit             一键启动一切（含拉起 ph-station）
│     rsi_campaign.py      RSI 七步链
│     frame_dump.py        画面和 keyframe
│     plugin_doctor.py     card 体检
│
├── profiles/dsh/     ← 注意：控制台的配置在这里，不在 ph-station
│     cordis.patch.template.yml  MCP server 注册 + LLM route + 默认 preset
│     deploy_profile.py   把模板渲染到 $DSH_HOME/cordis.patch.yml
│
└── runs/             证据（gitignored）
```

**关键**：控制台的配置在 physical-harness 里，由 harness 仓库拥有——换模型、注册
MCP server 都改 `profiles/dsh/cordis.patch.template.yml`（模板里没有任何绝对家目录，
路径全从仓库根推导）。`scripts/cockpit` 每次启动都用 `deploy_profile.py` 把它渲染成
`$DSH_HOME/cordis.patch.yml`；渲染失败就拒绝启动，因为没有那个文件 agent 就没有
`mcp__physical-harness__*` 工具，会退回原生 bash。可变项（base_url / model id / 显示名 /
apiKeyEnv / 端口）来自仓库根的 `.env`，见 `.env.example`。

### ph-station 里有什么

它是 deepseek-harness（dsh）的 fork。

```
ph-station/
├── packages/core/        agent loop、session、system-prompt 注册表
├── packages/llm/         provider 适配器
│     llm-pi-ai/            通用 OpenAI 兼容 route ← 本地/API 都走它
│     llm-deepseek/         DeepSeek 专用
│
├── packages/host/
│     dsh-ph-board/        ← 唯一连接 physical-harness 的包
│                            每个 @Remote 方法转发给 storecli
│                            自动暴露 POST /api/board/<name>
│
├── packages/client/      面板（全是渲染）
│     ui-ph-livegraph/      执行图谱 + 过程流 + 取景窗
│     ui-ph-panels/         RSI 总览 + 迭代记录 + 能力卡 + 账本 + Run RSI 按钮
│     ui-ph-ops/            运行体征侧栏：主机资源、本地模型开关
│     ui-ph-vault/          技能库
│     ui-ph-battle/         Held-out 战报
│     ui-ph-dash/           实验台（面板布局）
│     ui-conversation/      对话
│
└── apps/web/             浏览器入口
```

ph-station 从不直接读 `runs/`。它只会调 dsh-ph-board，后者 exec storecli。

### 两者怎么连（三个接触点）

```
① 启动
   physical-harness/scripts/cockpit
     → 拉起 harness_runtime（Python，一个 session 一个进程）
     → 拉起 node .../ph-station/apps/cli/lib/bin.js web --port 3080

② agent 调工具（写路径）
   ph-station agent loop
     → stdio JSON-RPC
     → physical-harness/board/mcp_server.py
     → 原子写进 runs/<session>/inbox/

③ 面板读数据（读路径）
   面板 → POST /api/board/<name>
        → dsh-ph-board（TS）
        → exec storecli（Python）
        → board/store.py 计算
        → JSON 逐字返回，TS 只负责画
```

配置由 ① 那条线的 `profiles/dsh/cordis.patch.template.yml` 渲染后注入。

### 谁拥有什么决策

| 功能 | 归属 | 为什么 |
|---|---|---|
| 任务图怎么拆 | physical-harness（planner） | 是能力，不是显示 |
| 统计检验、gate 判定 | physical-harness（plugins/rsi） | TypeScript 里不许有统计 |
| seed 账本、burn 检查 | physical-harness（runtime） | 决定结论有效性 |
| 什么算成功 | physical-harness（predicate/oracle） | 是证据 |
| brief 校验 | physical-harness（runtime，唯一权威） | MCP 层故意不校验 |
| 用哪个模型 | physical-harness（profiles/dsh/） | 配置属于主板 |
| agent 怎么理解你的话 | ph-station（agent loop + 模型） | 是交互 |
| 画面怎么显示 | ph-station（面板） | 是显示 |
| 面板布局、语言、主题 | ph-station | 是显示 |
| 红/黄/绿阈值着色 | ph-station（纯展示） | 数据仍来自 Python |

判断归属的问法：**这件事错了，会不会让一个科学结论变错？** 会 →
physical-harness。不会 → ph-station。

### 落点举例（2026-08-28 一轮改动）

| 做的事 | 落在哪 |
|---|---|
| keyframe 落盘 + 两个 board face | physical-harness |
| keyframe 缩略图 + 灯箱 | ph-station |
| Run RSI 按钮 + 七步 stepper | ph-station |
| submit_brief 的 CLI face | physical-harness |
| runtime heartbeat 字段 | physical-harness |
| cockpit 收编进化态 runtime、setsid | physical-harness |
| 取景窗 session 下拉、旧帧不隐藏 | ph-station |
| 新对话面板清空 | ph-station |
| 本地 Qwen route | physical-harness（profiles/dsh/） |
| VRAM 调参结论 | physical-harness（docs/ph-station-design.md） |
