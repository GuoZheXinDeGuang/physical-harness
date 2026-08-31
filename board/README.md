# RSI Board — the harness's read-only evidence layer

Point it at a `runs/` directory to read every campaign store, the runtime
session chains, the seed ledger and the rounds feed — all read-only over the
sealed evidence in `runs/`. The live cockpit is ph-station driving the MCP
server below; the exported deliverable is a self-contained HTML report.

## Surfaces

- **HTML report** — `python -m board.report --out out.html` renders the whole
  `runs/` tree into one self-contained page: executive summary, per-campaign
  generation timelines + scalar charts, held-out multi-block comparison with
  grasp-vs-place stage attribution, a runtime-sessions section with a hash-chain
  badge (verified / broken / not verifiable), the seed-ledger burn map, and the
  rounds feed. Headless, for cron. `--status` / `--progress` override the
  STATUS.md / progress.md paths (default: the files next to `runs/`).
- **MCP server** — `board/mcp_server.py` is the stdio MCP server the ph-station
  cockpit connects to: read-only tools (`list_stores`, `store`, `heldout`,
  `sessions`, `session`, `session_progress`, `runtime_status`, `runtime_events`,
  `runtime_frame`, `runtime_rollout`, `runtime_keyframes`, `runtime_keyframe`, `host_vitals`,
  `ledger`, `rounds`), each one call into `board.store` returning the same dicts,
  plus `submit_brief`, which drops a brief into the
  resident runtime's inbox (the runtime re-validates `_BRIEF_KEYS` server-side —
  the tool never becomes the authority). Three brief kinds: `task`, `campaign`,
  and `rsi` — the generic self-improvement chain, minimal form
  `{"kind":"rsi","task":"<task>"}` (`docs/project-documentation.md` §4). The second write is
  `model_server(action)` — `status` / `start` / `stop` for the local llama.cpp
  server on 127.0.0.1:30001, so the browser-only operator can hand its VRAM back
  to the simulator without a terminal. It switches the SERVICE PROCESS only;
  which model a request routes to stays the console's route picker. The launcher
  it may run is a constant in `board/store.py` and the action is whitelisted
  there — a caller supplies a word, never a path or a command line.

## Layout

- `board/store.py` — pure parse layer (read-only), unit-tested against fake
  stores in `tests/test_store.py`. Knows the CampaignStore shape: `index.jsonl`
  carries the artifact *kind*; payloads under `artifacts/<sha>.json` are
  content-addressed. Owns the `safe_child` traversal guard both surfaces reuse.
- `board/report.py` — pure HTML/SVG report builder + the `--out` headless entry.
- `board/mcp_server.py` — the MCP passthrough over `board.store` + `submit_brief`.

Reads are robust to partial/mid-write JSON: an unparseable artifact or a
half-written index line is skipped and counted, so a campaign still writing
artifacts is read whole on the next call.
