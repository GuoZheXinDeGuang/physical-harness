# PH Cockpit v2 — 实验台 + 执行图谱合并 + 可懂化改造

深度设计。目标读者：两位零上下文的实现者。写完即可照着建。

本文所有路径均相对 `ph-station` 仓库根（`/home/yusenzlabpc/Desktop/ph-station`）。
所有 UI 都在 fork 的 `packages/client/ui-ph-*` 里，走既有 board Remote 端点，**TS 只渲染、不算数**
（charter `physical-harness/GOAL.md`：fork = 界面层；图布局属于展示、允许；成功/门禁的数学不允许）。

---

## 0. 要解决的四条操作员反馈（原话）

1. **同屏**："几个重要的模块在同屏一起展示，而不是全部需要点上面的 list 切换页面。任务图和执行图和轨迹和对话放在一起展示……用户做试验的时候就能实时的 prompt，看到规划，执行顺序……更像一个 agentic OS 的面板。"
2. **看不懂**："演进和机箱是什么意思我有点看不懂"——面板要自解释（命名、一行说明、空状态、tooltip）。
3. **两图重叠**："任务图和执行图还可以大幅度优化，深度思考"——两张图都画 mission/plan+routing，一张偏封存历史、一张偏实时事件，职责重叠、互相打架。
4. **实测后的二轮反馈（原话）**：
   - "任务图比较浅"——**深度缺陷**。
   - "执行图没有点线边连接"——**边缺陷**：节点卡片悬浮、无连线（已在实测截图确认）。
   - 愿景一句话："一个人发布下去同屏幕内能够看到好几个板块，能看到规划出来的 graph，执行路线，执行过程，当前在哪一步，等等整个过程，完整的 plan，过程，结果。"

---

## 1. 布局系统研究结论（带文件引用）

### 1.1 视图环与共享 composer（这是整个 v2 的支点）

- 会话主体骨架 `ui-conversation/src/client/skeleton/ConversationRoot.tsx`：`scrollBody` 里并排两个兄弟节点——
  `renderSlot('conversation.session')`（视图环）与 `composerSeat`（sticky 到底部的输入条）。
  **composer 是视图环的同级兄弟，跟当前是哪个 tab 无关，永远在底部。**（`ConversationRoot.tsx:186-194`）
- 视图环 `ui-conversation/src/client/skeleton/ConversationSession.tsx`：
  `renderSlot('conversation.view', {...}, { only: active.id })`——**一次只渲染一个视图**（`ConversationSession.tsx:200-208`）。
  首屏默认由 `resolveActiveView` 决定：`selectedId ?? DEFAULT_VIEW_ID`，`DEFAULT_VIEW_ID = 'chat'`（`ConversationSession.tsx:24-31`）。
- tab 条 `ConversationSession.tsx:145-160`：每个 `conversation.view` 注册项投影成一个 tab（`views.list()`，`tabs.length > 1` 才显示）。
- 视图槽契约 `ui-conversation/src/client/contract/slots.ts:106-113`：`'conversation.view': { kind: 'list'; scope: 'session'; owner: ConvViewOwnerProps }`。
  每个视图通过框架标准 kit 拿到 `useSession`（会话快照）、`sessionId`、`useSessions`——见 `ConvViewProps`（`slots.ts:459-467`）。

**结论 A（关键）**：任何 `conversation.view` 视图都**自带底部 prompt**（共享 composer）。所以"同屏 prompt + 别的板块"
不需要嵌入聊天输入框——它已经在了。我们只需要把**图 + 轨迹**塞进视图区域即可。

**结论 B**：`ui-trajectory/src/client/TrajectoryView.tsx` 证明——一个 `conversation.view` 可以只靠 `useSession`
读会话快照、纯渲染（契约注释 `slots.ts:463-466`："store-less pure readers (ui-trajectory) take this base alone"）。
所以自定义视图能读到"对话"数据，无需重装 ChatView 的全部注入面。

### 1.2 三栏外壳与可用缝

- `ui-layout/src/client/AppFrame.tsx`：三栏 grid `sidebar | center(conversation) | details` + 一层 `shell.overlay`（`AppFrame.tsx:164-199`）。
  - `details` 右栏：单占位，当前是工具调用检查器 `DetailsPanel`；可拖拽，宽度区间 **300–520px**，关闭时宽度 0 但**不卸载**（`columns.ts:35-39`, `AppFrame.tsx:31-34`）。
  - 拖拽手柄 `DragHandle`：pointer capture + rAF 节流（`AppFrame.tsx:40-84`）——**这是我们做分栏拖拽条的现成模板**。
