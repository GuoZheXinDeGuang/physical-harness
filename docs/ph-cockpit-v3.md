# PH Cockpit v3 — 拖拽组合面板 + 每实验过程流 + tabler 图标系统 + 技能图谱分组与分化

深度设计。目标读者：零上下文的实现者。写完即可照着建。

所有 UI 路径均相对 fork 仓库根 `/home/yusenzlabpc/Desktop/ph-station`；所有 UI 在
`packages/client/ui-ph-*` 里，走既有 board Remote 端点，**TS 只渲染、不算数**（charter：fork = 界面层；
图布局/分组属于展示、允许；成功率/门禁的数学不允许）。

**本轮关键结论（先说，省一半工作量）**：feedback (2) 与 (4) 都**不需要动 Python / board**。
我已读真实数据核验（§3.2、§5.1），现有事件与 vault 字段已足够做每实验分段和分组。
所以 v3 是**纯 fork/TS 迭代**——无双面（board fn+storecli+mcp+bridge+tests+base-gate）纪律，
门禁只有 `typecheck` + `duplication`（+ 触到 THIRD_PARTY_NOTICES 时更新归属）。

---

## 0. 四条操作员反馈（原话）

1. "目前试验台和对话框不在一个地方，我希望这些 pannel 可以通过拖拽组合排列"——同屏、且**可拖拽组合**。
2. "过程流目前好像是一个累积的 log，而不是每次实验都能查看对应的过程流"——过程流要**按实验分段**。
3. "前端设计没那么完美……可以用一些 svg 图表……用 https://github.com/tabler/tabler-icons 里面的免费 icons"——采用 **tabler-icons (MIT)** 图标系统。
4. "skill graph 连接的不错，但没有 group，以及不同种类的 skills 和 packages 的表现没有分别（颜色和 shape 都一样，只能靠文字名字分别）"——技能图谱要**分组 + 按种类形状/图标/颜色分化 + 常驻图例**。

---

## 1. v2 实际落地了什么（读 fork main）+ 为什么"对话和试验台不在一处"

### 1.1 整个 UI 是一条**互斥 tab 环**

会话骨架 `ui-conversation/src/client/skeleton/ConversationSession.tsx`：

- `resolveActiveView(tabs, selectedId)`（`:30-36`）：`selectedId` 命中则用之，否则取**最左**（order 最小）tab，
  再兜底 `DEFAULT_VIEW_ID='chat'`（`:25`）。
- tab 条 `:154-170`：每个 `conversation.view` 注册项投影成一个 tab（`views.list()`，`tabs.length>1` 才显示）。
- 视图区 `ConversationSession`（`:222-227`）：`renderSlot('conversation.view', {...}, { only: active.id })`
  ——**一次只渲染一个视图**。composer（prompt 输入条）是视图区的同级兄弟，docked 在底部，与当前是哪个 tab 无关。

当前注册进 `conversation.view` 的 tab（order → id → 组件 → 来源包）：

| order | id | 组件 | 包 |
|---|---|---|---|
| **-10** | `lab`（实验台） | `LabView`（graph‖ticker split） | ui-ph-livegraph `index.ts` |
| 0 | `chat`（对话） | ChatView（完整转录） | ui-conversation |
| 10 | `trajectory`（轨迹） | TrajectoryView | ui-trajectory |
| 19 | `livegraph`（执行图谱） | `LiveGraphView` | ui-ph-livegraph |
| 20 | `battle`（战报） | `BattleView` | ui-ph-battle |
| 21 | `evolution`（演进） | `EvolutionView` | ui-ph-panels `index.ts:41` |
| 22 | `cards`（机箱卡） | `CardsView` | ui-ph-panels `index.ts:53` |
| 23 | `ledger`（账本） | `LedgerView` | ui-ph-panels `index.ts:63` |
| 28 | `vault`（技能库） | `VaultView` | ui-ph-vault `index.ts` |

外加非 view slot：`sidebar.section`（OperatorRail，ui-ph-ops `index.ts:32`）、`shell.overlay`（StatusBar）、
`conversation.input.dock`（TaskChips）。

