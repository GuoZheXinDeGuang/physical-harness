"""Read-only parse layer over RSI campaign stores + STATUS/progress markdown.

Pure functions, no server, no writes -- runs/ is sealed evidence. The store
shape is the one `plugins.rsi.campaign.CampaignStore` writes: an
``index.jsonl`` of ``{seq, kind, sha, time}`` rows plus content-addressed
``artifacts/<sha>.json`` payloads. The *kind* lives in the index, never in the
payload, so the index is the discriminator (see CampaignStore.put).

Robust to partial/mid-write JSON: a campaign writing artifacts tonight can be
polled while a file is half-flushed. Unreadable index rows and unreadable
artifacts are skipped and counted, so a LIVE poll landing mid-write simply
retries on the next tick rather than crashing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness.events import SessionLog

# --- store discovery / robust reads -----------------------------------------


def is_store(store_dir: str | Path) -> bool:
    """A directory is a campaign store iff it carries an index.jsonl."""
    return (Path(store_dir) / "index.jsonl").exists()


def safe_child(runs_dir: str | Path, name: str, is_kind) -> Path | None:
    """Resolve ``name`` to a direct child of ``runs_dir`` that passes ``is_kind``,
    rejecting path traversal. One audited guard for every name-addressed read
    (stores and sessions), shared by the board HTTP shell and the MCP server so
    neither can be walked outside runs_dir with a ``../`` name."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    runs_dir = Path(runs_dir)
    path = (runs_dir / name).resolve()
    if path.parent != runs_dir.resolve() or not is_kind(path):
        return None
    return path


def _index_rows(store_dir: Path) -> list[dict]:
    """Index rows in seq order, skipping any line that is not valid JSON.

    A campaign appends to index.jsonl line-by-line; a poll can catch a
    half-written trailing line. Skip it -- the next poll gets the whole row.
    """
    rows: list[dict] = []
    index_path = store_dir / "index.jsonl"
    if not index_path.exists():
        return rows
    for line in index_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and "kind" in row and "sha" in row:
            rows.append(row)
    return rows


def read_store_artifacts(store_dir: str | Path) -> tuple[dict[str, list[dict]], int]:
    """Every readable artifact grouped by kind, in index order, plus a skip count.

    The skip count is artifacts referenced by the index whose JSON did not parse
    or whose file is missing -- i.e. mid-write. Callers surface it so the UI can
    show "1 artifact still being written" instead of pretending it is absent.
    """
    store_dir = Path(store_dir)
    by_kind: dict[str, list[dict]] = {}
    skipped = 0
    for row in _index_rows(store_dir):
        path = store_dir / "artifacts" / f"{row['sha']}.json"
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            skipped += 1
            continue
        by_kind.setdefault(row["kind"], []).append(payload)
    return by_kind, skipped


def store_mtime(store_dir: str | Path) -> float:
    """Newest mtime across index.jsonl and the artifacts dir -- the LIVE signal.

    A new generation both appends to the index and drops a new artifact file, so
    either mtime moving means "something changed"; the frontend polls this.
    """
    store_dir = Path(store_dir)
    times = [0.0]
    for p in (store_dir / "index.jsonl", store_dir / "artifacts"):
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(times)


# --- payload shaping --------------------------------------------------------


def _delta(paired: dict) -> float | None:
    """governed_rate - base_rate: the effect a paired gate measured. None if the
    dict is not a paired result (defensive against a mid-write partial)."""
    if not isinstance(paired, dict):
        return None
    g, b = paired.get("governed_rate"), paired.get("base_rate")
    if g is None or b is None:
        return None
    return g - b


def trigger_str(trig: dict) -> str:
    """A one-line human summary of a Trigger payload, e.g.
    ``observable.finger_gap value lt 0.001 @arm58``. Mirrors the fields the
    campaign's Trigger.describe() carries without importing the plugin."""
    if not isinstance(trig, dict):
        return str(trig)
    thr = trig.get("threshold")
    thr_s = f"{thr:.4g}" if isinstance(thr, (int, float)) else str(thr)
    return (f"{trig.get('feature')} {trig.get('reducer')} {trig.get('op')} "
            f"{thr_s} @arm{trig.get('arm_after')} dwell{trig.get('dwell')}")


