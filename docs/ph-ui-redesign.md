# PH Console UI Redesign — Operator Rail + Mission Cockpit

Design-phase spec for the ph-station console (the fork that renders; stats stay
in Python `board/`). Verdict driving this: the current console is *functional
but not native enough for an agentic OS*. Four panels + a status strip exist as
**center tabs** you flip between — nothing about the running mission is visible
*at a glance*. This spec adds a persistent operator surface and a graph-first
mission cockpit, reusing the fork's plugin/slot discipline and the existing
board Remote. Prototype work lives on branch `ui-redesign` in the ph-station
worktree.

Status: **design only.** No code in this doc is committed. The build plan (§7)
is for the Prototype-phase agent.

---

## 1. What exists today (verified against the live console :3080)

**Frame** (`ui-layout/AppFrame.tsx`, registered into the built-in `root` slot) is
three columns + an overlay:

```
┌ sidebar ┬──────────── conversation (center) ────────────┬ details ┐
│ brand   │  chat  |  [战报][演进][机箱][账本] view tabs    │ tool    │
│ workspc │                                                 │ detail  │
│ …       │                                                 │ (session│
│ settings│                                                 │  scoped)│
└─────────┴─────────────────────────────────────────────────┴────────┘
  status strip (shell.overlay, root, always-on) ───────────────────────
```

- `sidebar` — single, root scope. Owned by `ui-sidebar/SidebarRoot`. Sub-slots:
  `sidebar.brand.mark|name`, `sidebar.workspaces` (single), `sidebar.settings`,
  `sidebar.footer.action` (list). Collapses to an icon rail on narrow / toggle.
  **No general-purpose panel slot exists here yet.**
- `conversation` (center) — chat + `conversation.view` (list, **session** scope).
  The five PH panels are tabs here: 战报 (`ui-ph-battle`), 演进/机箱/账本
  (`ui-ph-panels`).
- `details` (right) — single, **session** scope; the per-tool inspector. Resizable,
  collapses when no session. Good for contextual drill-down, wrong for always-on.
- `shell.overlay` (list, root) — the bottom status strip (`StatusBar`).

**Panels are pure consumers.** Every number comes from the board Remote; TS does
zero statistics (charter hard rule). Visual language: neutral, theme-aware via
`color-mix(in srgb, currentColor N%, transparent)` — no palette of its own.
Established status colors, reused everywhere: green `#16a34a` (pass/online),
red `#dc2626` (fail/burned), amber `#d97706` (reserved/pending). `tabular-nums`
for numbers, `ui-monospace` for shas/paths. 演进 already hand-rolls Δpp bars in
CSS (`barFill`/`barPos`/`barNeg`).

**Stack:** React 18.3, tsdown bundle, cordis slots, all `@deepseek-ai/dsh-*`
MIT. Poll cadence `POLL_MS = 15000`, paused while tab hidden (`usePolledLoad`).

**Board Remote** (`packages/host/dsh-ph-board`, execFile → `board/storecli.py`,
gateway serves each at `POST /api/board/<fn>`; MCP mirror in `board/mcp_server.py`,
byte-equivalence tested):

| Remote method | storecli fn | Returns (verified) |
|---|---|---|
| `stores()` | `list_stores` | campaign stores, newest-first: kinds, task, generations, promoted, heldout |
| `store({name})` | `store` | one campaign: preregistration, generations, stage_attribution, campaign_result, ablation |
| `heldout({name})` | `heldout` | held-out block + rescores |
| `cards()` | `cards` | installed plugin manifests (机箱) |
| `rounds()` | `rounds` | progress.md `## Round N` feed |
| `ledger()` | `ledger` | STATUS.md seed-block burn table |
| `sessions()` | `sessions` | runtime sessions, newest-first: `{name, mtime, chain_ok, kinds{}, skipped}` |
| `session({name})` | `session` | **rows grouped by kind** (see §2) — the mission-graph source |
| `runtimeStatus({name})` | `runtime_status` | live `runtime_status.json` (pid/render/mode) or `null` |

The Remote namespace is assembled in `packages/api/remotes/src/client/index.ts`
(`import boardRemote from '@deepseek-ai/dsh-ph-board/remote'`). Adding a method =
add it to the host `@Remote` class; the type merge flows automatically.

---

## 2. The data already carries the mission graph

`board/session` with `{"args":{"request":{"name":"session-main"}}}` returns
`rows` keyed by note kind. Live shapes (session-main today):