### 1.2 实验台（LabView）实际是什么

`ui-ph-livegraph/src/client/LabView.tsx`：一个 `SplitPane`，左 `LiveGraphView`、右 `TickerView`，
窄于 1100px 时纵向塌缩。它注册为 `id='lab'` order -10（最左）→ 成为**会话默认首屏**。
`SplitPane.tsx` 是本地两栏可拖拽原语（pointer-capture + rAF，比率存 `localStorage['ph.lab.split.h|v']`）。

### 1.3 根因：为什么操作员觉得"试验台和对话框不在一个地方"

**`实验台` 与 `对话` 是两个互斥 tab。** 你在 `实验台` 上能发 prompt（composer 共享、docked 在底），
能看 graph + ticker，但**看不到会话转录**；要读对话必须点 `对话` tab，一点就丢掉整个 cockpit。
v2 的"同屏"只把 graph+ticker 与 composer 并置，没把**转录**并进来，其余 7 个板块也仍是 tab 互斥。
9 个板块只能单显、chat 埋在其中 → 这正是"不在一个地方"。v3 要把互斥 tab 环换成**可拖拽组合的 dock 面板**，
让 chat 转录、执行图谱、过程流、rail、vault、battle…能同屏并排/组合/自由排布并持久化。

---

## 2. Part A — 拖拽组合面板（解 feedback 1）

### 2.1 拖拽库评估与决策

| 方案 | 能否满足"拖拽组合排列（组合=合成 tab 组 / split / float）" | 持久化 | 结论 |
|---|---|---|---|
| **dockview** (MIT) | ✅ VS Code 式停靠：拖 panel 到边缘 split、拖到另一 panel 上**合成 tab 组**、可 float 浮窗、可最大化 | `api.toJSON()/fromJSON()` 原生序列化 | **选它** |
| react-grid-layout (MIT) | ⚠️ 只是不重叠网格卡；能拖能缩放，但**无法把两块合成 tab 组**、无嵌套 split、无 float | 需自己存 layout 数组 | 否 |
| 手搓（延续 SplitPane） | ❌ SplitPane 只有两栏一根 gutter；n 路停靠+序列化+float 全要自造 | 全自造 | 否 |

**依据**：操作员原话"拖拽**组合**排列"——"组合"= 把面板合成一组（tab 化）与自由 split，这正是 dockview
的核心交互，react-grid-layout 表达不出。charter 也允许"删自有代码的成熟依赖"——dockview 恰好删掉我们本要
自造的整套停靠+序列化+浮窗逻辑。dockview 提供 framework-agnostic `dockview-core` + `dockview-react` 绑定，
React 18 兼容，每个 panel 是一个按 id 索引的 React 组件——**与现有 slot 注册表 1:1 对应**。

### 2.2 架构：新包 `ui-ph-dash` 承载 dockview，复用现有 slot 注册表（不重写任何面板）

**懒而正确**：不改任何现有面板。新增**一个** `conversation.view` 注册项 `id='dash'`（order **-20**，比 `lab` 更左
→ 成为新默认首屏；`lab` 保留为可选 tab）。`DashView` 内部：

1. 读**同一份** `views.list()` 视图账本（`useSyncExternalStore(views.subscribe, views.version)`，与 tab 条同源）。
2. 用 `dockview-react` 的 `<DockviewReact>` 承载。为每个视图账本条目造一个 dockview panel，
   panel 的 `component` 内部调用**现有** `renderSlot('conversation.view', props, { only: id })`
   ——即把该 tab 的组件渲染进 dock 面板。于是 chat、执行图谱、过程流、vault、battle、cards… **全部复用**，零重写。
3. `dash` 自身不出现在自己的面板清单里（过滤掉 `id==='dash'`），避免自嵌套。

> 注意：v2 的 LabView 把 graph‖ticker 打包成一个面板。v3 里**拆开**：把 `LiveGraphView`（执行图谱）与
> `TickerView`（过程流）各自作为独立 dockview panel，操作员想并排/上下/合成 tab 组随意。`lab` tab 可保留给窄屏兜底。

### 2.3 chat 同屏落地