- `sidebar.section`（list）：`ui-sidebar/.../contract/slots.ts`——"richer sidebar of panels"，当前被 `ui-ph-ops` 的 OperatorRail 占（`ui-ph-ops/src/client/index.ts:53-66`）。
- `shell.overlay`：全框浮层，当前被 `ui-ph-panels` 的 StatusBar 占（`ui-ph-panels/src/client/index.ts` 末尾）。
- `conversation.input.dock`（list，composer 上方整行）：当前被 TaskChips 占。

**能否把真实 ChatView 塞进自定义视图？** 不划算。ChatView 是 `conversation.view` 项，注入面极重
（`ChatViewInjected`：openDetails / openFile / loadOlder / loadImage / inspectCall / chatScroll / forkAt / fileMentions，见 `slots.ts:748-785`），
由 ui-conversation 的 apply 装配。重建这一整套 = 强耦合 + 大量样板。**放弃嵌入**。走反向设计（下 §3）。

### 1.3 现有分栏原语？——没有

`grep -rE "resiz|split|SplitPane|Resizable"` 只命中 `AppFrame.tsx` 的 gridTemplateColumns + DragHandle。
无第三方 split-pane、无可复用组件。**按既定策略：CSS grid + 一个拖拽分隔条（~30 行，复刻 AppFrame 的 DragHandle 手法），不加依赖。**

### 1.4 现有两图 + 数据面

- **任务图** `ui-ph-ops/src/client/CockpitView.tsx`（`conversation.view` id=`mission`, order 19）：封存历史向。
  读 session chain 的 `task.plan_complete` 多次运行 → `buildGraph`（`graphModel.ts`）→ React Flow DAG（goal→node→stage + capability fan）。
  有 run pills（多运行切换）、右侧 evidence 面板、chain strip。**这就是"3 张扁平卡、比较浅"的那张。**
- **执行图** `ui-ph-livegraph/src/client/LiveGraphView.tsx`（`conversation.view` id=`livegraph`, order 24）：实时事件向。
  读 `runtimeEvents`（增量 cursor，`afterSeq`）+ session rows → `foldEvents`（`graph.ts`）→ React Flow（mission/plan/cap 节点 + plan/verify/routing 边）。
  `foldEvents` 已经把 plan_built / node_start / stage_transition / node_verified / replan / task_done 折成模型（`graph.ts:117-199`）。
- 两图都画 mission + plan + routing。**这就是"重叠、打架"的根因**——同一份东西两种投影。
- board Remote 端点（`packages/host/dsh-ph-board/src/index.ts`，全部 verbatim 转发、零解释）：
  `stores / store / heldout / cards / rounds / ledger / sessions / session / sessionProgress / runtimeStatus / runtimeEvents`。
  重放/实时可用：`runtimeEvents`（cursor feed，含 `plan_built` 全节点图、`node_start`、`stage_transition`、
  `actuation_start/end`、`node_verified/failed`、`replan`、`task_done/failed`；`afterSeq` 增量）、
  `session`（chain rows，含 `task.plan_complete` 历史、`capability.resolve`）、`sessionProgress`（Python 折叠的进度）、`runtimeStatus`（pid/mode/boot）。

---

## 2. 边缺陷根因（已确认）+ 修复

**症状**：执行图渲染出节点卡片，**零连线**（mission 节点、skill 节点、六个 routing 卡之间没有任何边）。

**根因（确认，非猜测）**：
`LiveGraphView.tsx` 的三个自定义节点组件 `MissionNode` / `PlanNode` / `CapNode`（`LiveGraphView.tsx:50-102`）
**没有渲染任何 `<Handle>`**。React Flow 12 的自定义节点必须渲染 `<Handle>` 才能给边提供锚点；
没有 handle，React Flow 解析不出 source/target 端点，直接丢弃这条边（控制台报 error 008
"Couldn't create edge for source/target handle id"）。边其实是被创建了的——
`LiveGraphView.tsx:216-221` 明确 `edges={flow.edges.map(...)}`，`graph.ts:235-285` 的 `layout()` 明确 push 了
plan/verify/routing 三种边——但因为**节点无 handle**，全部渲染不出来。

**对照证据**：任务图的节点 `ui-ph-ops/src/client/StatusNode.tsx:22-26` **有** `<Handle type="target" .../>` 和
`<Handle type="source" .../>`（并给了 `css.handle` 隐藏样式），所以任务图的边能画出来。两张图唯一的差异就在这。

