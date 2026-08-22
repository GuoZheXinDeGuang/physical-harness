# RSI Board — the harness's read-only evidence layer

Point it at a `runs/` directory to read every campaign store, the runtime
session chains, the seed ledger and the rounds feed — all read-only over the
sealed evidence in `runs/`. The hand-rolled HTTP server + single-page front end
retired at round 95; the live cockpit is now DeepSeek Harness (dsh) driving the
MCP server below, and the exported deliverable is a self-contained HTML report.

## Surfaces

- **HTML report** — `python -m board.report --out out.html` renders the whole
  `runs/` tree into one self-contained page: executive summary, per-campaign
  generation timelines + scalar charts, held-out multi-block comparison with
  grasp-vs-place stage attribution, a runtime-sessions section with a hash-chain
  badge (verified / broken / not verifiable), the seed-ledger burn map, and the
  rounds feed. Headless, for cron. `--status` / `--progress` override the
  STATUS.md / progress.md paths (default: the files next to `runs/`).
- **MCP server** — `board/mcp_server.py` is the stdio MCP server the dsh cockpit
  connects to: seven read-only tools (`list_stores`, `store`, `heldout`,
  `sessions`, `session`, `ledger`, `rounds`), each one call into `board.store`
  returning the same dicts, plus `submit_brief`, which drops a brief into the
  resident runtime's inbox (the runtime re-validates `_BRIEF_KEYS` server-side —
  the tool never becomes the authority).

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