`chat`（对话，order 0）本身就是一个 `conversation.view` 条目，因此天然能作为一个 dock panel
（`renderSlot('conversation.view', props, {only:'chat'})`）。composer 仍 docked 在 DashView 下方（骨架同级兄弟，
不随 tab 变）。于是**chat 转录 panel + 执行图谱 panel + 过程流 panel 同屏 + 底部 composer** 一起在场 → 彻底解决"不在一处"。

### 2.4 布局持久化 + reset-to-default

- **默认布局**：`DashView` 内置一个默认 layout 描述（例：左列 chat 占 40%、右上执行图谱、右下过程流；vault/battle/cards
  作为次要 tab 组叠在右下）。首挂载若 localStorage 无存档 → 用默认描述 `api.addPanel(...)` 铺开。
- **持久化**：`api.onDidLayoutChange` → `localStorage['ph.dash.layout.v1'] = JSON.stringify(api.toJSON())`（防抖 ~300ms）。
  key 按 workspace 维度（沿用 SplitPane 的 `ph.*` 前缀习惯）。挂载时 `api.fromJSON(JSON.parse(stored))`，
  `fromJSON` 抛错（版本漂移/坏存档）则 catch 后回落默认布局并清 key。
- **reset 按钮**：dashboard 工具条一枚（tabler `layout-off` 图标，§4.2）→ 清 key + `api.clear()` + 重铺默认。
- **新增/移除面板的兼容**：`fromJSON` 后，对比 `views.list()` 与已恢复 panel：视图账本里有、但存档里没有的
  新面板 → 追加为浮动 tab（不丢新功能）；存档里有、但账本里已无的僵尸 panel → 忽略。

### 2.5 主题与响应式

- dockview 主题走它自带的 `dockview-theme-light/dark` class + CSS 变量覆盖；用 fork 的 `?inline` 通道挂它的样式表
  （与 ui-ph-livegraph `index.ts` 挂 xyflow-base.css 同法），并把 PH 设计 token 映射到 dockview 的 CSS 变量
  （边框/背景/激活 tab 色），做到 light/dark 主题自适应。
- 窄屏（< ~900px）：dockview 在窄屏体验差 → DashView 在断点下**回落**为现有互斥 tab 环（直接 `renderSlot only:` 单显 +
  一条简单 tab 条），或直接把 `lab` 设为窄屏默认。用 `ResizeObserver`（LabView 已有此法 `LabView.tsx:31-40`）切换。

---

## 3. Part B — 每实验过程流（解 feedback 2）

### 3.1 根因

`TickerView.tsx:78-90`：对 `useLiveFeed` 拿到的 `feed.current`（**全量 401 事件、20 个 run**）逐条 `tickerRow` 后
`reverse()` 成一条 newest-first 列表——**从不按 run 分段**，也**与执行图谱的 run 选择器完全脱钩**：
`LiveGraphView` 与 `TickerView` **各自独立**调用 `useLiveFeed`（还导致重复轮询）；graph 有 `runIndex`/`playhead`
状态（`LiveGraphView.tsx:254-299`）与 Scrubber run 选择器，ticker 一无所有。所以 ticker 是累积 log。

而 graph 侧**早已具备**每实验分段：`graph.ts:130-155` 的 `foldRuns` 把 feed 按 `task_claimed`（开）→
`task_done`/`task_failed`（关）切成 `RunInfo[]`，每个含 `firstSeq`/`lastSeq`/`task`/`brief`/`seed`/`markers`。

### 3.2 数据核验（读真实 `runs/session-main/runtime_events.jsonl`）——字段够用，**无需 Python 改动**

401 行、20 个实验（20×`task_claimed`、20×`task_done`、20×`plan_complete`）。逐 kind 字段：

- `task_claimed` 携带 `brief, campaign, seed, task`（+seq,ts）——**完整 run 身份**。
- `task_done` 携带 `brief, task`（+seq,ts）。
- 其余所有 kind（`plan_built/node_start/stage_transition/node_failed/replan/plan_complete/node_verified/actuation_*`）
  **只带 `seq,ts,kind` + 各自 payload，行内无 task/brief/seed 身份**。