**修复（合并后落在合并图核心里）**：
1. 给 `MissionNode` / `PlanNode` / `CapNode` 各加两个 handle。布局是 `rankdir: 'TB'`（`graph.ts:237`），所以：
   - 目标 handle：`<Handle type="target" position={Position.Top} id="in" className={css.handle} />`
   - 源 handle：`<Handle type="source" position={Position.Bottom} id="out" className={css.handle} />`
2. **routing 边的坑**：routing 卡放在 plan 链右侧（`graph.ts:271-284`），边是 `mission → cap`。
   mission 节点因此需要**第二个**源 handle：`<Handle type="source" position={Position.Right} id="cap" />`；
   cap 节点加 `<Handle type="target" position={Position.Left} id="in" />`；routing 边在 `layout()` 里带上
   `sourceHandle: 'cap'`（否则 React Flow 取第一个 handle，routing 边会从底部诡异地绕出来）。
   → `graph.ts` 的 `LaidOutEdge` 增加可选 `sourceHandle?: string`；plan/verify 边 `sourceHandle: 'out'`，routing 边 `sourceHandle: 'cap'`。
   `LiveGraphView.tsx:216-221` 的 edge map 透传 `sourceHandle` / `targetHandle`。
3. handle 隐藏样式（复刻 `ops.module.css` 的 `.handle`）：`opacity:0; width:1px; height:1px; min-width:0; min-height:0; border:0;`
   （base css 默认给 handle `#333` 背景 + `min-width:5px`，不隐藏会露出黑点）。
4. **当前执行边**要视觉独立且动画：给"正在执行节点的入边"单独一个 class `edgeActive`
   （accent 色 + `animated`）。现有 `LiveGraphView.tsx:219` 的 `animated: e.kind === 'plan' && running` 加 handle 后就能显现，
   但要把它收窄成"仅当前活跃边"动画，其余 plan 边静态实线。判定：`node_start` 指向的 node 的入边 = active。

这条**先于一切单独发一版**（见 §8 build order 第 1 步）——最刺眼、diff 最小。

---

## 3. Part A — 实验台（experiment cockpit）：同屏组合

### 3.1 决策

**实验台 = 一个新的 `conversation.view`（id=`lab`），内部是一个可拖拽的左右分栏。** composer 共享（prompt 免费）。
不嵌入真实 ChatView（§1.2 已论证不划算）。反向设计落地为：**围绕共享 composer 停靠我们的模块**——
composer 就是我们保留的那块"聊天面"（实时 prompt），图 + 轨迹停靠在它上方。完整对话历史保留在**未改动的 `对话` tab**，一键可达。

### 3.2 布局（默认视图）

```
┌──────────────────────────────────────────────────────────────────────┐
│ header（标题 + tab 条）   ← 外壳自带，共享                              │
├────────────────────────────────────┬───────────────────────────────────┤
│                                    ║                                   │
│   执行图谱（合并图，见 §4）          ║   过程流 ticker（= 轨迹）           │
│   完整 plan + 执行路线 + 当前步高亮   ║   runtime_events 时间线：           │
│   + 结果态          （默认 ~58%）    ║   规划→节点→阶段→结果（~42%）        │
│                                    ║   当前步置顶/高亮                   │
│                                    ║                                   │
├────────────────────────────────────┴───────────────────────────────────┤
│  [共享 composer：prompt + 流控制]   整宽，sticky 底部                    │
└──────────────────────────────────────────────────────────────────────┘
```

- **左栏（图，~58%）**：`执行图谱` 合并图（§4），LIVE 模式自动跟随运行中的任务。
- **右栏（过程流 ticker，~42%）**：`runtimeEvents` 折出的**时间倒序/顺序事件流**——每条一行：
  `plan_built`（列出 N 个节点）、`node_start`（节点名 + skill）、`stage_transition`（阶段 ✓/✗）、
  `actuation_end`（steps 数）、`node_verified/failed`、`replan`、`task_done/failed`。
  **当前步**（最新的运行中节点）置顶高亮、与图上高亮同色。这就是操作员要的"执行过程 / 当前在哪一步 / 轨迹"。
- **底部 composer**：共享、整宽。操作员在这里发 prompt，无需切 tab 就能看着图和 ticker 长出来。

