# PH Flexible Graphs — 三个图面的语义缩放 / 折叠 / 最大化 / 布局 / 实时化 + composer 死带修复

深度设计。目标读者：零上下文的实现者。写完即可照着建。

所有 UI 路径均相对 fork 仓库根 `/home/yusenzlabpc/Desktop/ph-station`；三个图面都在
`packages/client/ui-ph-*`，走既有 board Remote 端点，**TS 只渲染、不算数**（charter：fork = 界面层；
图布局 / 分组 / 缩放 / 折叠属于展示，允许；成功率 / 门禁的数学不允许）。

**本轮关键结论（先说，省一半工作量）**：**零新依赖**。所有需求都能用**已装的**
`@xyflow/react@12.11.3`（`MiniMap` / `Controls` / `Panel` / `useStore` / `useReactFlow`）+
`@dagrejs/dagre` + `dockview-react` 的既有 API 做完。elkjs（EPL-2.0）与 framer-motion **都被否**
（§2，各有实测理由）。因此本轮是**纯 fork/TS 迭代**，门禁只有 `typecheck` + `duplication` +
`BUILD`（build 是更严的类型门，务必过）；**不触** THIRD_PARTY_NOTICES（没加包）。

---

## 0. 操作员反馈（原话）

1. "视觉效果不好，无法一个屏幕展示出所有东西或者说看不清楚，你需要思考一种让框图更加 flexible 的展示方法"
3. 截图显示 composer 上方那条**白带拖不动**（固定 `--dsh-composer-height` 预留出一段操作员无法回收的死空间）。
4. "把 skill，执行图谱 graph 和 blueprint 做的更加 flexible，交互更好，更加实时，uiux 也做的更加人性化，
   多使用外部的库和 svg 去提升效果。"

> 注（4）"多使用外部的库"：本轮实测发现**该用的外部库早已在依赖里**（xyflow 自带 MiniMap/Controls/Panel/
> useStore；dockview 自带 maximize）。真正缺的不是新包，是**没用满已装包的能力**。加 elkjs/framer 只增体积不增能力（§2）。

---

## 1. 实测证据（live :3081，session-main 上真实跑着的 11 节点 `inventory_build`）

在默认 dockview 布局里驱动 live 控制台，对 `执行图谱` 面板取值（`.react-flow` / seat / stage）：

| 量 | 实测值 | 含义 |
|---|---|---|
| `执行图谱` 画布尺寸 | **818 × 124 px** | 右列上半格，高只有 124px |
| React Flow viewport | `translate(-701.9px, 12.5px) scale(0.6)` | 缩放**顶死在 `FIT_MIN_ZOOM = 0.6` 地板**，链条被平移 −702px → 右侧节点全在屏外 |
| 节点数 | 12（mission + 11 plan） | 一行 dagre LR，宽 ≈ 12×(198+20) ≈ 2600px |
| 单节点渲染 | 138 × 40 px（0.6×） | 卡片被**上下裁切**，只剩中段 verify_grasp…pick-milk 一条横带可见 |
| `执行图谱` MiniMap / Controls | **都没有**（DOM 实测 false） | 无缩略图、无 fit/缩放按钮 → 操作员无总览、无手段拉回 |
| `技能库` MiniMap / Controls | **都有** | vault 已是装备最全的面 |
| composer seat `offsetHeight` | **160 px** | ConversationRoot 的 ResizeObserver 实测发布 |
| `--dsh-composer-height` | **160 px** | ＝ seat 实测（152 只是首帧 fallback，**不是**一直用固定值） |
| dash `.stage` `padding-bottom` | **160 px** / stage 高 519px | 预留吃掉 **31%** 竖向空间；右列两格再分剩下的 359px |
| dockview `.dv-right-actions-container` | **存在但空** | maximize 容器在，只是**没接按钮** |

**读法**：白带不是"测错高度"（它确实等于 composer 实高），而是这段预留是**硬的、不可回收**——
操作员想把这 160px 拖给 dock 也没有把手（§9）。图谱看不清也不是缩放算错——是 **11 节点单行本就 2600px 宽**，
塞进 818×124 的格子只能顶着 0.6 地板溢出（§7 换行才是正解）。