def _rule_view(rule: dict) -> dict:
    """{trigger_str, recovery} from a rule payload ({trigger, recovery, rule_id})."""
    if not isinstance(rule, dict):
        return {"rule_id": None, "trigger_str": str(rule), "recovery": None}
    rec = rule.get("recovery") or {}
    return {"rule_id": rule.get("rule_id"),
            "trigger_str": trigger_str(rule.get("trigger", {})),
            "recovery": rec.get("name") or rec.get("strategy")}


def _ablation(entries: list) -> list[dict]:
    """[[sd, paired], ...] -> [{sd, governed_rate, base_rate, delta, privileged}]."""
    out = []
    for entry in entries or []:
        try:
            sd, paired = entry
        except (ValueError, TypeError):
            continue
        out.append({"sd": sd, "governed_rate": paired.get("governed_rate"),
                    "base_rate": paired.get("base_rate"), "delta": _delta(paired),
                    "declared_privilege": paired.get("declared_privilege")})
    return out


def store_detail(store_dir: str | Path) -> dict:
    """Full structured view of one store: whichever of the known artifact kinds
    are present. Unknown kinds are still surfaced (name + count) so a new probe
    type is visible before this parser learns to shape it."""
    store_dir = Path(store_dir)
    by_kind, skipped = read_store_artifacts(store_dir)
    detail: dict = {"name": store_dir.name, "skipped": skipped,
                    "kinds": {k: len(v) for k, v in by_kind.items()}}

    prereg = (by_kind.get("preregistration") or [None])[0]
    if prereg:
        heldout = prereg.get("heldout") or []
        dev = prereg.get("dev") or []
        detail["prereg"] = {
            "dev_n": len(dev), "heldout_n": len(heldout),
            "task": prereg.get("task"), "policy": prereg.get("policy"),
            "stages": bool(prereg.get("stages")),
            "terminal_label": prereg.get("terminal_label"),
            "alpha": prereg.get("alpha"), "min_fixed": prereg.get("min_fixed"),
            "percept_noise": prereg.get("percept_noise"),
            "heldout_block": [min(heldout), max(heldout) + 1] if heldout else None,
        }

    gens = []
    for g in by_kind.get("generation", []):
        dg, bg = g.get("dev_gate", {}), g.get("blind_gate", {})
        gens.append({
            "generation": g.get("generation"), "promoted": g.get("promoted"),
            "reason": g.get("reason"), "rule": _rule_view(g.get("rule", {})),
            "dev_gate": dg, "dev_delta": _delta(dg),
            "blind_gate": bg, "blind_delta": _delta(bg),
        })
    if gens:
        detail["generations"] = sorted(gens, key=lambda x: x["generation"] or 0)

    result = (by_kind.get("campaign_result") or [None])[0]
    if result:
        detail["result"] = {
            "generations": result.get("generations"),
            "promoted": result.get("promoted"), "rules": result.get("rules"),
            "heldout": result.get("heldout"),
            "heldout_delta": _delta(result.get("heldout", {})),
            "heldout_vs_blind": result.get("heldout_vs_blind"),
            "ablation": _ablation(result.get("ablation", [])),
        }

    rescores = []
    for r in by_kind.get("heldout_rescore", []):
        paired = r.get("paired", {})
        rescores.append({
            "block": r.get("block"), "n": r.get("n"),
            "paired": paired, "delta": _delta(paired),
            "vs_blind": r.get("vs_blind"), "judgement": r.get("judgement"),
            "stage_attribution": r.get("stage_attribution"),
        })
    if rescores:
        detail["rescores"] = rescores

    r25 = (by_kind.get("round25_rerun") or [None])[0]
    if r25:
        arms = {}
        for name, arm in (r25.get("arms") or {}).items():
            arms[name] = {"trigger_str": trigger_str((arm.get("rule") or {}).get("trigger", {})),
                          "heldout": arm.get("heldout")}
        detail["round25"] = {"dev_block": r25.get("dev_block"),
                             "heldout_block": r25.get("heldout_block"), "arms": arms}

    probe = (by_kind.get("arm_time_probe") or [None])[0]
    if probe:
        detail["probe"] = {"grade": probe.get("grade"), "p1": probe.get("p1"),
                          "p2": probe.get("p2")}

    fix = (by_kind.get("round88_fix") or [None])[0]
    if fix:
        detail["round88_fix"] = {"grade": fix.get("grade"), "dev_rate": fix.get("dev_rate"),
                                "top_score": fix.get("top_score"), "anchors": fix.get("anchors"),
                                "heldout": fix.get("heldout")}
    return detail