```jsonc
// task.plan_complete  (one per completed task — the mission→task→stage tree)
{ "goal": "stack cubeA on cubeB", "success": true, "actuations": 1,
  "replans": 0, "faults": [],
  "nodes": { "stack-0": { "success": true,
    "stages": [ {"name":"grasp","success":true}, {"name":"place","success":true} ] } } }

// capability.resolve  (the wiring graph: which provider served each capability)
{ "capability":"task.planner", "consumer":"task", "privileged":false,
  "ref":"plugins.task.planner_stack:provider" }
// …graph.scene, graph.skill, embodiment.env, policy.driver, exec.rollouts,
//   reasoner.proposer, percept.model — 7-8 capability nodes per task

// capability.provide  (providers offered at mount)   kernel.mount { plan_sha }
// runtime.boot { mode, mount_plan_sha, skills_manifest }
// runtime.task_error { task, brief, error }          ← faults, e.g. injected-key soak
```

So the **mission→task→stage graph is fully present** in `session({name})`. The
**capability-wiring graph** (task.planner fanning out to scene/skill/embodiment/
policy/executor/reasoner) is present in `capability.resolve`. What is *not*
present as a first-class read: an **aggregate** (tasks/succeeded/failed/replans/
stage-pass-rate) and a **live in-flight** progress row. Those are the two gaps
(§6) — aggregation is added in Python, never in TS.

`runtimeStatus(session-main)` returns `null` today (this runtime doesn't write
the live file) — the design shows an honest "no live status" state, never fakes it.

---

## 3. Information architecture — what an operator needs at a glance

An agentic-OS operator watching a robotics harness wants, without clicking:

1. **Where is the mission?** mission → task chain → stages, each colored by
   execute + verify status; the currently-running node obvious.
2. **Is it making progress?** tasks done / total, success rate, replans, faults.
3. **Is the machine healthy?** MODE (execution vs evolution — the two-layer
   firewall from GOAL v4.1), heartbeat, mounted skills, mount-plan sha, viewfinder.
4. **Is it getting better?** latest generation Δpp + latest round headline.
5. **Drill on anything** → the evidence behind it (paired gate, held-out, oracle).

Map to surfaces:

```
┌ sidebar (LEFT) ────────┬──────── conversation (CENTER) ─────────┬ details (RIGHT) ┐
│ brand / new session    │  chat                                   │  node evidence  │
│ workspaces             │  ┌ view tabs ─────────────────────────┐ │  drill-down     │
│ �new▶ OPERATOR RAIL     │  │[任务图][战报][演进][机箱][账本]     │ │  (reuse Battle/ │
│  ┌───────────────────┐ │  │                                     │ │   store detail) │
│  │ ● Mission mini-map│ │  │   MISSION COCKPIT (React Flow):     │ │                 │
│  │   stack-0 ✓ ✓     │◀─┼──│   goal ▸ task ▸ stage DAG          │ │  ← click node   │
│  ├───────────────────┤ │  │   + capability wiring lane          │ │    on the graph │
│  │ ▤ Progress        │ │  │   + chain-event timeline strip      │ │                 │
│  │   3/4 ✓  1 fault  │ │  └─────────────────────────────────────┘ │                 │
│  ├───────────────────┤ │                                          │                 │
│  │ ♥ Runtime vitals  │ │                                          │                 │
│  │   MODE exec · 12s │ │                                          │                 │
│  ├───────────────────┤ │                                          │                 │
│  │ ↗ Evolution       │ │                                          │                 │
│  │   g2 +8pp · R99   │ │                                          │                 │
│  └───────────────────┘ │                                          │                 │
└────────────────────────┴──────────────────────────────────────────┴─────────────────┘
  status strip: MODE · session · ♥ 12s · skills 0 · plan 8dd5252b · board ●online
```

- **Left sidebar = the "richer sidebar of panels"** the verdict asked for. New
  `ui-ph-ops` plugin renders four stacked, collapsible cards into a new
  `sidebar.section` list slot (§4). Persistent, always-on. Collapsed rail →
  status **dots + counts only** (mini-map becomes 4 status dots; vitals becomes
  a MODE dot + heartbeat). This is the one core seam the redesign adds.
- **Center = Mission Cockpit**, a new `conversation.view` tab `任务图`: the
  full-size interactive graph + timeline. Zero core change (list slot already
  open). The existing four tabs stay, restyled to the shared language.