---

## 2. 横切裁决：用哪些库（复用 / 否决）

**复用（已装，零新增）**

| 能力 | 用什么（已装） | 现状 |
|---|---|---|
| 语义缩放 LOD | `useStore(s => s.transform[2])` 读实时 zoom | 未用 |
| 缩略图 | `<MiniMap>` | vault 已用，livegraph 缺 |
| fit/缩放按钮簇 | `<Controls>` + `<Panel position="top-right">` | vault 已用，livegraph 缺 |
| 命令式 fit/居中 | `useReactFlow().fitView()/setCenter()` | livegraph 已用 fitRef |
| 面板最大化 | dockview `panel.api.maximize()` / `group.api` | 容器在、没接 |
| 状态过渡 / 入场动画 | CSS `transition` + `@keyframes` + xyflow 自带 `animated` 边 | 部分用 |

**否决**

- **elkjs（EPL-2.0）**：许可本身可接受（弱 copyleft，文件级，不感染 app），但**没必要**。plan 链是
  线性 1×N，elk 的 layered 单趟照样输出一条长行——真正的可读性来自**多行换行**（§7），那是 dagre 输出后
  一个手写后处理，几十行、零依赖。vault 是有扇入扇出的真 DAG，但它已 network-simplex + 默认藏稠密关系 +
  只对 focus 边正交路由，够用。→ **按"无当前需求"否，不是怕许可**。
- **framer-motion（~30–50KB gzip）**：本面需要的动画只有三种——(a) 节点状态变色、(b) 执行中边流动、
  (c) 新子目标节点入场。三种都是纯 CSS（`transition` / xyflow `animated` / `@keyframes`）。framer 增体积、零能力增益 → **否**。

> 若将来真要装外部库：走 `scripts/gen-third-party-notices.ts` 自动归属（本轮不涉及）。

---

## 3. 三个面今天是什么（改造起点）

| 面 | 文件 | 今天 | 缺的 flexible 能力 |
|---|---|---|---|
| **执行图谱** | `ui-ph-livegraph/src/client/{LiveGraphView.tsx,graph.ts}` | 单行 dagre LR + scrubber + Evidence 卡；无 MiniMap/Controls/maximize；顶死 0.6 | LOD、换行、缩略图、缩放簇、最大化、折叠 replan、CSS 过渡 |
| **技能库** | `ui-ph-vault/src/client/{VaultGraphCanvas.tsx,graph.ts}` | 全局 dagre LR + MiniMap + Controls + focus/dim + 折叠图例；默认藏 REQUIRES/PROVIDES | LOD、按谱系折叠、maximize |
| **任务小图 / blueprint** | `ui-ph-ops/src/client/OperatorRail.tsx`（`MissionCard`） | rail 里的**静态卡**：run 点排 + node→stage chips，折叠成点 | 折叠 stage、点击 pill 联动 `执行图谱`（当它的"最远 LOD 常驻总览"） |

三面共享同一份 plan 数据（`plan_built` / `plan_complete`）。`任务小图`就是`执行图谱`的**最远 LOD**——设计上让它们
语义一致（§4），点 `任务小图` 的 pill → 在 `执行图谱` 里 `setCenter` 到该节点。

---

## 4. 决策 1：语义缩放 / LOD（按 zoom 换渲染粒度）

在自定义节点组件里读 `const z = useStore(s => s.transform[2])`（xyflow store，零依赖），按阈值切三档。
**关键：dagre 布局始终用 near 档（最大）footprint**，位置不随 zoom 变——LOD 只换**内容**，不重排。远档的
pill 居中坐在预留框里即可。

**阈值（三面统一）**

| 档 | zoom 区间 | 语义 |
|---|---|---|
| **far** | `z < 0.55` | 总览：只认状态和身份 |
| **mid** | `0.55 ≤ z < 0.9` | 导航：名字 + 状态 + 关键计数 |
| **near** | `z ≥ 0.9` | 详情：全卡 |

**执行图谱 · 每档内容**（`plan` 节点，near footprint 仍 198×104）