**结论**：分段是**位置式**的——按 seq 顺序落在 `task_claimed`→`task_done` 窗口内即归属该 run，`foldRuns` 已如此实现，
在真实数据（单会话、20 个 run **严格顺序、无交错**）下正确。故**字段足够、无需给事件加 `run_id`、无需动 board**。
（数据佐证：`task` ∈ {stack×17, lift_geometric×2, clear_table×1}，可直接用于 run 选择器标签。）

> 唯一的天花板（YAGNI）：若将来出现**并发多任务交错**写同一 feed，位置式分段会错配。届时才在 physical-harness
> 给每事件盖 `run_id`（走完整双面纪律）。当前单会话顺序执行，不做。

### 3.3 修复（纯 TS）：提升共享选择，ticker 按 run 窗口过滤，顺带删双轮询

在 `ui-ph-livegraph` 引入 **`RunFeedProvider` + `useRunFeed()`**（React Context），每会话**只挂一份** `useLiveFeed`，
并持有选择态 `{ runs, runIndex, playhead, run, headSeq, live, pick, seek, goLive, playing… }`（把
`LiveGraphView.tsx:254-299` 现有的那套状态原样搬进 Provider）。DashView（或窄屏 LabView）在根部包 `RunFeedProvider`。

- `LiveGraphView` 与 `TickerView` 从 `useRunFeed()` 读，不再各自 `useLiveFeed` → **消除重复轮询**（同时删掉两处的
  `fastRef` 自持，改由 Provider 依 `isRunning(model)` 统一提速）。
- **Scrubber**（run 选择器 + 时间轴 + play/pause + LIVE 徽章，`LiveGraphView.tsx:139-224`）移到 Provider 消费者共享的
  位置：留在执行图谱 panel 头部即可，其 `pick/seek/goLive` 写 Provider。
- **TickerView 改造**：`const {run, headSeq, feed} = useRunFeed()`，渲染
  `feed.current.filter(e => run && e.seq >= run.firstSeq && e.seq <= headSeq)`。默认 = live/最新 run；
  选过去 run 或拖 scrubber → graph 与 ticker **同步**只显示那一个实验的过程流（回放到 playhead 时 ticker 也截断到 playhead）。
- ticker 头部加一行 run 标签：`过程流 · 实验 {index+1} · {task} #{seed} · {LIVE|回放}`，让"这是哪个实验的流"一眼可见。
- dockview 下 graph 与 ticker 是**两个独立 panel**、无法 prop 直传——正是需要 Context 的原因；`RunFeedProvider`
  包在 `<DockviewReact>` 外层，所有 panel 都是其后代，Context 正常穿透。

**自测（留一个可跑 check）**：给 `foldRuns` / ticker 窗口过滤补一个 vitest：喂真实 jsonl 的前 N 行，断言
选 run k 时 ticker 行的 seq 全落在 `[runs[k].firstSeq, runs[k].lastSeq]`、且行数 = 该窗口内可渲染事件数。

---

## 4. Part C — tabler 图标系统（解 feedback 3）

### 4.1 评估：`@tabler/icons-react` vs 内联 vendored 子集 → **选 vendored 子集**

| 方案 | 体积 | 依赖 | 结论 |
|---|---|---|---|
| `@tabler/icons-react` | 全包 ~5900 图标，各为一个组件；即便 Vite tree-shake 按需，仍是一个要在**严格 workspace** 里安装/审计的运行时依赖 | +1 npm dep | 否 |
| **vendored 子集** | cockpit 实际只用 ~40 个图标，都是 stroke SVG 的 path 数据；打成一个内联组件包约 4–6KB | **零运行时依赖** | **选它** |

**依据**：图标集是**固定小集合**（tab/rail/node kind/status/button/empty，~40 个），无需运行时动态查表；
tabler 图标是纯 24×24 stroke path（MIT），直接抄 path 进内联 React 组件即可，`stroke="currentColor"`
天生主题自适应。新增一个 leaf 包 `ui-ph-icons`（纯展示、零 board 耦合）导出这 ~40 个 typed 组件，
各 ph 包依赖它（这是**共享叶子资产**，不是逻辑克隆，不违反面板独立纪律；反而删掉各包重复画 SVG）。
在 `THIRD_PARTY_NOTICES.md` 加 tabler-icons MIT 归属。