- **Right `details` = evidence drill-down.** Clicking a graph node routes here
  (session-scoped is correct for drill-down). Reuse `ui-ph-battle` / store detail.
- **Bottom strip stays** as the always-on minimal heartbeat for when the rail is
  collapsed; vitals card is the rich version.

### Collapsed-rail states (mini-map / vitals when sidebar is an icon rail)

```
 ●●   ← mission: one dot per task node, colored by rollup status
 ▤ 3/4
 ♥ ●  ← MODE dot (green=execution frozen, violet=evolution) + heartbeat age
 ↗ +8
```

---

## 4. Interaction model

- **Click node** (cockpit or mini-map) → select → open evidence in `details`:
  - *task node* → stages list, replans, faults, actuations, goal.
  - *stage node* → verify/oracle result; if a campaign covers it, link to its
    `store({name})` (stage_attribution / paired gate / held-out badge).
  - *capability node* → provider `ref`, `params`, `privileged` flag.
- **Hover** → tooltip: `ref`, sha, counts.
- **Session switch** — rail header dropdown from `sessions()` (session-main =
  execution, session-evolution = campaign). Everything below rebinds to it.
- **Live refresh** — reuse `usePolledLoad` / `POLL_MS = 15000`, hidden-tab pause.
  Honest human cadence; the evidence layer changes at run/edit speed.
- **MODE is a first-class signal** — execution vs evolution is the GOAL v4.1
  firewall (execution mounts are frozen archives; only evolution experiments).
  The vitals card colors MODE distinctly and labels it, so an operator never
  confuses a frozen-execution session with a live-experiment one.

### Node status → color (reuse the established three)

| Node state | derived from | color |
|---|---|---|
| success | `success:true` | green `#16a34a` ring |
| failed | `success:false` or task in `runtime.task_error` | red `#dc2626` |
| replanned / awaiting verify | `replans>0`, or stage run but verify pending | amber `#d97706` |
| planned / not yet run | in plan, no result row | neutral, dashed (currentColor low-α) |

Keeps the cockpit visually identical in vocabulary to 战报/演进/账本.

---

## 5. Component choices (external deps sanctioned for visual quality)

In-repo check first: **no chart/graph lib exists in the app** (only `website/`
VitePress has unrelated deps). 演进 hand-rolls bars in CSS. So every dep below is
net-new and justified against the ladder.

### 5.1 Graph rendering — **`@xyflow/react` (React Flow v12)** ✅ primary

- **License MIT**; maintained by the xyflow team (the successor package to the
  old `reactflow` v11 name — v12 is the current line). React 17/18 compatible
  (matches our React 18.3).
- **Why it earns a dependency:** the redesign is graph-first and interactive —
  custom status-colored nodes (execute/verify), pan/zoom, controlled selection
  for click→drill, edge routing, a minimap. Hand-rolling all of that in SVG is
  the larger diff *and* worse a11y/interaction. React Flow's custom-node API maps
  cleanly onto our `StatusNode`.
- **Honest ceiling:** today's `task.plan_complete` tree is tiny (1 task, 2
  stages). React Flow is heavier than SVG for *that alone*. It earns its keep on
  (a) the 7-8-node capability-wiring graph, (b) multi-task missions as the planner
  matures (GOAL M3/M4 StageSpec chains), (c) the interaction model. **Fallback if
  bundle size is contested:** a hand-rolled SVG DAG for the mini-map only, React
  Flow reserved for the center cockpit.
