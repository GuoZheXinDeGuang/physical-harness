# ph-station design — the physical-harness console (fork of deepseek-harness)

Status: design, 2026-08-24. Supersedes the *overlay* half of
`docs/dsh-cockpit-design.md` (npx + `rebrand.sh` string-patching). It does **not**
re-open the seam ruling: the MCP inbox seam, the killed board HTTP shell, and the
two-mode rule all stand. This doc specifies the **fork** (`ph-station`), which now
holds the console *code*; design capital lives here in the motherboard repo.

Audience: an implementer with zero context. Every file path is absolute. Fork =
`/home/yusenzlabpc/Desktop/ph-station` (remote `yusenthebot/ph-station`, built,
tree clean, HEAD `b150a551b8`). Motherboard = `/home/yusenzlabpc/Desktop/physical-harness`.

---

## 0. Why a fork now (charter reconciliation — read this first)

`GOAL.md` v4 W3 said "界面=dsh, 不做任何新界面 … dsh 代码一行不改, 除非发布版有坑
挡路才轻 fork 打最小补丁 (不改名、不换身份)". Round 95 honored that literally: run
stock dsh via `npx`, patch 7 brand strings on every launch (`profiles/dsh/rebrand.sh`),
seed one workspace (`profiles/dsh/seed_workspace.py`), mount our MCP server through
`~/.dsh/cordis.patch.yml`. That overlay hit its ceiling:

- **Fragile brand.** 7 string-swaps against *built* bundles, re-applied every launch,
  emit `[DRIFT]` and silently regress the instant upstream moves a string
  (`rebrand.sh:133`). The whale `FishLogo` fallback renders on any miss.
- **Two identities on one machine.** `apps/cli/package.json` is `@deepseek-ai/dsh`
  v0.1.1-rc.2 (**not** `private`); the box also runs the *published* `@deepseek-ai/dsh`
  via npx. Same name, two trees.

This task commissions the fork as the deliberate next rung. It reconciles with W3,
it does not contradict it:

- **"不换身份" = do not erase upstream authorship.** Honored as the **legal floor**:
  `/home/yusenzlabpc/Desktop/ph-station/LICENSE` (MIT, "Copyright (c) 2026 DeepSeek")
  and `THIRD_PARTY_NOTICES.md` stay verbatim; the help/about attribution prose stays.
- **"own package identity" = stop colliding with published dsh.** We rename the
  *fork's* publishable entry and mark it private so it can never be confused with
  or published over `@deepseek-ai/dsh` on npm. Changing our name is how we *stop*
  masquerading, which is what W3 actually cared about.
- **v4.2 still governs: dsh is INTERFACE LAYER ONLY.** No business logic, no
  statistics, no gate decisions in TypeScript. The fork adds *presentation*
  (brand, panels) and *transport* (a data bridge). Every number a panel shows is
  computed in `board/store.py` (Python) and rendered verbatim. This is the
  "GUI 同源" rule from `docs/motherboard-design.md`: MCP tool, CLI, and GUI panel
  are three call-faces of the *same* `board.store` functions; logic lives in one
  place.
- **Two-mode rule is untouched.** The console is an operator surface. Mode
  (execution vs evolution) is enforced server-side by `scripts/harness_runtime.py`
  on brief claim (`_BRIEF_KEYS` + the R4 MODE gate). Nothing in the fork can grant
  a mode or bypass that guard.

---

## 1. Identity — native rebrand in source

Kill `profiles/dsh/rebrand.sh` by nativizing its 7 surfaces into fork source. Each
row below is a rebrand.sh surface → the **source** location to edit (source paths
differ from rebrand.sh's built-artifact targets because rebrand.sh patches the npx
`dist/`; the fork edits source and rebuilds).

| # | Surface | Source location to edit | Native value |
|---|---------|--------------------------|--------------|
| 1 | Static tab `<title>` | `apps/web/index.html:8` | `physical-harness 控制台` |
| 1/3 | Build-inlined title | `scripts/client-build-environment.ts` — add a `physical-harness` client build profile (mirror the `official` one) setting `DSH_CLIENT_TITLE='physical-harness 控制台'`; build the fork under it | drives #1 rewrite + #3 default |
| 3 | Runtime `document.title` default | `packages/client/ui-renderer/src/client/DocumentTitle.tsx:3` `DEFAULT_CLIENT_TITLE` | `physical-harness 控制台` |
| 2 | PWA manifest | `apps/web/public/manifest.webmanifest` (static, copied verbatim to dist) | name `physical-harness 控制台`, short_name `PH` |
| 7 | Favicon | `apps/web/public/favicon.svg` (static) | `PH` monogram (theme-aware; reuse the exact SVG in `rebrand.sh:219-225`) |
| 5 | Boot splash wordmark | `packages/client/web/src/boot-page.ts:37` `'HARNESS'` | `physical-harness` |
| 4 | Hero headline + composer placeholder + preview badge | `packages/client/ui-conversation/src/client/locales.ts` — `hero.headline` (zh L74 / en L251), `placeholder.hero` (zh L21 / en L198), `hero.preview` (L75/L252) | zh `物理测试台` / en `physical-harness`; placeholder zh `对物理测试台下达任务…` / en `Command the physical-harness…` (verbatim from `rebrand.sh:187-190`) |
| 6 | Sidebar + hero brand marks | `packages/client/ui-brand-official/src/client/index.ts:15` + `Brand.tsx` | remove the `DSH_CLIENT_BUILD_PROFILE !== 'official'` gate so it is **always on**; rewrite `OfficialBrandMark`/`OfficialBrandName` to render text `PH` / `physical-harness` (the `FishLogo`/`BrandWordmark` whale art is then never mounted, so the sidebar/hero fallbacks in `ui-sidebar/src/client/SidebarRoot.tsx:140,144-151,168` and `ui-conversation/.../EmptyHero.tsx:124` stay dormant) |