> "对话"如何满足：(a) 底部共享 composer 负责输入；(b) 右栏过程流负责实时叙述执行过程；
> (c) 完整助手对话历史在未动的 `对话` tab（一键）。若操作员坚持要**真实 transcript 与图并排**——那是更大改动
> （嵌入 ChatView 全注入面），列入 §9 风险，需显式确认后再做。**可选增强**（懒版，后加）：右栏 ticker 顶部
> 插一行"最新助手规划旁白"，直接从 `useSession` 快照取最后一条 assistant 文本——不用新端点、不重建 ChatView。

### 3.3 分栏原语（本地，~30 行）

新文件 `ui-ph-livegraph/src/client/SplitPane.tsx`。两栏 CSS grid + 一个竖直拖拽条，复刻 `AppFrame.tsx:40-84`
的 pointer-capture + rAF 手法（**不抄整份，只借模式**——避免 jscpd 命中就用不同的最小实现）：

- props：`left: ReactNode; right: ReactNode; storageKey: string`。
- 状态：`ratio`（0.25–0.75 clamp），初值从 `localStorage[storageKey]` 读，默认 0.58。
- `gridTemplateColumns: \`${ratio*100}% 6px 1fr\``；拖拽条 `onPointerDown` setPointerCapture，`onPointerMove`
  rAF 节流算 `ratio = (clientX - rectLeft) / rectWidth`，`onPointerUp` 写回 localStorage。
- 一处 `assert`-式自检足够（见 §8 gates）：clamp 边界。

### 3.4 响应式塌缩规则

用容器宽度（`ResizeObserver`，同 AppFrame 手法）决定：
- **≥ 1100px**：左右分栏（图 | ticker）。
- **768–1100px**：上下堆叠——图在上（60vh），ticker 在下（可滚动）。SplitPane 改为纵向（`gridTemplateRows`）。
- **< 768px**：单列，图在上、ticker 折叠成一个"过程"抽屉（点开），composer 仍在底部。
  （移动端不是验收重点，但不能横向溢出——外壳的窄屏会自动收起 sidebar，`columns.ts:30-33`。）

### 3.5 成为会话默认，且不毁掉纯聊天 tab

见 §7 seam 变更（`resolveActiveView` 改成"默认落在 order 最小的 tab，否则 'chat'"）+ 把 `lab` 注册在 order 18（最左）。
`对话`(chat) tab 仍在（只是不再是首屏默认），持久化的 `store.view` 若用户上次选了 `对话` 就仍停在 `对话`
（`resolveActiveView` 用 `selectedId` 优先，`ConversationSession.tsx:27-31`）。零破坏。

---

## 4. Part B — 合并图 `执行图谱`（一块画布，替掉任务图+执行图）

**归宿**：**扩展 `ui-ph-livegraph` 成合并图核心**（任务要求）。它已经有 `runtimeEvents` fold + React Flow 画布，
是天然的家。把任务图（CockpitView）里有用的深度件（Evidence 面板、run pills、逐阶段成绩）**迁移**进来，
然后**删掉** `ui-ph-ops` 的 `mission` tab 注册与 CockpitView/MissionGraph/graphModel（迁移不是克隆，删原件避免 jscpd 命中）。

`ui-ph-ops` 只保留 `sidebar.section` 的 OperatorRail（仍有用）。

### 4.1 一块画布，三层可组合

1. **Plan 层（主，永远在）**：mission → plan 节点 → 每节点的 stage 流水线。
   数据源二选一：`plan_built`（LIVE）或 `task.plan_complete`（历史/封存）。`foldEvents`（`graph.ts:117-199`）已折 LIVE；
   `foldSealedPlan`（`graph.ts:98-114`）已折封存。**深度补强见 §4.3。**
2. **Routing 层（次，可折叠，默认关）**：capability fan（`capability.resolve` → consumer/capability/provider ref/privileged）。
   头部一个 toggle `显示能力接线`，默认隐藏以减少杂乱（当前 6 张 routing 卡是主要视觉噪声来源）。
3. **Live 动画层**：来自 `runtimeEvents`——当前节点脉冲（`stRunning` 已有）、当前执行边动画（§2.4 的 `edgeActive`）、
   cursor 位置。

### 4.2 模式逻辑：LIVE / HISTORY

- **LIVE（默认）**：自动跟随运行中的任务。沿用 `LiveGraphView` 现逻辑：取最新 session、`runtimeEvents` 增量轮询
  （`afterSeq` cursor，`LiveGraphView.tsx:117-160`，快 1.2s / 慢 4s / 隐藏暂停）。playhead 钉在末尾。