- **Rejected alternatives:** `reactflow` v11 (older name, use v12);
  `cytoscape`/`cytoscape-react` (powerful but heavy, non-React idiom, larger
  bundle — overkill for structured DAGs); `@visx/network` (low-level, more owned
  code); `react-force-graph`/reagraph (WebGL force layout — wrong idiom for a
  small deterministic DAG); raw `d3` (we'd own rendering + interaction).

### 5.2 Layout — **`@dagrejs/dagre`** ✅ (with `elkjs` as the upgrade)

- React Flow does **not** auto-position; it needs computed coordinates.
- **`@dagrejs/dagre`** — **MIT**, small, layered DAG layout — is more than enough
  for our node counts (single-digit to low-dozens). The lazy pick.
- **`elkjs`** — **EPL-2.0** (charter prefers MIT/Apache) — only if we later need
  nested containers (mission⊃task⊃stage as sub-flows) or port-accurate routing.
  Note the license divergence; defer until the graph actually needs it.
- **Rejected:** `d3-dag` / `d3-hierarchy` (layout only, no interaction — we'd
  still need a renderer, so pairing it with React Flow buys nothing over dagre).

### 5.3 Charts — **none new; hand-roll CSS/SVG** ✅ (deferral is the choice)

- The redesign's quantitative viz is small: Δpp bars (already exist in 演进),
  success-rate meters, a heartbeat/round sparkline, the timeline strip. The
  existing CSS bar pattern is theme-aware and works. Adding Recharts/visx for a
  handful of bars is over-engineering.
- **Add a chart lib only when** real curves arrive (ablation decay curves per
  GOAL v4.2 ④, McNemar-over-time). At that point: **Recharts (MIT)** for
  declarative categorical/line charts, or **uPlot (MIT, tiny)** for dense
  time-series. Not now.

### 5.4 Icons / misc — reuse repo convention

`ui-sidebar` already ships icon components (`IconPanelLeftOutline16`, `FishLogo`)
and a `Tooltip`. Rail section icons and tooltips reuse those — **no icon dep.**

### Shortlist (the answer)

1. **`@xyflow/react` (React Flow v12)** — MIT — mission/task/stage + capability
   graphs, custom status nodes, pan/zoom, click-to-drill, minimap. *Primary.*
2. **`@dagrejs/dagre`** — MIT — layered auto-layout feeding React Flow. *Small,
   sufficient; `elkjs` (EPL-2.0) only if nested/ported layouts appear.*
3. **Charts: none** — hand-roll CSS/SVG (reuse 演进 bars). *If curves later:
   Recharts (MIT) or uPlot (MIT).*

Total net-new runtime deps for the prototype: **two** (both MIT).

---

## 6. Data requirements — exists vs. add

**Sufficient as-is** (no backend change): `sessions`, `session` (mission graph,
wiring graph, faults, boot/MODE, plan_sha), `runtimeStatus` (live vitals, honest
`null`), `stores`/`store`/`heldout` (evidence drill-down + evolution), `cards`,
`ledger`, `rounds`.

**Two gaps → add Python read fns** (charter: aggregation lives in `board/`, TS
renders only; every new fn takes the **storecli + MCP dual face** with the
byte-equivalence test, matching `cards.py`):

1. **`session_progress(name)`** — aggregate the session's `task.plan_complete`
   rows into `{ tasks, succeeded, failed, replans, faults, stage_pass_rate,
   latest: {task, goal, nodes} }`. Feeds the **Progress card** and the cockpit
   header. Keeps the success-rate / fault math in Python, not TS. *(Fold the
   latest-task stage list in here so the pipeline view needs no second call.)*
2. **`session_pipeline(name)`** *(optional; fold into #1 if trivial)* — the
   latest/in-flight task's stage-by-stage execute+verify state, shaped for the
   pipeline panel, so TS does no row reshaping. If the resident runtime does not
   yet log interim (in-flight) stage events, this returns the **last sealed**
   plan + an `idle`/`awaiting` marker — the panel must not invent live state.

Wiring each new fn (mirror the existing pattern exactly):
- `board/store.py` — the aggregation function.
- `board/storecli.py` — add to `dispatch()` (name-addressed, `safe_child`).
- `board/mcp_server.py` — add the mirrored `@mcp.tool()`.
- `tests/` — byte-equivalence test (CLI stdout == MCP return), as for `cards`.
- `dsh-ph-board/src/index.ts` — add `@Remote('sessionProgress')` calling
  `run('session_progress', request.name)`; reuse `BoardSessionRequest`.

No new brief/write path. Reads only.

---

## 7. Build plan (Prototype-phase agent)

Order chosen so each step is independently verifiable. Motherboard (`board/`,
`tests/`) commits to physical-harness; fork UI commits to **branch `ui-redesign`**
in the ph-station worktree (never main — a second workflow owns main).

1. **Backend read fn.** Add `session_progress` to `board/store.py` +
   `storecli.py` dispatch + `mcp_server.py` tool + byte-equivalence test. Verify:
   `python -m board.storecli session_progress session-main --runs runs`.
2. **Host Remote.** Add `sessionProgress` `@Remote` to `dsh-ph-board`; rebuild so
   `api/remotes` picks up the type merge. Verify: `curl POST /api/board/sessionProgress`.
3. **New client package `@deepseek-ai/dsh-client-ui-ph-ops`.** Scaffold from
   `ui-ph-panels` (package.json, tsconfig, tsdown.config, `invariant.ts`,
   `locales.ts` zh/en, `css-modules.d.ts`, `src/client/index.ts`). Deps:
   `@xyflow/react`, `@dagrejs/dagre`. `dsh.client.inject` = runtime, api-remotes,
   locale, ui-conversation, ui-layout, ui-sidebar.
4. **Sidebar seam (the one core touch).** In `ui-sidebar`: declare
   `sidebar.section` (`kind: 'list', scope: 'root'`) in the SlotMap +
   `contract/slots.ts`, and `renderSlot('sidebar.section', {collapsed, wide})`
   in `SidebarRoot.tsx` below `sidebar.workspaces`. Ship an HMR-disposal test.
   **Fallback if this change is contested by the concurrent main workflow:** skip
   the seam and register the Ops panels as an additional `conversation.view` tab
   `运行` — zero core change, the rail becomes a center tab. The cockpit ships
   either way.
5. **Components** in `ui-ph-ops`:
   - `MissionGraph.tsx` — React Flow + dagre layout + custom `StatusNode`
     (color map §4). Two lanes: mission→task→stage tree; capability-wiring row.
     Controlled `selection` → calls an injected `onSelect(node)`.
   - `MissionMiniMap.tsx` — compact rail version (or hand-rolled SVG per §5.1
     fallback).
   - `ProgressCard.tsx`, `VitalsCard.tsx` (promote/enrich `StatusBar` content),
     `EvolutionTicker.tsx` (reuse 演进 bar CSS).
   - `TimelineStrip.tsx` — chain events (provide/resolve/mount/plan_complete/
     task_error/boot) as ticks along time; hand-rolled flex, no lib.
   - `CockpitView.tsx` — center tab composing MissionGraph + TimelineStrip;
     `inject` provides `fetchSession`, `fetchSessionProgress`, `fetchSessions`.
   - Rail `index.ts` registers the four cards into `sidebar.section` and the
     cockpit into `conversation.view` (order after 战报).
6. **Theme React Flow.** It ships its own stylesheet and renders its own DOM, so
   it will NOT inherit `currentColor`. Override its CSS custom properties
   (`--xy-node-*`, `--xy-edge-*`, background, controls) to the `color-mix(…,
   currentColor, …)` scheme; set `colorMode="system"`. **Verify both light and
   dark** — this is React Flow's main integration cost. Node status colors use the
   established green/red/amber literals so it matches the other panels exactly.
7. **Bundle wiring.** Add to `packages/bundle/web-app/package.json` deps and a
   `cordis.patch.yml` entry (`- id: ui-ph-ops / name: …ui-ph-ops`, after
   `ui-ph-panels`); add tsconfig `host`/`client` references.
8. **Preview on :3180** from the worktree (do NOT restart the :3080 console the
   other workflow owns): build your branch and serve with
   `dsh --profile web --port 3180` (`--port` belongs to the web app per
   `apps/cli/README`); export `PH_BOARD_*` env so the board bridge loads; kill
   your own exact PID when done.
9. **Tests / gates** (per fork policy): HMR-disposal test for every registry
   contribution; package `./invariant`; locales; a keyless snapshot if the
   surface is model/user-visible. Keep all statistics in Python — TS stays
   render-only (the charter's audited hard rule).

### Definition of done (prototype)
Rail visible with the four cards on `ui-redesign`; cockpit tab renders the real
`session-main` mission graph with status colors; clicking a node opens its
evidence in `details`; MODE/heartbeat live in vitals; light+dark both correct;
`board/` gains only render-only reads with the dual-face equivalence test green.

---

## 8. Open questions for the user / next phase
- **In-flight liveness:** does the resident runtime (GOAL M4) log interim stage
  events, or only sealed `task.plan_complete`? Decides whether the pipeline card
  animates live or shows last-sealed + idle. (§6 #2 degrades honestly either way.)
- **Sidebar seam vs center tab:** flagship left rail (one `ui-sidebar` core touch)
  or the zero-core center-tab fallback? Recommend the rail — it's literally the
  "richer sidebar" asked for — but the concurrent main workflow may prefer we not
  touch `ui-sidebar` yet.
- **Cross-session mission:** if a mission spans multiple chained sessions, add a
  `session_chain()` aggregate later; single-session `session()` covers today.