- **far**：一颗**状态色药丸**——`STATUS_CLASS` 底色 + 节点 id 尾段（`grasp-cube`→`grasp`）；running 加脉冲环；
  replan ≥1 加小三角。stage/计时/predicate 全隐。→ 11 节点整链一屏可读。
- **mid**：`skill` 名 + `id` + 一枚 stage 汇总徽 `✓3 ✗1`（不铺开单 chip）+ running 的 `▶` 游标。
- **near**：现状全卡——铺开 stage chips、`步数/秒/faults`、`⊨ predicate`。

**技能库 · 每档内容**（复用 `KIND_COLOR` 三色 + 三 silhouette）

- **far**：silhouette 剪影 + kind 字形（bulb/box/plug），无文字；skill 用 status 次通道描边。
- **mid**：字形 + `label`（skill）/`name`（package）/`id`（capability）+ skill 的 `status` chip。
- **near**：现状全卡——`gen/Δ`、`priv`、mono id。

**任务小图**不参与 xyflow zoom——它**恒等于 far 档**（rail 常驻总览）。

实现：三面各加一个 `useNodeLOD()`（本地 3 行 hook，读 `transform[2]` 返回 `'far'|'mid'|'near'`）。
`ponytail: 阈值是硬编码常量，属展示常量非部署可变项，允许；真要调再提 config。`

---

## 5. 决策 2：折叠 / 展开（每面）

**执行图谱**

- **stage chips 折进节点头**：node 头显示 `✓3 ✗1` 汇总徽；**单击节点** = 就地展开/收起 stage strip（本地
  `Set<expandedKey>`）；**双击** = 打开 Evidence 卡。（今天是单击开 Evidence——改成双击，单击让给折叠。）
- **replan 谱系折进 head**：一个逻辑 id 的多次 attempt（`stack-0#0/#1/#2`）默认**折成一颗 head**，带
  `×N 试` 徽；单击 head 就地把失败的旧 attempt 展成分支（现有 `branch` 边 + `replanned` 琥珀色已具备，只是
  改成默认折叠）。→ 12 节点在最坏 replan 下也不炸成一片。

**技能库**

- 已有 focus/dim + 折叠图例，保留。
- **按谱系折叠**：一条 `DESCENDS_FROM` 链（child→parent）默认折进它 promoted 的 head，带 `+N 代` 徽；
  单击展开该家族。capability 带靠边不动。dense 关系（REQUIRES/PROVIDES）继续默认藏（现状）。

**任务小图**

- `MissionCard` 里每个 node 的 stage chips 默认折成计数 `report ✓ · 3 步`；点 node pill 展开 stage 行
  **并**在 `执行图谱` 里 `setCenter` 到它（跨面联动，见 §3）。rail 折叠成点时维持现状。

---

## 6. 决策 3：面板最大化 + 缩放簇

**Maximize（dockview 既有 API，容器已在只是空）**

- 在 `ui-ph-dash/DashView.tsx` 的 dockview 上，给每个 group 的 `rightHeaderActionsComponent` 接一枚
  **⤢ 最大化** 按钮：点→`group.api.maximize()`（或 `api.maximizeGroup(group)`）；已最大化时图标变 ⤡ →
  `api.exitMaximizedGroup()`。**Esc** 全局监听 → `if (api.hasMaximizedGroup()) api.exitMaximizedGroup()`。
- 语义：一键把`执行图谱`占满整个 stage（在 519px→接近全高时，11 节点换行后彻底可读，见 §7）。布局本就持久化
  （`ph.dash.layout.v1`），maximize 是运行态、不进 `toJSON`，Esc/再点即还原。

**缩放簇（每个图都要）**

- `技能库`已有 `<Controls>`——保留。
- `执行图谱`**补齐**：加 `<Controls showInteractive={false}>`（fit / +/− / 1:1）+ 一个
  `<Panel position="top-right">` 放三枚小按钮：**Fit**（`fitView(fitOpts)`）、**100%**
  （`setViewport 到 zoom=1` 居中 running 节点）、**Fill**（换行密排后 fit 到满宽，见 §7）。
  今天 livegraph 只有内部 `fitRef`，没有可见控件——操作员顶到 0.6 后**没有任何把手拉回**，这是硬伤。

---