Surface 6 note: keeping the package named `ui-brand-official` while it renders
physical-harness is a naming smell but a package rename costs a roster row + every
importer; not worth it. Leave the package name, change what it renders. It is
already in the roster (`packages/bundle/web-app/cordis.patch.yml:213`), so no
roster edit is needed once the gate is dropped.

**Theme colors** are out of scope for v1 (dsh's neutral palette is fine); if wanted,
edit `--dsw-alias-*` in `packages/client/ui-theme/src/styles/design-platform.css`.

### Package identity

- Rename `/home/yusenzlabpc/Desktop/ph-station/apps/cli/package.json` `name`
  `@deepseek-ai/dsh` → **`@physical-harness/console`** and add `"private": true`.
  The cockpit invokes the fork **by path** (`node .../apps/cli/lib/bin.js`), never
  by npm name, so the rename breaks nothing and guarantees no npm collision /
  accidental publish over the real dsh.
- Do **not** rescope the ~50 internal `@deepseek-ai/dsh-*` workspace packages.
  They are `workspace:^`-linked, never published from the fork, and never touch
  npm; rescoping is pure churn and would fight every future upstream sync. Their
  `@deepseek-ai/dsh-*` names double as upstream attribution.
- Keep `LICENSE` + `THIRD_PARTY_NOTICES.md` byte-for-byte.

---

## 2. Information architecture

Primary surface stays the **chat 任务台** (dsh's `conversation` view, unchanged):
the operator types a task, the LLM composes `submit_brief`, the resident runtime
claims it. That is done and free.

Around it, four read panels + a status bar. Each is a top-level tab registered
into the `conversation.view` list slot — the canonical pattern is
`packages/client/ui-trajectory/src/client/index.ts:43` (copy that package
verbatim as the scaffold; the status bar instead uses the `shell.overlay` list
slot declared in `packages/client/ui-layout/src/client/index.ts:83`).

| Panel | What it shows | Data (all from `board/store.py`) | Release |
|-------|---------------|----------------------------------|---------|
| **战报** Stores | paired gate, McNemar numbers, held-out badge, per-generation Δpp | `list_stores`, `store_detail`, `heldout_blocks` | **v1 (first slice)** |
| **演进** RSI monitor | rounds timeline, Δpp per generation, promotion events | `parse_rounds`, `store_detail.generations` (`promoted`/`dev_delta`/`blind_delta`) | later |
| **机箱** Cards | card manifests + doctor status | **blocked**: needs `plugins/*/manifest.toml` (GOAL v4.1 R5) + `scripts/plugin_doctor.py` (R6) — neither exists yet | later (after R5/R6) |
| **账本** Ledger | seed blocks burned / reserved / planned | `parse_ledger` | later |
| **status bar** | MODE, runtime heartbeat, render window, model serving | MODE ← R4 session `runtime.boot` row (not built yet); heartbeat ← session `mtime`; model ← dsh `settings`/`llm` | later (minimal version v1.5) |

Rationale for the cut: 战报 alone exercises the full stack (native brand + fork
serve + new data plane + one real panel) with data that **exists today**. 演进 and
账本 reuse the exact same bridge with functions that also exist — cheap follow-ons.
机箱 and the MODE field of the status bar depend on motherboard rungs (R4/R5/R6)
that are not landed, so they are honestly deferred, not hand-waved.

---

## 3. Data plane — THE decision

**Requirement.** Panels must read through the **same functions** as the MCP tools
(`board/store.py`), same-origin, with **no LLM turn** and **no business logic in
TypeScript** (render only).

### Options considered

**(a) A thin Python JSON-HTTP service in `board/` that panels `fetch`.**
Rejected. This is exactly `scripts/rsi_board.py` — the HTTP shell round 95
*deleted* (`docs/dsh-cockpit-design.md` rung 4). It reintroduces a second
long-running server on a second port, cross-origin to the dsh app at
`127.0.0.1:3080` (CORS + its own trust fence to re-implement), and a second thing
for the cockpit to supervise. Reversing a landed deletion for no gain.

**(b) Proxy through dsh's existing MCP client connection.**
The `mcp-physical-harness` row already connects the dsh host to
`board/mcp_server.py`. But the *only* handle that connection exposes is
`ctx.tools` registration, and that handle is **agent-execution-shaped**: each tool
is a `ToolDefinition` whose `execute(args, exec)` closes over the MCP client and
requires an `exec: ToolExecution` (needs `exec.signal`; image paths need
`exec.agent`) — see `/home/yusenzlabpc/Desktop/ph-station/packages/mcp/mcp-client/src/tools.ts:303-360`.
Driving that from a panel means synthesizing a fake `ToolExecution` and routing
reads through the agent tool-call machinery (approvals, guards, session logging),
coupling every human-cadence poll to the churn-prone internal executor signature.
Rejected as the *mechanism*, though the **shape** — dsh-host-side, same-origin — is
right and is what (c) keeps.

**(c) [CHOSEN] A fork host bridge over the harness's CLI call-face.**
Logic stays in `board/store.py`. Expose it to panels through the charter's own
model — `docs/motherboard-design.md`: "MCP 与 CLI 是同一函数的两个调用面". The MCP
face (`board/mcp_server.py`) serves the LLM/chat; add the **CLI face** for the
panel transport, and bridge it to the browser same-origin through the dsh gateway.

Three thin layers, each a pure passthrough:

1. **Harness CLI face — `board/storecli.py` (new, motherboard repo).**
   `python -m board.storecli <fn> [name]` where `fn ∈ {list_stores, store,
   heldout, sessions, session, ledger, rounds}`; name-addressed fns
   (`store`/`heldout`/`session`) take a store/session `name` run through
   `board.store.safe_child` (the one audited traversal guard,
   `board/store.py:31`). It calls the identical `board.store` function
   `board/mcp_server.py` calls, `json.dumps` the result to stdout, exit 0;
   unknown fn/name → `{"error": …}` + nonzero. ~20 lines. **Byte-equivalence test**
   (the round-95 discipline): assert `storecli(fn) stdout == json.dumps(board.store.<fn>(...))`
   for every fn, and that a `../` name is rejected — proving all three faces are
   the same function.

2. **Fork host bridge — a `TypertRemoteService` `board` (new, fork).**
   The clean dynamic-endpoint seam
   (`/home/yusenzlabpc/Desktop/ph-station/packages/feedback/message-feedback/src/index.ts`
   is the copy-template): `class BoardBridge extends TypertRemoteService`,
   `super(ctx, 'board')`, one `@Remote('<fn>')` method per fn. Each method
   `execFile`s `<pythonPath> -m board.storecli <fn> [name]` with `cwd=<repoRoot>`
   and returns `JSON.parse(stdout)` **verbatim**. The gateway
   (`packages/api/gateway`) then auto-serves `POST /api/board/<fn>` behind the
   `trusted-host` fence — no `handler.ts` edit, no manual trust fence. Config
   (dsh rule: no hardcoded tunables) = `{pythonPath, repoRoot, runsDir}`.
   **Zero statistics, zero interpretation** — it spawns a process and forwards
   bytes. `execFile` (not shell) + the fixed fn allowlist (method names, not user
   input) + `safe_child` on `name` = no injection surface.

3. **Panels — fork client plugins (new, fork).** Copy `ui-trajectory`; the view
   component calls `/api/board/<fn>` via the runtime RPC client and **renders
   only**. Poll cadence follows `store_mtime` (human speed).

```
browser panel ──/api/board/stores──▶ dsh gateway ──▶ BoardBridge.@Remote('stores')
  ──execFile──▶ .venv/bin/python -m board.storecli list_stores ──▶ board.store.list_stores(runs)
  ◀── JSON ── (same dict the LLM gets from mcp__physical-harness__list_stores) ── rendered verbatim
```

Why (c) beats (b): the bridge TS is `execFile` + `JSON.parse` — no MCP protocol,
no long-lived connection lifecycle, no coupling to dsh's internal `ToolExecution`.
It survives dsh churn. Why it beats (a): one server (`:3080`), same-origin, existing
fence; `rsi_board.py` stays dead; the harness gains a CLI face the charter already
blesses, not a resurrected HTTP shell.

**Ceiling (marked):** one Python subprocess per panel request; cold-imports
`board.store → harness.events.SessionLog`. Fine at human-cadence polling on tiny
stores. If measured slow (import dominates), promote the bridge to a persistent
read worker or the dedicated-MCP-client variant of (b) — the panel and CLI faces
do not change, only the bridge's transport. `# ponytail: subprocess-per-request,
persistent worker if poll latency matters`.

### Endpoint spec

| HTTP (same-origin, `trusted-host`) | `board.store` fn | args | v1? |
|-----|------------------|------|-----|
| `POST /api/board/stores` | `list_stores(runs)` | — | ✅ |
| `POST /api/board/store` | `store_detail(safe_child(runs,name))` | `{name}` | ✅ |
| `POST /api/board/heldout` | `heldout_blocks(runs,name)` | `{name}` | ✅ |
| `POST /api/board/sessions` | `discover_sessions(runs)` | — | later |
| `POST /api/board/session` | `read_session(safe_child(runs,name))` | `{name}` | later |
| `POST /api/board/ledger` | `parse_ledger(STATUS.md)` | — | later |
| `POST /api/board/rounds` | `parse_rounds(progress.md)` | — | later |

Writes (submitting a brief from a panel) are **not** added — the chat already
composes `submit_brief` through the MCP seam into the same runtime-validated inbox.
A panel submit button is roadmap and would call the existing MCP tool, not a new path.

### Where each piece is mounted

- `board/storecli.py` + its test → motherboard repo (`board/`).
- BoardBridge **code + mount row** → fork
  (`packages/host/dsh-ph-board/` + a `board-bridge` `- insert:` row in
  `packages/bundle/web-app/cordis.patch.yml`, next to `message-feedback`).
  **Rollback fix (adversarial check):** the row does NOT go in the shared deploy
  overlay `~/.dsh/cordis.patch.yml`. That overlay is also layered by
  `scripts/cockpit --npx`, which serves the *published* dsh whose tree cannot
  resolve this fork-only host package — a `- insert:` row there would hard-fail
  the strict boot and break the rollback §6 promises. Keeping the row in the
  fork bundle (which `--npx` never loads) makes rollback safe by construction;
  the overlay stays MCP-row-only. Box-specific `pythonPath`/`repoRoot`/`runsDir`
  are the **fork-only channel**: `scripts/cockpit` exports them as `PH_BOARD_*`
  env vars that the row reads via `!!js`, and the row is
  `disabled: !!js process.env.PH_BOARD_REPO === undefined` so a plain `dsh web`
  still boots (a leading `!` in a `!!js` scalar reads as a second YAML tag).
- 战报 panel **code + roster row** → fork (`packages/client/ui-ph-battle/` +
  a `dsh.client` row in `packages/bundle/web-app/cordis.patch.yml`); no
  box-specific config, so bake it in.

---

## 4. Coding-agent chrome — keep / hide / remove

Default **HIDE (drop the roster row) or leave dormant** over delete; deleting
shared component code (e.g. editing `ui-tool/src/client/apply.ts`) risks breaking
the tool-render dispatch every tool depends on. Dropping a `dsh.client` roster row
in `packages/bundle/web-app/cordis.patch.yml` is non-breaking and reversible.

| Surface | What it is | Ruling | Why |
|---------|-----------|--------|-----|
| Per-tool renderers `bash`/`read`/`file-mutation`(diff)/`search`/`web`/`todo` (`ui-tool` built-ins) | keyed `tool.call.toolview` rows | **KEEP dormant** | keyed by wire tool name; if the harness agent never calls that tool, the renderer never mounts. Zero visible clutter. Removing them means editing shared `ui-tool` code — risky, no gain. |
| `mcp__physical-harness__*` tool cards | MCP tools in chat | **KEEP (generic)** | no keyed renderer → `GenericToolCard` fallback. Fine for v1; a custom card is roadmap. |
| `ui-trajectory` tab | agent step/trajectory view | **KEEP** | generic agent view, not coding-specific; harmless beside chat. Revisit if it competes with 演进. |
| `ui-cordis` (`:224`) | "agent modifies its own runtime" UI | **HIDE** (drop roster row) | self-modification chrome irrelevant to an operator console. |
| `ui-skill` (`:248`), `ui-reference` (`:255`) | coding skill/reference browser | **HIDE** (drop rows) | our "skills" are SkillRecords in `runs/`, surfaced by 战报/机箱, not dsh's code-skill catalog. |
| `ui-subagent` (`:251`), `ui-plan` (`:288`), `ui-jobs` (`:259`) | subagent delegation, plan mode, background jobs | **KEEP dormant for v1** | mount but stay empty unless those tools fire; drop rows later if they add visible entries. Cheaper to leave than to prune blind. |
| `ui-deliverables` (`:234`), `ui-workflow-run` (`:229`) | produced-file / workflow UI | **KEEP** | `report.py` HTML export is a produced-file deliverable dsh can surface (round-95 ruling) — deliverables UI is useful. |
| `ui-goal` (`:263`), `ui-message-feedback` (`:268`), settings panes | goals, thumbs, settings | **KEEP** | generic, useful, non-coding. |
| Diff viewer `DiffBlock` (`ui-primitives`) | code-diff render | **KEEP dormant** | only mounts for a `file-mutation` tool call; our agent does not emit those. |

No shared code is deleted. All "HIDE" actions are roster-row removals in one file.

---

## 5. First slice (the Implement phase — smallest end-to-end proof)

Proves the whole idea: native brand + fork-served cockpit + ONE real panel (战报)
on the chosen data plane. Ordered; each step commits + pushes.

1. **Native brand in fork source** — all 7 surfaces from §1 (edit the source
   locations, ungate `ui-brand-official`, add the `physical-harness` client build
   profile), rename `apps/cli` package to `@physical-harness/console` +
   `private:true`. `pnpm install && pnpm run build`. **Check:** `curl localhost:3080/`
   → `<title>physical-harness 控制台</title>`; served
   `/plugins/@deepseek-ai/dsh-client-ui-conversation/client.js` carries the patched
   hero/placeholder; no `HARNESS`/`DSH Local Build`/`FishLogo` leaks; sidebar shows
   `PH` + `physical-harness`.
2. **Harness CLI face** — `board/storecli.py` + byte-equivalence test
   (`storecli == board.store == mcp tool`, `../` rejected). Motherboard repo.
3. **Fork host bridge** — `packages/host/dsh-ph-board/` `TypertRemoteService board`
   with `@Remote('stores'|'store'|'heldout')` execFile→storecli, returning
   `JsonValue` (the Typert generator rejects an unconstrained `unknown` at a
   Remote boundary). Register the generated `board` remote-client in
   `packages/api/remotes/src/client/index.ts` so `ctx.remote.board` resolves. Add
   the `board-bridge` row to the **fork bundle** patch (not `~/.dsh/`; see §3
   "Where each piece is mounted") gated on `PH_BOARD_*` env.
4. **战报 panel** — `packages/client/ui-ph-battle/` copied from `ui-trajectory`,
   registers view id `battle` into `conversation.view`, fetches `/api/board/stores`
   (list) → `/api/board/store` + `/api/board/heldout` (drill-down), renders paired
   gate / McNemar / held-out badge / per-gen Δpp. Roster row in the web-app bundle.
   `pnpm run build`.
5. **Cockpit switchover** (§6) — fork build becomes default, `--npx` fallback.
6. **GUI acceptance in the browser** (GOAL v4.1 GUI 同源 补遗 rule 1, not curl):
   open `scripts/cockpit`, confirm brand, open 战报, see a real store with real
   McNemar numbers, and confirm chat still submits a brief via
   `mcp__physical-harness__submit_brief`. Record a GIF (fork's own
   `record-browser-gif` skill applies to GUI changes).

Everything else → §7 roadmap.

---

## 6. Migration — npx+overlay → fork build

> **STATUS 2026-08-24 — MIGRATION COMPLETE, OVERLAY RETIRED.** The parity
> checklist below passed against the running fork build (all 7 green), so the
> retirement was executed: `profiles/dsh/rebrand.sh` deleted, and the `--npx`
> branch + `DSH_PKG` pin removed from `scripts/cockpit`. The fork build is now
> the **sole** path (no `--npx` fallback). `profiles/dsh/seed_workspace.py` and
> the `~/.dsh/cordis.patch.yml` deploy overlay (MCP row only) are **kept**. The
> bullets and `--npx` mechanics below are retained as the historical record of
> how the switchover was designed and verified.

Edit `/home/yusenzlabpc/Desktop/physical-harness/scripts/cockpit`.

**New default = fork build; `--npx` = fallback.**

- Add `PH_STATION="${PH_STATION:-$HOME/Desktop/ph-station}"` and a `--npx` flag.
- Keep the `nvm use 22` + `export DSH_HOME="${DSH_HOME:-$HOME/.dsh}"` block
  unchanged — this is what makes the fork inherit the MCP row + workspace seed
  from `$DSH_HOME`. (The BoardBridge row rides the fork *bundle*, not `$DSH_HOME`;
  the fork path additionally exports `PH_BOARD_*` so that row activates.)
- **Fork path (default):** guard the built artifacts
  (`[ -f "$PH_STATION/apps/web/dist/index.html" ] && [ -f "$PH_STATION/apps/cli/lib/bin.js" ]`);
  if missing, error `run 'pnpm run build' in ph-station` (do **not** serve an
  unbuilt tree — `web-app/src/index.ts resolveDistIndex` throws). Run
  `seed_workspace.py` (still needed — it seeds `$DSH_HOME` *user data*, orthogonal
  to brand; native brand does not seed a workspace). Do **not** run `rebrand.sh` —
  brand is native now. Then `exec node "$PH_STATION/apps/cli/lib/bin.js" web --no-open --port "$PORT"`.
- **`--npx` path (fallback, unchanged):** run `rebrand.sh` (+ its seed tail) then
  `exec npx --yes @deepseek-ai/dsh@0.1.1-rc.2 web --no-open --port "$PORT"`.

**Rollback (historical):** while the overlay lived, `scripts/cockpit --npx`
restored the exact round-95 behavior, and the `0.1.1-rc.2` pin + `rebrand.sh`
stayed in-tree until the parity checklist passed. That gate is now met, so the
`--npx` path, the pin, and `rebrand.sh` have been removed (see the STATUS banner
above and the Retire plan below). Reverting is a `git revert` of the retirement
commit, not a runtime flag.

**Single entry — runtime adopt-or-spawn (round 98, ✅ landed).** The operator is
UI-only, so `scripts/cockpit` now starts *everything*: before serving the console
it brings up the resident `harness_runtime` on `runs/session-main`. Adopt-or-spawn,
because `harness_runtime.py` has no concurrent-session guard of its own (write-once
`MODE`; atomic inbox-claim rename — a double-run can't corrupt but is still wrong):
cockpit scans `ps` for a live runtime on that session dir, **adopts** it if found
(prints its PID, does not restart, does not record it for `--stop`), else **spawns**
one (`nohup`, log → `runs/session-main/runtime.log`) and records its PID. `--render`
is passed IFF `$DISPLAY` is set (the runtime hard-refuses `--render` headless);
headless spawns get `MUJOCO_GL=egl`. `--stop` kills the web server and any
*spawned* runtime by the **exact PIDs** in `runs/session-main/cockpit.pids` (never
pattern-kill — that would match operator shells); an adopted runtime is left alone.
Opt-outs: `--no-runtime` (console only), `--no-render` (force headless). This
supersedes the two-terminal round-97 recipe in the README.

**Shared `$DSH_HOME` caution:** `healProfilesModuleFallback` re-points
`~/.dsh/profiles/node_modules` at whichever install ran last — never run the npx
and fork paths concurrently on the same `DSH_HOME`; expect symlink churn when
alternating. Both are version `0.1.1-rc.2` so `profiles/web/package.json`
normalizes cleanly.

### parity_checklist (all must pass before `rebrand.sh` + the npx pin retire) — ✅ ALL PASSED 2026-08-24 (fork build, running :3080)

1. `curl localhost:3080/` on the **fork** build → `<title>physical-harness 控制台</title>`; PWA manifest name/short = `physical-harness 控制台`/`PH`; favicon is the PH monogram.
2. Served client bundles carry the native hero headline + placeholder (`对物理测试台下达任务…`); no `探索未至之境` / `Into the Unknown` / `DSH Local Build` / `HARNESS` / `FishLogo` whale anywhere in the running UI (sidebar mark, hero mark, boot splash).
3. Workspace pre-selected on cold load: composer live, no "选择一个工作区开始" (seed ran; `workspace.json` holds exactly one `physical-harness` record after restart).
4. MCP row loads on the fork build: `mcp__physical-harness__submit_brief` and the read tools appear in chat; a brief submitted from chat lands in the runtime inbox and the runtime claims it (mode + `_BRIEF_KEYS` guard still fires).
5. BoardBridge live: `/api/board/stores` returns the same dict as
   `board.store.list_stores(runs)` (byte-equivalence test green); the 战报 panel
   renders a real store's paired gate + McNemar + held-out badge.
6. URL/bind parity + rollback: fork serves `127.0.0.1:3080` same as npx. `--npx`
   **boots green WITH the deployed `~/.dsh/cordis.patch.yml` overlay present** —
   proving the BoardBridge row is NOT in that overlay (it rides the fork bundle),
   so the published dsh never meets a host package its tree cannot resolve.
7. Fork build is green (`pnpm run build`) and the box can rebuild from clean.

### Retire plan — ✅ EXECUTED 2026-08-24

Done: deleted `profiles/dsh/rebrand.sh`; removed the `DSH_PKG=@deepseek-ai/dsh@0.1.1-rc.2`
pin and the `--npx` branch from `scripts/cockpit`. Kept `profiles/dsh/seed_workspace.py`
(user-data seed — the fork cockpit path already calls it directly, so it was never
coupled to the deleted overlay) and `~/.dsh/cordis.patch.yml` (MCP row only — the
BoardBridge row lives in the fork bundle, see §3).

---

## 7. Roadmap (deferred, in dependency order)

- **演进 panel** — reuse BoardBridge `rounds` + `store` endpoints; render rounds
  timeline + per-gen Δpp + promotion events from `store_detail.generations`.
- **账本 panel** — reuse BoardBridge `ledger`; render burned/reserved/planned seed
  blocks from `parse_ledger`.
- **status bar (minimal, v1.5)** — `shell.overlay` slot: runtime heartbeat
  (session `mtime`), render window, model serving (dsh `settings`/`llm`). Add the
  **MODE** field once motherboard R4 seals the `runtime.boot` row.
- **机箱 panel** — blocked on motherboard R5 (`plugins/*/manifest.toml`) + R6
  (`scripts/plugin_doctor.py`); then a BoardBridge `manifests`/`doctor` endpoint
  over those, rendered as card status.
- **Custom MCP tool cards** — a `tool.call.toolview` keyed renderer for
  `mcp__physical-harness__*` (key MUST equal `publicToolName(serverName, rawName)`
  from `packages/mcp/mcp-client/src/tools.ts:111`, not a hand-written string).
- **report.py as a deliverable** — surface `python -m board.report` HTML through
  `ui-deliverables` (round-95 "promote report.py to primary deliverable").
- **Panel submit** — a 战报/演进 button that calls the existing `submit_brief` MCP
  tool (no new write path).
- **Theme colors** — `--dsw-alias-*` in `design-platform.css` if a lab palette is wanted.
- **Nativize workspace auto-select** — optionally teach the fork to auto-create the
  repo workspace when the registry is empty, retiring `seed_workspace.py`.

---

## 8. Risks

1. **Two `$DSH_HOME` producers.** BoardBridge and the agent's `mcp-client` both hit
   `board/store.py` (via storecli / via the MCP server) but only ever **read**
   `runs/`. No write contention. The only writer is the resident runtime.
2. **Subprocess import cost** — see the §3 ceiling; measure before optimizing.
3. **Upstream sync churn.** The fork now carries brand edits + a host package + a
   client package + a build-profile + a roster row. Keep them small and localized
   (the brand edits are the only ones touching upstream files; BoardBridge and the
   panel are new packages that upstream never touches). Re-apply on sync per
   `vendor`/fork policy.
4. **`rejectStandaloneServe`** — never "just serve the dist"; only `dsh web`
   injects `window.__DSH_BOOT__`. The cockpit must serve through the built bin.
5. **No auth layer.** dsh's `trusted-host` fence is anti-DNS-rebinding, not
   authentication; `/api/board/*` inherits it. Keep the `127.0.0.1` bind
   (`--host 0.0.0.0` is rejected by `startup.ts` by design). BoardBridge is
   read-only, so even a fence bypass exposes only sealed `runs/` reads.
6. **`GenericToolCard` for MCP tools** shows `mcp__physical-harness__…` as a plain
   card in v1 — acceptable; custom card is roadmap.

---

## 9. Backbone LLM — local Qwen3.8-27B (2026-08-28)

The console's agent runs on the **local** sglang server, not the DeepSeek API.
One model serves chat, planning, and vision.

### Server — `~/models/launch_qwen38.sh` (outside any repo; back-up `.bak-agent`)

```
--served-model-name qwen3.8-27b        stable id; the real one is a filesystem path
--tool-call-parser  qwen3_coder        ← without this, NO agent works
--reasoning-parser  qwen3              splits <think>…</think> into reasoning_content
--context-length 32768 --max-total-tokens 32768 --max-prefill-tokens 32768
--mem-fraction-static 0.92 --disable-piecewise-cuda-graph
```

**`qwen3_coder`, not `qwen`/`qwen25`.** The checkpoint's own
`chat_template.jinja:68` instructs the model to answer in the XML dialect
`<tool_call><function=NAME><parameter=K>v</parameter></function></tool_call>`,
not the hermes-style JSON the `qwen` parsers expect. With the wrong parser (or
none) sglang returns that XML as ordinary `content` with no `tool_calls` field,
and every agent silently calls nothing — it reads as a dumb model rather than a
misconfigured one. Verify with a `tools:` request and look for `finish_reason:
"tool_calls"`, never by reading the prose.

**Qwen3.5 is a hybrid mamba/linear-attention model, and that is the whole memory
story.** Its mamba state cache and the attention KV pool are drawn from the same
`--mem-fraction-static` budget, so raising context *starves* the state cache:
`--max-total-tokens 32768` at `0.86` dies at startup with `max_mamba_cache_size=4,
mamba_ratio=5, resulting max_num_reqs=0`. The fix is a bigger static fraction, and
what pays for it is `--disable-piecewise-cuda-graph` (the piecewise prefill graph
cost 1.15 GB for little benefit at these prompt lengths).

Measured on the 24 GB 4090D: weights 17.71 GB, attention KV ~64 KiB/token,
`max_total_num_tokens=26089`, ~1.0 GB free at idle. **sglang silently clamps
`--max-running-requests` from 2 to 1** — the state cache affords one concurrent
request — so concurrent calls queue rather than fail. MTP/NEXTN speculative
decoding stays off (unusable in the installed sglang), so the context increase
cost no decode speed; there was none to give up.

**26089 is the real ceiling, not the advertised 32768.** A request over the pool
fails even while under the advertised window, so the harness is told 24576.

### The backbone moved to llama.cpp (2026-08-28)

sglang cannot shrink past ~21 GB for structural reasons: `embed_tokens` has no
quantization path in it at all (`compressed_tensors.py` implements
`ParallelLMHead` and nothing for `VocabParallelEmbedding`), so 2.37 GiB of the
weights is a permanent floor, and every 4-bit checkpoint on the Hub leaves
`lm_head` in its `ignore` list as well. GGUF quantizes both, but sglang's
`GGUF_HF_NAME_MAP_BUILDERS` does not list `qwen3_5`, so serving a GGUF means
serving it from llama.cpp — which costs nothing at the console, because
`llama-server` is OpenAI-compatible and the route is a `baseURL`.

Serving `unsloth/Qwen3.8-27B-GGUF` `UD-Q4_K_XL` (17.56 GB) + `mmproj-BF16.gguf`
(931 MB) on the existing Vulkan build, `-ngl 999 -c 24576 --jinja`, measured:

| | sglang AWQ | llama.cpp Q4_K_XL |
|---|---|---|
| resident, after a vision request | 21.8 GB | **19.3 GB** |
| structured `tool_calls` | yes | yes (needs `--jinja`) |
| vision on a robosuite frame | accurate | accurate |
| generation | ~60 tok/s | 24 tok/s |
| prompt | CUDA-fast | 307 tok/s |
| smaller rungs | none | IQ4_XS 13.3 GB · Q3_K_XL 12.2 GB |

The first request reports ~7 tok/s prompt eval; that is Vulkan compiling graphs,
not the steady state — measure the second one. The generation gap is largely the
Vulkan build: a CUDA build should recover most of it, and is the next move if the
speed bites. Revert is `baseURL` back to `:30000` plus a sglang restart.

Two levers that looked promising and are not: `--mamba-ssm-dtype bfloat16` saves
0.36 GB and **breaks the model** — the same tool prompt went from 35 reasoning
tokens to 600 with no answer, an endless loop; and `--enable-memory-saver` does
work (`/release_memory_occupation` really does hand back 20 GB in 0.03 s, and
`/resume_memory_occupation` takes it straight back), but it needs
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments` unset, and with fp32 state and
24576 tokens the remaining budget then fails the mamba cache at startup.

### Sharing the card with the simulator (2026-08-28)

The model at `0.92` leaves ~0.6 GB, which is enough for **one** sim task (a
`stack` run completed with the server live, 23.9/24.5 GB) and nowhere near
enough for an RSI calibration, whose ten pool workers each want an EGL context.
Measured while hunting for a smaller footprint — every row a real boot attempt:

| `--mem-fraction-static` / context | result |
|---|---|
| 0.92 / 32768 | boots, 22.5 GB, ~0.6 GB free |
| 0.88 / 24576 + `--kv-cache-dtype fp8_e5m2` | boots, 21.2 GB, ~2.8 GB free ← current |
| 0.88 / 8192 | boots, 21.0 GB, but 8 K cannot hold the agent's own prompt (~22 K with the board tools) |
| 0.855 / 8192, cuda graphs off | **fails**: `max_mamba_cache_size=2, mamba_ratio=5 → max_num_reqs=0` |
| 0.81 / 16384 | **fails**: 0.22 GB left after weights; the state cache needs 146.81 MB *per request* |

**There is no configuration under ~21 GB.** The weights occupy ~19.7 GB resident
(17.71 GB of tensors plus activations and graphs) and the hybrid state cache
needs five 146.81 MB slots before it will serve one request, so the arithmetic
floor sits just above 20 GB no matter how the knobs are turned.

Two levers do not apply here. `--cpu-offload-gb` (host RAM for VRAM, and there
is 64 GB of it) **crashes this model at load**: `Expected all tensors to be on
the same device, but found at least two devices, cuda:0 and cpu` — AWQ weights
plus the hybrid layers do not survive the split. Speculative decoding
(`--speculative-*`, MTP/NEXTN, DFlash2) buys latency, not memory: a draft model
*adds* resident weights, which is why DFlash2 was abandoned here before.
`--kv-cache-dtype fp8_e5m2` is the one that pays: it halves the attention pool,
which is what bought 24576 tokens back at a lower fraction.

**So one 24 GB card cannot host this backbone and an RSI calibration at the same
time.** Either stop the server for a calibration run, or move the backbone to a
smaller multimodal checkpoint (8-14 B AWQ, ~8-10 GB) — the route is
configuration, so swapping the model is one edit in the patch below.

### Console — `profiles/dsh/cordis.patch.yml`

`llm-pi-ai` is mounted **dormant** by the base bundle (zero routes) exactly so a
deployment can declare its own, so the row is a bare `- id:` **override** — the
mirror image of the `insert:` rule at the top of that file, and wrong in the
other direction here. `local-qwen` is a hand-declared route (`api` + `baseURL` +
an explicit `models` list; pi-ai ships no catalog for it and nothing interrogates
the endpoint), declaring `input: [text, image]` and `compat.maxTokensField:
max_tokens` + `supportsDeveloperRole: false` (pi-ai cannot recognize a private
baseURL and otherwise addresses it as OpenAI itself).

**The placeholder credential is not optional.** sglang is unauthenticated on
loopback, but a hand-declared route has no catalog and `llm-pi-ai`'s
`provider.ts:132` therefore always declares apiKey auth for it — there is no
spelling for "keyless". Omitting `apiKeyEnv` fails every request up front with
`PI_AI_ERROR: No API key for provider: local-qwen`. The route names
`LOCAL_QWEN_API_KEY` and the value lives in `$DSH_HOME/.credentials.yaml`
(mode 0600), never in this repo. sglang ignores the header.

### Deploy — two files, and the second is the one that runs

`scripts/cockpit` does **not** deploy this patch. `$DSH_HOME/cordis.patch.yml` is
a manual copy, so editing the repo file alone changes nothing:

```
cp profiles/dsh/cordis.patch.yml ~/.dsh/cordis.patch.yml
```

`$DSH_HOME/settings.yaml` **wins over the entry config**, so its
`agent-default-model:` section was switched to `local-qwen`/`qwen3.8-27b` in the
same change — patching only the cordis row would have left every new session on
DeepSeek. Settings are hot-reloaded; the cordis row needs a console restart.

Verify against the **runtime**, not the files:

```
curl -s -X POST http://127.0.0.1:3080/api/llm.models -H 'Content-Type: application/json' \
  -d '{"type":"client-request","rpcId":"...","method":"llm.models","payload":{}}'
```

`local-qwen` must appear in `groups` with `failures: []`.

### Restarting the console without killing an experiment

`runs/session-main/cockpit.pids` can carry `runtime_adopted=0`, meaning a previous
cockpit **spawned** the resident runtime — `cockpit --stop` would then kill it.
To restart only the console, `kill` the exact `web_pid` and start cockpit again;
it re-adopts the live runtimes (`adopting resident runtime … not restarting`).