- **HISTORY（重放）**：从 `task.plan_complete` 历史 / feed 里的 `task_claimed` 边界选一次过往运行，用**时间轴 scrubber** 重放。

  **重放机制（纯客户端、零新端点）**：`foldEvents` 本就是"从头顺序折整段 feed"。所以重放 = **把 feed 截断到 seq ≤ K 再折**：
  `foldEvents(sessionRows, feed.filter(e => e.seq <= K))`。playhead 位置 K → 直接渲染那个中间态。
  一次运行的边界由 `task_claimed` 事件划分（`foldEvents` 遇到 `task_claimed` 会 reset 模型，`graph.ts:138-146`）——
  scrubber 的量程 = 选中运行的 `[firstSeq, lastSeq]`。
  **跨 boot 的老运行**：`runtime_events.jsonl` 重启会截断（`LiveGraphView.tsx:138-147` 已处理 cursor 回退）。
  feed 里没有的老运行，回退到 `foldSealedPlan` 的**静态封存快照**（无 scrub）——诚实降级，空/静态态见 §5。

### 4.3 深度补强（解 "任务图比较浅"）

节点从"一张扁平卡"升级为**可展开、带流水线的实体**：

- **逐阶段成绩**：每节点内联 stage 芯片（`PlanNode` 已有 `n.stages` 芯片，`LiveGraphView.tsx:76-82`），
  每芯片 ✓/✗ 上色（`stVerified/stFailed`），补 **step 数**（`actuation_end.steps`，`graph.ts:169-170` 已存 `n.steps`）
  与**耗时**（相邻事件 `ts` 差，`OpEvent.ts` 有，`graph.ts:20-25`）。
- **故障**：节点角标显示 fault 数（LIVE 来自 `node_failed`；封存来自 `task.plan_complete.faults`），点开在 Evidence 面板列出。
- **重规划血缘（可见分支边）**：现在 `replan` 只把 failed 节点重染成 `replanned`（`graph.ts:178-181`）——**改成血缘**：
  保留失败的那次尝试为一个节点，`replan` 时新增一个"尝试 2"节点，画一条 `branch` 分支边
  `失败节点 → 尝试2 节点`（新 edge kind `branch`，虚线 + 琥珀色 + `replan #N` 标签）。
  数据：`plan_built` 携带 `replan` 计数（`graph.ts:149`），每次 replan 会重发 `plan_built`；
  `foldEvents` 改为按 `(id, replanIndex)` 保留历史尝试而非仅按 id 覆盖。
- **Evidence 面板（从任务图迁入）**：点节点 → 图侧/底部 Evidence（迁移 `CockpitView.tsx:177-218` 的 `Evidence`）：
  节点/阶段/能力/mission 各自的原始行 + stages 数 + faults + replans + actuations + evidence 链接。
- **特权/预算徽章**：能力节点 `privileged` 已有红点（`LiveGraphView.tsx:96`）。预算徽章：数据在哪加在哪——
  `capability.resolve` 有 `privileged`；per-node budget 目前 board 无此字段，**不臆造**（charter）；
  若 `sessionProgress` / ledger 未来带上再挂。

### 4.4 折掉冗余

- 任务图的 **run-history strip**（run pills，`CockpitView.tsx:101-120`）→ 折进合并图头部的**运行选择器 + scrubber**。
- 任务图的 **evidence drilldown** → 折进 §4.3 的 Evidence 面板。
- 旧两 tab（`mission` + `livegraph`）→ 塌成**一个 `执行图谱` tab** + 其在 `实验台` 里的内嵌用法。

### 4.5 Scrubber / 时间轴 UI（精确规格）

合并图头部（画布上方一条）：

```
[LIVE ●] | 运行: [run 3 ✓ ▾] | ├─●───────────────────────────────┤ 12/47 | [▶] [显示能力接线 ☐]
  |          |                    |   playhead    刻度=关键事件         |   |
  |          |                    └ 时间轴 track（seq 量程）            |   └ 播放/暂停：~300ms/事件 自动推进
  |          └ 运行选择器：task_claimed 边界 + 更老的封存运行（✓/✗ 上色）
  └ LIVE 徽章：绿=跟随实时；拖动 playhead 离开末尾 → 变灰"HISTORY 已暂停"，点它跳回末尾并恢复跟随
```