def store_summary(store_dir: str | Path) -> dict:
    """Cheap card for the store list: counts, task, promoted/total generations,
    mtime. Reads the artifacts once (stores are tiny), so the list is honest
    about mid-write skips too."""
    store_dir = Path(store_dir)
    by_kind, skipped = read_store_artifacts(store_dir)
    prereg = (by_kind.get("preregistration") or [None])[0]
    result = (by_kind.get("campaign_result") or [None])[0]
    gens = by_kind.get("generation", [])
    return {
        "name": store_dir.name,
        "mtime": store_mtime(store_dir),
        "kinds": {k: len(v) for k, v in by_kind.items()},
        "task": prereg.get("task") if prereg else None,
        "generations": len(gens),
        "promoted": sum(1 for g in gens if g.get("promoted")),
        "heldout": (result or {}).get("heldout", {}).get("fixed") if result else None,
        "skipped": skipped,
    }


def list_stores(runs_dir: str | Path) -> list[dict]:
    """Every store under runs_dir, newest first -- auto-discovery, TensorBoard-style."""
    runs_dir = Path(runs_dir)
    stores = [store_summary(p) for p in sorted(runs_dir.iterdir()) if p.is_dir() and is_store(p)]
    return sorted(stores, key=lambda s: s["mtime"], reverse=True)


def heldout_blocks(runs_dir: str | Path, store_name: str) -> dict:
    """The multi-block held-out comparison for a campaign: its own scored block
    plus every sibling ``<name>-rescore-*`` store's rescored block. Each block
    carries the paired numbers and (when the campaign ran a stage overlay) the
    grasp/place stage attribution. This is view #2 -- stack-g1's three blocks."""
    runs_dir = Path(runs_dir)
    detail = store_detail(runs_dir / store_name)
    blocks = []
    result = detail.get("result")
    if result and result.get("heldout"):
        h = result["heldout"]
        prereg = detail.get("prereg", {})
        first = (prereg.get("heldout_block") or [None])[0]
        blocks.append({"source": store_name, "block": first, "paired": h,
                       "delta": result.get("heldout_delta"),
                       "vs_blind": result.get("heldout_vs_blind"),
                       "stage_attribution": None})
    for p in sorted(runs_dir.iterdir()):
        if not (p.is_dir() and is_store(p) and p.name.startswith(f"{store_name}-rescore")):
            continue
        for rs in store_detail(p).get("rescores", []):
            blocks.append({"source": p.name, "block": rs["block"], "paired": rs["paired"],
                           "delta": rs["delta"], "vs_blind": rs.get("vs_blind"),
                           "judgement": rs.get("judgement"),
                           "stage_attribution": rs.get("stage_attribution")})
    blocks.sort(key=lambda b: b["block"] if b["block"] is not None else 0)
    return {"store": store_name, "task": detail.get("prereg", {}).get("task"), "blocks": blocks}


# --- runtime session chain --------------------------------------------------
#
# The resident runtime (scripts/harness_runtime.py) writes ONE long-lived
# SessionLog under ``<session>/session-log/rows.jsonl`` -- a different on-disk
# shape from a campaign store (chained rows carrying their data inline, no
# content-addressed artifacts). Kept deliberately separate from is_store/
# list_stores so a runtime session never renders as an empty campaign.