**vendored 组件形态**（示意，统一签名 `{size=16, className}`，`fill="none" stroke="currentColor" stroke-width="2"`）：
每个图标一份，path 从 tabler 源 `icons/outline/<name>.svg` 抄入。提供一个 `Icon` 索引导出便于按名取用。

### 4.2 图标使用映射（tabler 名，全部 outline）

| 位置 | 元素 | tabler 图标 |
|---|---|---|
| **tab/panel 标题** | 实验台/仪表盘 dash | `layout-dashboard` |
| | 对话 chat | `message` |
| | 执行图谱 livegraph | `sitemap` |
| | 过程流 ticker | `timeline` / `list-details` |
| | 技能库 vault | `books` |
| | 战报 battle | `report` / `trophy` |
| | 机箱卡 cards | `box` |
| | 账本 ledger | `book` |
| | 演进 evolution | `trending-up` |
| **rail 卡** | 任务 mini-map | `target` |
| | 进度 | `gauge` |
| | 运行时体征 | `activity` / `heartbeat` |
| | 演进 ticker | `arrow-big-up-lines` |
| **node kind（vault）** | skill | `bulb` |
| | package | `box` |
| | capability | `plug` |
| **node kind（graph）** | mission | `flag` / `target` |
| | plan node | `subtask` / `checkbox` |
| | cap node | `plug` |
| **status chip** | pending | `circle-dashed` |
| | running | `player-play` / `loader-2` |
| | verified | `circle-check` |
| | failed | `alert-circle` / `circle-x` |
| | replanned | `refresh` / `rotate` |
| | privileged | `shield-lock` |
| **按钮** | play/pause | `player-play` / `player-pause` |
| | LIVE | `broadcast` |
| | reset 布局 | `layout-off` |
| | close | `x` | 
| | back | `arrow-left` |
| | search | `search` |
| **空状态** | 无数据 | `mood-empty` / `inbox` |
| | 离线 | `plug-connected-x` |

落地把现有文字/emoji 徽标（如 `LiveGraphView.tsx` 的 `▶/✓/✗`、`TickerView` 的 icon 字段、chip 的 `?` sup）
替换为对应 tabler 组件；status→icon 用一张 `Record<NodeStatus, Icon>` 映射，与现有 `STATUS_CLASS` 并列。

---

## 5. Part D — 技能图谱分组与分化（解 feedback 4）

### 5.1 根因（+ 数据核验，无需 Python）

`VaultView.tsx:69-103` 三个自定义节点 `SkillGraphNode/PackageGraphNode/CapabilityGraphNode` **形状相同**（都是圆角矩形
`css.gnode`），颜色仅靠 skill 的 `st_${status}` 与 priv 弱区分；`graph.ts:187-220` 的 dagre LR 把三种 kind
**混排**、无聚类；过滤 chip 行（`:425-442`）不是图例，kind 无任何常驻图例。→ 只能靠读文字名分辨。

**数据核验**：vault 节点已带足够分组字段——`board/vault.py` 对 skill 节点输出 `task`、`skill_kind`、`generation`
（`vault.py:84-88`），`SkillNode.task/skill_kind` 已在 fork 类型里（`ui-ph-vault/graph.ts:41-49`）。故
kind 分区 + skill 的 **task-family 子分区**（真实数据 task ∈ {stack, lift_geometric, clear_table}）**纯客户端可做，无需动 board**。

### 5.2 分组：React Flow group nodes（kind 区 + skill 的 task-family 子区）

用 React Flow v12 的 **parent/group 节点**（`type:'group'` + 子节点 `parentId` + `extent:'parent'`），
在 `ui-ph-vault/graph.ts` 的 `layout()` 里：

1. 先按现在的 dagre LR 布局出各节点坐标（保留 lineage 成链的可读性）。
2. 按 `kind` 把节点分成三组，各算包围盒 → 为每组 emit 一个 group 容器节点：**技能区 / 机箱卡区 / 能力区**，
   容器带标题（kind 名 + kind 图标 + kind 底色，见 §5.4），z-index 置底、非交互。