- **track**：横条，量程 = 选中运行的 `[firstSeq, lastSeq]`。刻度点标在关键事件 seq 上
  （`plan_built` / 每个 `node_start` / `node_verified|failed` / `replan` / `task_done|failed`），hover 显示事件摘要。
- **playhead**：可拖拽圆点，映射到 seq K。位置变化 → `foldEvents(rows, feed≤K)` 重渲染。右侧 `K/last` 计数。
- **[▶] 播放/暂停**：自动把 K 从当前推进到 `lastSeq`，~300ms/事件（用事件个数步进，不用真实 `ts`，避免长 idle 卡住）。
- **LIVE 徽章**：K==lastSeq 且任务运行中 → 绿、跟随实时新事件；拖回 → 灰"HISTORY 已暂停"；点绿徽章/双击 track 末端 → 跳回跟随。
- **运行选择器**：下拉，列出本 boot feed 里的 `task_claimed` 运行（✓/✗ 上色）+ 更老的封存运行（来自 `session` 的 `task.plan_complete`，静态、无 scrub）。

---

## 5. Part C — 可懂化（命名 / 副标题 / 空状态 / tooltip）

### 5.1 面板改名 + 一行副标题（确切字符串）

| 位置 | 旧名 | 新名（短） | 一行副标题（plain 中文，展示在 tab 下方/面板头） |
|---|---|---|---|
| 合并图 tab | 任务图 + 执行图 | **执行图谱** | 计划 · 执行路线 · 当前步 · 结果，一张图看全 |
| 新视图 tab | —（新增） | **实验台** | 发一条 prompt，同屏看规划、执行过程和结果 |
| `evolution` tab | 演进 | **代际进化** | 每一代改动相对上一代的成绩变化和是否晋级 |
| `cards` tab | 机箱 | **能力卡** | 已装的技能/能力：驱动方式、是否需仿真、任务绑定 |
| `battle` tab | 战报 | **战报**（不改名，加副标题） | 最近战役的 held-out 结果：晋级判定与统计显著性 |
| `ledger` tab | 账本 | **账本**（不改名，加副标题） | 各 seed 区块的预算占用：已烧 / 预留 / 计划 |
| 实验台右栏 / ticker | 轨迹 | **过程流** | 本次任务的事件时间线：规划→节点→阶段→结果 |

> 改名只动 locale 字典的 value（`view.*` 键），不动 id（id 保持 `evolution/cards/battle/ledger`，避免持久化视图错乱）。
> `演进/机箱` 的锅是"抽象动词/硬件隐喻"：`代际进化` 用"代际"锚定到"一代一代"，`能力卡` 用"卡"对上 manifest 卡片，都更具体。
> 副标题字符串放各包 locale：`ui-ph-panels/src/client/locales.ts`（evolution/cards/battle... 注意 battle 在 `ui-ph-battle`）、
> `ui-ph-livegraph/src/client/locales.ts`（执行图谱/实验台/过程流）。加 `sub.*` 键，渲染在面板头部第一行。

### 5.2 空状态解释卡（每面板一张，说清"这是什么 / 为何空 / 怎么填"）

确切字符串（zh）：

- **执行图谱**（无运行）："还没有任务在跑。在下方输入框发一条任务（如 stack 抓取），这里会实时长出规划图和执行路线。"
- **实验台**（无会话/空闲）："这是实验台。左边是任务的规划与执行图，右边是执行过程时间线。发一条 prompt 就开始。"
- **代际进化**（无战役）："还没有战役数据。战役 = 一次针对某任务的多代自我改进；每代会打 dev/盲测/留出集分数并判定是否晋级。"
- **能力卡**（无卡）："还没读到能力卡。每张卡来自一个已装插件的 manifest.toml，描述它提供的技能、驱动方式与任务绑定。"
- **战报**（无战役）："runs/ 下暂无战役。战报汇总最近一次战役在 held-out（留出集）上的成绩与晋级判定。"
- **账本**（无预算）："STATUS.md 里还没有区块预算。账本按 seed 区块记录预算的已烧/预留/计划。"
- **过程流**（无事件）："任务一开跑，这里就按时间列出：规划完成→进入节点→阶段通过/失败→结果。"

### 5.3 术语 tooltip（hover 一行 plain 中文）

**复用 `ui-primitives/src/Tooltip.tsx` / `HoverCard.tsx`**（已存在，别自造）。给下列术语在其出现处包一个带 `?` 角标的 tooltip：