## 7. 决策 4：布局引擎（dagre 够不够？）

**结论：三面都留 dagre，`执行图谱`加一趟"蛇形换行"后处理；elkjs 否。**

`执行图谱`根因（§1 实测）：11 节点单行 = 2600px 宽，塞 818px 格子必顶 0.6 地板、右侧溢出。elk 的 layered
单趟对**线性链**照样出一条长行——换 elk 不解决"一行太宽"。真正的解法是**把长链折成贴合面板宽高比的多行**：

- dagre 先照常出 LR 坐标（拿到每节点 rank 顺序）。
- 后处理：`perRow = max(2, floor(paneW / (nodeW + nodesep)))`（用 `canvasRef` 实测宽，ResizeObserver 里已有）。
  把 plan 链按 rank 顺序切成每行 `perRow` 个，**蛇形（boustrophedon）**：偶数行左→右、奇数行右→左，
  于是行间连边总是"上一行末 → 下一行首"，短且不交叉。
- 行 y = `row * (rowH + rankGap)`；行内 x 按方向铺。mission 单独坐首格。routing 扇仍挂 mission 下方（现状）。
- 触发点：`layout()` 多收一个 `wrapWidth?: number`；LiveGraphView 的 ResizeObserver 已在测宽，把宽传进
  `useMemo(() => layout(model, showRouting, paneW), [..., paneW])`。窄格 → 多行；maximize/宽格 → 单行（perRow 变大自动摊平）。

这样 11 节点在 818px 下折成 ~4 行、每节点回到接近 1:1 缩放，**一屏读全**——直接答复反馈 (1)。

`技能库`：真 DAG，dagre `network-simplex` + 默认藏稠密关系 + focus 正交，够用，**不动**。
`任务小图`：非 xyflow，rail 里的 flex 换行，天然多行，**不动**。

> elkjs 复核：EPL-2.0 可接受（不感染 app），但对这两种图形（线性链 / 已收敛 DAG）零边际收益、+bundle → **否**。
> `ponytail: 蛇形换行是手写启发式，天花板是"极端不均衡 rank 下换行可能留空档"，真出问题再考虑 elk。`

---

## 8. 决策 5：实时手感（CSS-only + xyflow 自带；为 M7 步级流设计）

M7 常驻任务会在**同一条 `runtime_events` feed** 上流式吐 in-episode 子目标事件。`graph.ts` 的 fold 本就
**增量追加**节点/stage（cursor 逐条折），所以前端只需让"新出现"和"状态变"有过渡：

- **状态变色**：`.node` 上 `transition: background-color/border-color/box-shadow 180ms ease`。pending→running→
  verified 的 `STATUS_CLASS` 切换自动补间。
- **执行中边流动**：`animated: e.active === true` 已在用（xyflow 自带 dash 动画），保留。
- **running 脉冲**：running 节点 `@keyframes pulse`（box-shadow 呼吸环），far 档药丸也用它 → 一眼看到"当前步"。
- **子目标入场**：新节点/新 stage chip 以 `key={node.key}` 挂载时跑一次 `@keyframes fadeInUp`（120ms，
  `prefers-reduced-motion` 下禁用）。fold 追加即触发，无需额外事件。
- **自动跟随当前步**：running 节点位置变化时 `useReactFlow().setCenter(x, y, {duration: 300})`（在 LIVE 且
  未手动平移时；节流 ≥300ms，避开 300ms-RTT 抖动）。HISTORY/scrub 模式不跟随。

framer-motion 不引入（§2）。全部 CSS + xyflow 内建，bundle 0 增。

---

## 9. 决策 6：composer 死带 → 可拖拽 sash（反馈 3）

**实测再述**：预留 160px ＝ seat 实高 ＝ `--dsh-composer-height`，占 stage 31%。测量**没错**，错在**硬、不可回收**。

**机制：一个变量 + 一根 sash，绝不重叠。**

- 新增 CSS 变量 `--dsh-dock-reserve`，**clamp 到 `[MIN, 实测 composer 高]`**：
  `MIN = 56px`（只留裸输入条），MAX = `--dsh-composer-height`（seat ResizeObserver 实测，随 composer 自适应）。