3. **skill 区内**再按 `SkillNode.task` 切子容器（stack / lift_geometric / clear_table…），子容器标题为 task 名。
   子容器是 skill 区的嵌套 group（parentId=技能区）。
4. group 节点需显式 width/height（由包围盒 + padding 得出）。子节点 position 转为相对父容器坐标。

> 替代（更省，若 group 嵌套调参烦）：**swimlane** —— 按 kind 分别跑 dagre，纵向排成三条带，带首画一个标题容器 rect
> 作背景（不用 parentId，纯背景节点）。group nodes 是更"真"的容器（可折叠、可选中），swimlane 是纯背景。默认走 group nodes。

### 5.3 每 kind 形状：自定义 SVG 节点（形状即可辨）

三个自定义节点各给**不同轮廓**（用内联 SVG 背景或 clip-path），使**不看文字也能分辨**：

- **skill** = 圆角矩形 + 左侧粗色条（accent bar），偏"卡片"。
- **package** = 带左上折角/标签页的**盒形**（notched box，像文件夹/机箱）。
- **capability** = **胶囊/体育场形**（stadium）或菱形/六边形，偏"插口/契约"。

各节点头部放 §5.4 的 kind 图标 + kind 名；skill 保留 status 徽标与 priv，但降为**次要**通道（边框/小 chip），
不再是主色。node 尺寸沿用 `NODE_SIZE`（`graph.ts:173-177`），形状差异靠自定义组件的 SVG/CSS，不改 dagre 尺寸输入。

### 5.4 强色分离 + kind 图标

给**每个 kind 一个主色相**（与 skill status 正交）：

- skill → business/primary（蓝）；package → success/teal（绿）；capability → violet/amber（紫或琥珀）。
  （用 fork 现有 `--dsw-alias-*` token；status 退居边框/小圆点，不再抢主色。）
- kind 图标（tabler，§4.2）：skill=`bulb`、package=`box`、capability=`plug`，画在节点头与 group 容器标题里。
- 边色维持 `REL_COLOR`（`VaultView.tsx:38-48`，已按 relation 分色，做得好，保留）。

### 5.5 常驻图例（always-visible legend）

画布角落固定一张图例卡（非过滤 chip）：**上半**列三种 kind 的「形状缩略 + 图标 + 主色 + 名称」；
**下半**复用 `REL_COLOR` 列 9 种 relation 的色键。图例常显（可折叠但默认展开）。过滤 chip 行（kind/status/rel）保留其
**过滤**职责，与图例**并存**（图例=看懂，chip=筛选）。

**自测**：给 vault `layout()` 补 vitest——喂 fork 里现成的 vault 折叠样例，断言每 kind 恰好一个 group 容器、
skill 区子容器数 = distinct `task` 数、每子节点 `parentId` 指向正确容器。

---

## 6. 包计划

| 包 | 动作 | 内容 |
|---|---|---|
| `packages/client/ui-ph-icons` | **新建**（leaf，零 board 耦合） | vendored tabler MIT SVG 子集（~40 typed 组件 + `Icon` 索引）。各 ph 包依赖它。 |
| `packages/client/ui-ph-dash` | **新建** | `DashView`（dockview 承载 + 复用 `views.list()` + `renderSlot only:` 适配器 + 布局持久化/reset + 窄屏回落）。注册 `conversation.view` `id='dash'` order -20。依赖 `dockview-react`。 |
| `packages/client/ui-ph-livegraph` | 改 | 新增 `RunFeedProvider`/`useRunFeed`（提升 `useLiveFeed` + run 选择态）；`LiveGraphView`/`TickerView` 改为消费 Provider；ticker 按 run 窗口过滤 + run 标签；图标替换。`lab` tab 保留（窄屏兜底）。 |
| `packages/client/ui-ph-vault` | 改 | group 容器节点（kind 区 + task-family 子区）；三种自定义 SVG 形状；kind 主色 + kind 图标；常驻图例。 |
| `packages/client/ui-ph-ops` / `-panels` / `-battle` | 改（机械） | tab 标题 / rail 卡 / status chip / 空状态 / 按钮换 tabler 图标。 |
| 根 | 改 | `package.json`/`pnpm-lock.yaml` 加 `dockview-react`（MIT）；`THIRD_PARTY_NOTICES.md` 加 tabler-icons + dockview 归属。 |