def is_session(session_dir: str | Path) -> bool:
    """A directory is a runtime session iff it carries session-log/rows.jsonl."""
    return (Path(session_dir) / "session-log" / "rows.jsonl").exists()


def _chain_rows(log_dir: Path) -> tuple[list[dict], int]:
    """Whole rows in seq order + a mid-write skip count -- same partial-tolerant
    line loop as _index_rows: the runtime appends rows.jsonl line by line, so a
    live poll can catch a half-written trailing (or non-row) line. Skip it (and
    count it) rather than crash; the next poll gets the whole row."""
    rows: list[dict] = []
    skipped = 0
    path = log_dir / "rows.jsonl"
    if not path.exists():
        return rows, skipped
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(row, dict) and "kind" in row and "data" in row:
            rows.append(row)
        else:
            skipped += 1
    return rows, skipped


def _session_rows(log_dir: Path) -> tuple[dict[str, list[dict]], int]:
    """Row ``data`` payloads grouped by kind, plus a skip count. Thin regroup
    over _chain_rows (which does the partial-tolerant read)."""
    rows, skipped = _chain_rows(log_dir)
    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row["data"])
    return by_kind, skipped


def chain_rows(session_dir: str | Path) -> list[dict]:
    """Whole session chain rows (seq/kind/data/...) in order, mid-write tolerant.
    read_session groups payloads by kind and drops seq; this keeps rows whole so
    a reader can attribute one to a brief (used by mcp_server.run_task)."""
    return _chain_rows(Path(session_dir) / "session-log")[0]


def _session_mtime(log_dir: Path) -> float:
    """rows.jsonl mtime -- the LIVE signal; a new note appends to it."""
    try:
        return (log_dir / "rows.jsonl").stat().st_mtime
    except OSError:
        return 0.0


def read_session(session_dir: str | Path) -> dict:
    """One runtime session: note payloads grouped by kind, a mid-write skip
    count, and whether the hash chain still verifies.

    ``chain_ok`` reuses the writer's own SessionLog.load().verify() -- the one
    audited chain check -- rather than reimplementing the hash fold. A truncated
    trailing line (mid-write) makes load() choke, so it is caught and read as
    "not currently verifiable" (False); the skip count disambiguates that from a
    real tamper in the UI, and the next poll recovers.
    """
    session_dir = Path(session_dir)
    log_dir = session_dir / "session-log"
    by_kind, skipped = _session_rows(log_dir)
    try:
        chain_ok = SessionLog.load(log_dir).verify()
    except (OSError, json.JSONDecodeError):
        chain_ok = False
    return {"name": session_dir.name, "mtime": _session_mtime(log_dir),
            "chain_ok": chain_ok, "skipped": skipped,
            "kinds": {k: len(v) for k, v in by_kind.items()}, "rows": by_kind}


def read_runtime_status(session_dir: str | Path) -> dict | None:
    """The resident runtime's LIVE status for one session
    (``<session>/runtime_status.json``: pid/render/mode/boot_ts/display),
    overwritten each boot. ``None`` when absent (a session that has not booted
    since the file existed) or mid-write -- a plain read, not a chain row: this
    is live operational state, not sealed evidence, so no chain verify. The pid
    is reported, never judged: liveness is the reader's call, not this layer's."""
    path = Path(session_dir) / "runtime_status.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def read_runtime_events(session_dir: str | Path, after_seq: int = 0) -> dict:
    """The resident runtime's OPERATIONAL event feed for one session
    (``<session>/runtime_events.jsonl``, written by harness.opstream: truncated
    per boot, one JSON line per event) -- events with ``seq > after_seq`` plus
    ``last_seq``, the newest seq in the file. Live state, not sealed evidence:
    no chain verify, absent file reads as an empty feed.

    Cursor contract: a poller passes its last-seen seq and appends the returned
    events; ``last_seq < after_seq`` means the runtime re-booted (the file was
    truncated and seq restarted), so the poller resets its cursor to 0 and
    re-reads. Same partial-tolerant line loop as the other jsonl readers: a
    half-written trailing line is skipped and picked up whole on the next poll.
    """
    path = Path(session_dir) / "runtime_events.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {"events": [], "last_seq": 0}
    events: list[dict] = []
    last_seq = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not (isinstance(row, dict) and isinstance(row.get("seq"), int)):
            continue
        last_seq = max(last_seq, row["seq"])
        if row["seq"] > after_seq:
            events.append(row)
    return {"events": events, "last_seq": last_seq}