- **McNemar**："McNemar 检验：只看被改动'修对'和'改坏'的题，判断这代改动是不是真变好（而非运气）。"
- **held-out / 留出集**："留出集：训练时从没见过的题，用来诚实检验泛化，防止背答案刷分。"
- **晋级**："晋级：这一代通过了 dev / 盲测 / 留出集的门槛，被采纳为新基线。"
- **特权 / privileged**："特权能力：能执行高风险/越权操作（如直接驱动硬件）的能力，需额外授权。"
- **Δpp（dev/blind/held-out）**："Δpp：相对上一代的成功率变化，单位百分点。"
- **seed 区块**："seed 区块：一段任务种子编号范围；预算按区块分配和消耗。"
- **阶段通过率**："阶段通过率：所有节点的阶段里通过的比例。"
- **重规划 / replan**："重规划：某节点失败后，规划器重新出计划再试一次。"

字符串落在对应包 locale（`mcnemar.tip` 等键）。tooltip 触发用现成 `Tooltip`，无新依赖。

---

## 6. 组件 / 包计划

| 包 | 变更 |
|---|---|
| `ui-ph-livegraph` | **成为合并图核心 + 实验台的家。** ① 给 3 个节点加 handle（§2）；② 迁入 Evidence 面板、run 选择器（源自 CockpitView）；③ 深度：replan 血缘分支边、step/耗时/故障、routing 折叠 toggle；④ Scrubber/时间轴 + HISTORY 重放（feed 截断折叠）；⑤ 新 `SplitPane.tsx`（本地 ~30 行）；⑥ 新 `LabView.tsx`（`conversation.view` id=`lab`，= SplitPane(图, 过程流 ticker)）；⑦ 新 `TickerView`（过程流，读同一 feed）；⑧ 相册 tab 重命名 `执行图谱`，新增 `实验台`；⑨ locale 加 `sub.*` / tooltip 键。 |
| `ui-ph-ops` | **瘦身。** 删 `mission` 的 `conversation.view` 注册 + `CockpitView.tsx` / `MissionGraph.tsx` / `StatusNode.tsx` / `graphModel.ts`（有用件已迁走）。**保留** OperatorRail（`sidebar.section`）。清理 locale 里 mission-only 的键。 |
| `ui-ph-panels` | locale：`view.evolution`→`代际进化`、`view.cards`→`能力卡`；加 `sub.*`、空状态、tooltip 键。组件基本不动（只加副标题头 + 空状态文案 + tooltip 包裹）。 |
| `ui-ph-battle` | locale：加 `sub.battle`、held-out/McNemar tooltip 键。 |
| `ui-conversation` | **唯一框架级改动**：`resolveActiveView` 默认落 order-最小 tab（§7）。~3 行。 |
| host / board | **零改动**。所有数据走既有 11 个端点。 |

**实验台放哪**：放 `ui-ph-livegraph`（与合并图同包），这样 `LabView` 直接 import 同包的合并图组件与 ticker，
**不产生跨包耦合**（面板独立性/jscpd 规矩针对的是耦合与克隆；同包内共享组件是正常复用）。不新建包（省 scaffold/invariant/tsconfig/catalog）。

---

## 7. 需要的 seam 变更（就一处框架级）

**`ui-conversation/src/client/skeleton/ConversationSession.tsx`（`resolveActiveView`, 行 24-31）**：
默认视图从写死 `'chat'` 改成"注册项里 order 最小的那个 tab，若没有更小的再回落 `'chat'`"。

```
// 现在：const DEFAULT_VIEW_ID = 'chat'; requestedId = selectedId ?? DEFAULT_VIEW_ID
// 改成：selectedId 有效则用它；否则用 tabs 里 order 最小者（tabs 已按 order 排好，取 tabs[0]）；tabs 空则 'chat'。
function resolveActiveView(tabs, selectedId) {
  if (selectedId != null) { const hit = tabs.find(v => v.id === selectedId); if (hit) return hit }
  return tabs[0] ?? tabs.find(v => v.id === 'chat')
}
```

- 把 `实验台` 注册在 **order 18**（最左），它即成首屏默认；`对话`(chat) 仍是普通 tab，未被破坏。
- 纯展示改动（只影响"首屏默认高亮哪个 tab"），不碰数据/门禁数学，符合 charter。
- 影响面：PH build 里**所有**会话首屏默认落 `实验台`（无运行时它显示 idle 图 + composer，可接受）。非 PH build 没注册 `lab`，`tabs[0]` 仍是 chat，行为不变。
- **零改版回退**（若不想动框架）：不改 `resolveActiveView`，靠 `实验台` 是最左 tab，操作员点一下即到。两条都便宜，推荐改 seam。