**新增依赖**：仅 `dockview-react`（拉入 `dockview-core`，MIT，React18 兼容）。tabler **不进 npm**（vendored）。
既有可复用：`@xyflow/react@12.11.3`、`@dagrejs/dagre`、`clsx`、React 18.3.1。

---

## 7. 需要的 seam / 依赖变更

- **无框架级 seam 变更**：DashView 复用**现有** `conversation.view` list slot 与 `renderSlot(...{only})`，
  只是多注册一个视图 + 在其内部编排 dockview。tab 环、composer、骨架**都不动**。
- `?inline` 样式挂载：dockview 主题 CSS 用与 xyflow-base 相同的 `ctx.effect` 挂 `<style data-plugin>`（ui-ph-dash `index.ts`）。
- **无 board / Python 变更**（§3.2、§5.1 已核验字段充足）。因此**无双面纪律、无 base-gate snapshot**。

---

## 8. 构建顺序（每步提交 + 推 branch；每步独立可验收；worktree + scratch 端口）

> 每步前 `git fetch && git pull --rebase`（v2 workflow 的 deploy/audit + 小 agent 的 beta-notice/poll-recovery 仍可能在落）。
> `source ~/.nvm/nvm.sh && nvm use 22` 再 pnpm/push（lefthook pre-push=typecheck）。门禁：`typecheck` + `duplication`
> （沿用 `/* jscpd:ignore */` 面板独立标记）。

1. **`ui-ph-icons`**（vendored tabler 子集）。零依赖、解锁其后所有视觉。验收：typecheck + 一个渲染冒烟。
2. **Part D 技能图谱**（feedback 4，全在 ui-ph-vault，自包含、视觉回报最高、无跨面板协调）：group 容器 + 形状 +
   主色 + kind 图标 + 图例 + layout vitest。
3. **Part B 每实验过程流**（feedback 2）：`RunFeedProvider` + ticker 窗口过滤 + run 标签 + 删双轮询 + vitest。
   保持 LabView（`lab` tab）继续可用。
4. **Part A dockview 面板**（feedback 1，结构大件）：`ui-ph-dash` + DashView + 持久化/reset + 窄屏回落 +
   PH token→dockview 主题映射。注册 `dash` order -20 成默认；`lab` 保留兜底。
5. **Part C 图标铺开**（feedback 3，机械收尾）：ops/panels/battle 的 tab/rail/chip/空状态/按钮换图标；
   更新 `THIRD_PARTY_NOTICES.md`。

（顺序理由：1 解锁视觉；2/3 是自包含面板改造，先把每个面板打磨好；4 再把打磨好的面板停靠进 dashboard；5 收尾。
每步都能独立 demo 给操作员看。）

---

## 9. 风险

- **dockview 主题**：需把 PH token 映射到 dockview CSS 变量，light/dark 都要过一遍；映射不全会漏色。缓解：先只覆盖
  边框/背景/激活 tab 三组变量，截图 light+dark 各一。
- **dockview 序列化漂移**：`fromJSON` 遇旧存档/新面板会抛或缺板。缓解：§2.4 的 try/catch 回落默认 + 面板差集补挂 +
  layout key 带版本号 `.v1`。
- **窄屏 dockview 体验**：断点回落 tab 环（§2.5），别硬塞。
- **group 节点嵌套调参**：React Flow parent/extent + 子节点相对坐标易出错；若卡住走 swimlane 背景 rect 替代（§5.2）。
- **图标铺开面广**：纯机械但触点多；集中在 status→Icon 映射表 + tab label，别逐处手写。
- **位置式分段的天花板**：仅在未来并发交错时才失效（§3.2），当前单会话顺序执行不触发；真出现再上 board 的 `run_id`。