def discover_sessions(runs_dir: str | Path) -> list[dict]:
    """Every runtime session under runs_dir, newest first -- summary cards (no
    row payloads) for the sidebar. Sessions are tiny, so this reads each once."""
    runs_dir = Path(runs_dir)
    out = []
    for p in sorted(runs_dir.iterdir()):
        if p.is_dir() and is_session(p):
            s = read_session(p)
            out.append({k: s[k] for k in ("name", "mtime", "chain_ok", "kinds", "skipped")})
    return sorted(out, key=lambda s: s["mtime"], reverse=True)


# --- markdown feeds ---------------------------------------------------------

_RANGE = re.compile(r"(\d{4,5})-(\d{4,5})")
_STATE_ORDER = {"planned": 0, "reserved": 1, "burned": 2}


def parse_ledger(text: str) -> list[dict]:
    """Seed-block burn map from STATUS.md's ``区块预算`` section.

    The ledger is prose, not a table, so this is a best-effort extraction: pull
    every ``NNNN-NNNN`` range from the budget section, classify each by the
    keyword in its sentence (已烧 -> burned, 留/需要时 -> reserved, else
    planned), and keep the strongest state when a range recurs (a held-out
    block first planned then later burned reads as burned). A bullet wrapped
    onto continuation lines keeps its classification: lines without a keyword
    inherit the last keyword seen since the bullet started (a ``**`` line or a
    blank line resets it), and a line's own keyword takes precedence over the
    inherited one. The source line is carried for a tooltip so a fuzzy
    classification is always auditable.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if "区块预算" in ln), None)
    if start is None:
        return []
    seg: list[str] = []
    for ln in lines[start:]:
        if "frontier" in ln and seg:
            break
        seg.append(ln)
    found: dict[tuple[int, int], dict] = {}
    carry = ""  # state inherited by wrapped continuation lines of one **...** bullet
    for ln in seg:
        if not ln.strip() or ln.lstrip().startswith("**"):
            carry = ""
        burned = "已烧" in ln
        reserved = "留" in ln or "需要时" in ln
        state = "burned" if burned else "reserved" if reserved else carry or "planned"
        if burned or reserved:
            carry = state
        for m in _RANGE.finditer(ln):
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi <= lo:
                continue
            key = (lo, hi)
            prev = found.get(key)
            if prev is None or _STATE_ORDER[state] > _STATE_ORDER[prev["state"]]:
                found[key] = {"lo": lo, "hi": hi, "state": state,
                              "line": re.sub(r"\*\*|`", "", ln).strip()}
    return sorted(found.values(), key=lambda r: r["lo"])


_ROUND = re.compile(r"^##\s*Round\s+(\d+)\s*-\s*([\d-]+)\s*-\s*(.*)$")


def parse_rounds(text: str) -> list[dict]:
    """progress.md ``## Round N - DATE - TITLE`` sections, latest first, each with
    its body text for a collapsible rounds feed."""
    lines = text.splitlines()
    heads = [(i, m) for i, ln in enumerate(lines) if (m := _ROUND.match(ln))]
    rounds = []
    for idx, (i, m) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = "\n".join(lines[i + 1:end]).strip()
        rounds.append({"round": int(m.group(1)), "date": m.group(2),
                       "title": m.group(3).strip(), "body": body})
    rounds.sort(key=lambda r: r["round"], reverse=True)
    return rounds
