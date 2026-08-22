# RSI Board — TensorBoard for the harness

Point it at a `runs/` directory; it serves a local web board that auto-discovers
every campaign store, live-updates as new artifacts land, and renders comparable
scalar timelines. Same model as TensorBoard: local HTTP server + browser, no
build step, read-only over the sealed evidence in `runs/`.

## Launch

```bash
python scripts/rsi_board.py --runs runs/ --port 6006
```

Opens `http://127.0.0.1:6006` and auto-opens a browser (`--no-browser` to skip).
`--status` / `--progress` override the STATUS.md / progress.md paths (default:
the files next to `runs/`). Ctrl-C to stop.

Pure stdlib server (`http.server`) + vanilla-JS frontend with hand-rolled SVG
charts — no dependencies, nothing vendored.

## Views

1. **Campaigns** — store list (auto-discovered, newest first) → per-campaign
   generation timeline: gen, promoted/rejected, rule summary, dev
   n/base/governed/Δ/fixed/broken/fires/p, judgement reason, plus scalar charts
   across generations (fixed vs broken, dev Δ, base-vs-governed rate, p-value).
   The campaign result shows the once-scored held-out block, the blind-twin
   judgement gate, and the transfer-ablation curve. Diagnostic stores
   (round25 3-arm, arm-time probe, round88 fix) render their own comparison
   cards.
2. **Held-out blocks** — a campaign's own scored block plus every sibling
   `<name>-rescore-*` block on one axis (fixed / Δ per block), with grasp-vs-place
   **stage-attribution** bars (per-stage success + first-failure counts) where a
   stage overlay was scored.
3. **Seed ledger** — burn map parsed from STATUS.md's block-budget section: a
   proportional number line + chips coloured burned / reserved / planned, each
   with its source line on hover.
4. **Rounds** — collapsible feed parsed from progress.md's `## Round N` headers,
   latest first.

LIVE: the frontend polls `/api/stores` every 4 s (server reads filesystem mtimes
each request) and re-renders when anything changes, so a campaign writing
artifacts appears without a reload. Reads are robust to partial/mid-write JSON:
an unparseable artifact or a half-written index line is skipped and counted, and
the next poll picks it up whole.

## Layout

- `board/store.py` — pure parse layer (read-only), unit-tested against fake
  stores in `tests/test_rsi_board.py`. Knows the CampaignStore shape: `index.jsonl`
  carries the artifact *kind*; payloads under `artifacts/<sha>.json` are
  content-addressed.
- `scripts/rsi_board.py` — thin stdlib HTTP server; every endpoint is one call
  into `board.store`. Endpoints: `/api/stores`, `/api/store?name=`,
  `/api/heldout?name=`, `/api/ledger`, `/api/rounds`.
- `board/index.html` — the single-page frontend (dark console look, borrowed
  from DeepSeek Harness's local-server GUI form).