- `ui-ph-dash/DashView.module.css` 的 `.stage`：
  `padding-bottom: var(--dsh-dock-reserve, var(--dsh-composer-height, 152px));`
- composer seat（`ConversationRoot` 的 `[data-composer-seat]`）：`max-height: var(--dsh-dock-reserve)` +
  可折叠行（`conversation.input.dock` 的目标/快捷 chip 行）随之 `overflow: hidden` 收起——**textarea 在栈底，
  永远保留**。因为预留和 seat 高**读同一个变量**，二者永不失配、dock 永不被 composer 盖住。
- **sash**：在 `.stage` 底边（dock 与 composer 交界）放一条 6px 抓手（`cursor: ns-resize`）。指针拖动写
  `--dsh-dock-reserve = clamp(MIN, drag, MAX)`；`pointerup` 存 `localStorage['ph.dash.reserve.v1']`（每 workspace）。
  双击 sash = 还原到 MAX（满 composer）。
- 未设过 override 时 `--dsh-dock-reserve` 缺省回落 `--dsh-composer-height`（现状行为，零回归）。

于是操作员**向下拖** = 收起 composer 快捷行、把最多 ~104px 还给 dock（图谱 124px→~228px，直接翻倍可读）；
**向上拖** = 恢复满 composer。这答复反馈 (3)："让白带拖得动、能回收"。

`ponytail: sash 只调一个 CSS 变量 + 一个 localStorage 键，不碰 dockview 序列化、不新增面板。`

---

## 10. 落地清单（按文件）+ 门禁

**执行图谱**（改动最大，主答反馈 1）

- `graph.ts`：`layout(model, showRouting, wrapWidth?)` 加蛇形换行后处理（§7）。
- `LiveGraphView.tsx`：① `useNodeLOD` + 三档节点渲染（§4）；② 补 `<Controls>` + `<Panel>` 缩放簇（§6）；
  ③ 单击折叠 stage / 双击 Evidence + replan head 折叠（§5）；④ ResizeObserver 把 `paneW` 喂进 `layout`；
  ⑤ CSS 过渡/脉冲/入场 + `setCenter` 跟随（§8）。
- `LiveGraphView.module.css`：`@keyframes pulse/fadeInUp`、`transition`、far 药丸样式、`prefers-reduced-motion`。

**技能库**

- `VaultGraphCanvas.tsx`：`useNodeLOD` + 三档剪影内容（§4）；`DESCENDS_FROM` 家族折叠（§5）。MiniMap/Controls 已有。

**任务小图**

- `OperatorRail.tsx`：`MissionCard` stage 折成计数 + 点 pill 展开并联动 `执行图谱`（§3/§5）。

**dash 宿主**

- `DashView.tsx`：group `rightHeaderActionsComponent` 接 maximize 按钮 + Esc 还原（§6）；`.stage` sash + 
  `--dsh-dock-reserve` + localStorage（§9）。
- `DashView.module.css`：`.stage` 改读 `--dsh-dock-reserve`；sash 样式。
- `ConversationRoot`（`ui-conversation`）：seat `max-height: var(--dsh-dock-reserve)` + 可折行 overflow（§9）。
  （唯一动到 `ui-ph-*` 之外的一处；纯 CSS 变量接线，无逻辑改动。）

**门禁**：`source ~/.nvm/nvm.sh && nvm use 22` → `typecheck` + `duplication` + `BUILD`（build 更严，必过）。
无新依赖 → **不动** THIRD_PARTY_NOTICES。**每一步提交实时推送**。headless 验收四档（normal / 300ms-RTT /
hidden-tab / both），live 重启用精确 PID 杀 :3080 node → `physical-harness && nohup scripts/cockpit
--trusted-host 172.26.112.106:3081`。

**验收断言（对着 11 节点 `inventory_build`）**：① `执行图谱`默认布局下 11 节点换行后 zoom ≥ 0.9、无卡片裁切；
② 有可见 fit/缩放簇与 MiniMap；③ group 可一键最大化、Esc 还原；④ sash 可拖、composer 收起后 dock 变高、
刷新后保留；⑤ LIVE 下新 stage 有入场动画、running 有脉冲、视口跟随当前步。