除此之外**无需**其它框架缝改动：composer 已跨视图共享（prompt 免费）；`useSession` 视图可读；`details` 右栏原封不动（工具检查器）；
合并图/实验台用的 `sessions/session/runtimeEvents/sessionProgress` 都已在各自 slot 注册的 inject 面里（`ui-ph-livegraph/index.ts:48-52`）。

---

## 8. 构建顺序（每步提交 + 推 branch；每步可独立验收）

前置：`source ~/.nvm/nvm.sh && nvm use 22`；在专用 worktree/branch 上做
（`git -C .../ph-station worktree add <path> -b <branch> origin/main`）；scratch-serve 在 3580/3680/3780，杀自己的确切 PID；别碰 :3080。

1. **边修复（defect 1，先单独发）**：`ui-ph-livegraph` 三节点加 handle + 隐藏样式 + routing `sourceHandle='cap'` + `edgeActive` 动画边。
   验收：scratch build，发一个 stack 任务，肉眼确认边出现、当前执行边动画。**最小 diff、最刺眼收益。**
2. **深度（defect 2）**：迁入 Evidence 面板 + run 选择器；节点 step/耗时/故障；replan 血缘分支边；routing 折叠 toggle；特权徽章。
3. **模式 + scrubber（Part B）**：HISTORY 重放（feed 截断折叠）+ 时间轴 track + playhead + 播放/暂停 + LIVE 跟随；运行选择器。
   删 `ui-ph-ops` 的 `mission` tab 注册与 CockpitView 等原件；合并 tab 改名 `执行图谱`。
4. **实验台（Part A）**：`SplitPane.tsx` + `LabView.tsx`（图 | 过程流 ticker）+ `TickerView`；注册 `conversation.view` id=`lab`, order 18；响应式塌缩。
5. **默认落点（seam）**：改 `resolveActiveView`（§7）。
6. **可懂化（Part C）**：locale 改名/副标题/空状态/tooltip 键；面板头渲染副标题；jargon 用 `ui-primitives` Tooltip 包裹。
7. **门禁 + 验收**：`pnpm run typecheck`；`pnpm run duplication`（保留 `/* jscpd:ignore */` 面板独立标记，删原件而非克隆）；`pnpm run build`。
   scratch-serve :3580，主题明/暗都过一遍；验证：发 prompt→图长出→当前步高亮→ticker 同步→结束态；HISTORY 拖 scrubber 能重放；分栏拖拽 + 塌缩。
   SplitPane clamp 与 `foldEvents(feed≤K)` 各留一个 `assert`/`__main__` 或最小 `test_*` 自检（非平凡分支）。

---

## 9. 风险

- **对话不可嵌入** → 实验台用"共享 composer + 过程流 ticker"顶上"对话"位；完整 transcript 留在未动的 `对话` tab。
  若操作员坚持真实 transcript 与图并排，需嵌入 ChatView 全注入面（大改），**需显式确认**。已在 §3.2 标注可选"助手旁白"懒增强。
- **runtime_events 重启截断** → HISTORY scrub 只对当前 boot 的 feed 有效；更老的运行退化为静态封存快照（无 scrub）。诚实降级 + 空态文案。
- **React Flow 多 handle** → mission 节点有 2 个源 handle（`out` 给 plan 链、`cap` 给 routing）；routing 边必须带 `sourceHandle:'cap'`，
  否则边从底部乱绕。实现时按 §2.2 给每个 handle 显式 `id` 并在 edge 上引用。
- **默认视图 seam 影响全会话** → PH build 里所有会话首屏都落实验台（无运行=idle 图+composer，可接受）；非 PH build 不受影响（`tabs[0]` 仍 chat）。
- **scrubber 性能** → 每次 playhead 移动重折整段 feed，O(n)，n≈单次运行几十条事件，无压力。若未来 feed 巨大再加"折到 K 的记忆化"。上限已标注。
- **jscpd 面板独立门禁** → 迁移 Evidence/run-pills 到 livegraph 可能命中克隆。对策：**删** ops 原件（迁移非克隆），保留既有 `jscpd:ignore` 面板独立标记，SplitPane 用与 AppFrame 不同的最小实现（借模式不抄块）。
- **改名与持久化** → 只改 locale value、不改 view id，持久化的 `store.view` 不会错乱。
```
