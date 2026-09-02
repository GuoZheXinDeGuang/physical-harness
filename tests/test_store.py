"""Unit tests for the RSI board's pure parse layer (board/store.py).

No server, no simulator: fake CampaignStore directories are hand-built in a
tmp_path so the parser is exercised against the exact on-disk shape
`plugins.rsi.campaign.CampaignStore` writes (index.jsonl + content-addressed
artifacts/<sha>.json, kind carried only in the index).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from board import store as bs


def _mkstore(root: Path, artifacts: list[tuple[str, dict]]) -> Path:
    """Write a store the way CampaignStore does: each payload under a content
    hash, kind recorded only in index.jsonl."""
    (root / "artifacts").mkdir(parents=True)
    lines = []
    for seq, (kind, payload) in enumerate(artifacts):
        digest = _digest(payload, seq)
        (root / "artifacts" / f"{digest}.json").write_text(json.dumps(payload))
        lines.append(json.dumps({"seq": seq, "kind": kind, "sha": digest, "time": seq}))
    (root / "index.jsonl").write_text("\n".join(lines) + "\n")
    return root


def _digest(payload: dict, seq: int) -> str:
    import hashlib
    return hashlib.sha256((json.dumps(payload, sort_keys=True) + str(seq)).encode()).hexdigest()


def _paired(base, gov, fixed, broken, p, n=100, fires=0, priv=0) -> dict:
    return {"n": n, "base_rate": base, "governed_rate": gov, "fixed": fixed,
            "broken": broken, "p_value": p, "fires": fires, "declared_privilege": priv}


def _campaign(root: Path) -> Path:
    prereg = {"dev": list(range(1000, 1196)), "heldout": list(range(2000, 2100)),
              "task": "stack", "policy": "scripted", "stages": [{"name": "grasp"}],
              "terminal_label": True, "alpha": 0.05, "min_fixed": 3, "percept_noise": 0.0}
    gen1 = {"generation": 1, "promoted": True, "reason": "promoted",
            "rule": {"rule_id": "g1", "trigger": {"feature": "observable.finger_gap",
                     "op": "lt", "threshold": 0.001, "dwell": 1, "arm_after": 58,
                     "reducer": "value"}, "recovery": {"name": "regrasp"}},
            "dev_gate": _paired(0.60, 0.66, 12, 0, 0.0005, n=196, fires=58),
            "blind_gate": _paired(0.60, 0.64, 8, 2, 0.01)}
    gen2 = {"generation": 2, "promoted": False, "reason": "rejected (fixed=16 broken=12 p=0.57)",
            "rule": {"rule_id": "g2", "trigger": {"feature": "observable.finger_gap",
                     "op": "lt", "threshold": 0.0386, "dwell": 1, "arm_after": 62,
                     "reducer": "value"}, "recovery": {"name": "regrasp"}},
            "dev_gate": _paired(0.63, 0.64, 16, 12, 0.57, n=385),
            "blind_gate": _paired(0.63, 0.63, 10, 10, 0.9)}
    result = {"generations": 2, "promoted": 1, "rules": ["g1"],
              "heldout": _paired(0.585, 0.65, 13, 0, 0.0002, n=200, fires=54),
              "heldout_vs_blind": _paired(0.65, 0.72, 79, 8, 1e-9),
              "ablation": [[0.0, _paired(0.585, 0.765, 36, 0, 1e-11, n=200, priv=1)],
                           [0.01, _paired(0.585, 0.72, 30, 0, 1e-6, n=200)]]}
    return _mkstore(root, [("preregistration", prereg), ("generation", gen1),
                           ("generation", gen2), ("campaign_result", result)])


def test_store_detail_campaign(tmp_path):
    d = bs.store_detail(_campaign(tmp_path / "camp"))
    assert d["prereg"]["task"] == "stack"
    assert d["prereg"]["dev_n"] == 196 and d["prereg"]["heldout_n"] == 100
    assert d["prereg"]["stages"] is True
    assert len(d["generations"]) == 2
    g1, g2 = d["generations"]
    assert g1["promoted"] is True and g2["promoted"] is False
    assert g1["rule"]["recovery"] == "regrasp"
    assert "finger_gap" in g1["rule"]["trigger_str"] and "@arm58" in g1["rule"]["trigger_str"]
    # delta = governed - base, to floating tolerance
    assert abs(g1["dev_delta"] - (0.66 - 0.60)) < 1e-9
    assert d["result"]["promoted"] == 1 and d["result"]["rules"] == ["g1"]
    assert abs(d["result"]["heldout_delta"] - (0.65 - 0.585)) < 1e-9
    assert len(d["result"]["ablation"]) == 2
    assert d["result"]["ablation"][0]["declared_privilege"] == 1


def test_list_and_summary(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _campaign(runs / "stack-g1")
    (runs / "not-a-store").mkdir()  # no index.jsonl -> ignored
    stores = bs.list_stores(runs)
    assert [s["name"] for s in stores] == ["stack-g1"]
    s = stores[0]
    assert s["task"] == "stack" and s["generations"] == 2 and s["promoted"] == 1
    assert s["heldout"] == 13


def test_heldout_blocks_with_rescores(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _campaign(runs / "stack-g1")
    rescore = {"block": 42200, "n": 200, "judgement": True,
               "paired": _paired(0.60, 0.70, 19, 0, 1e-8, n=200),
               "vs_blind": _paired(0.70, 0.78, 40, 5, 1e-6),
               "stage_attribution": {"n": 200, "successes": 127, "stage_order": ["grasp", "place"],
                   "per_stage": {"grasp": {"reached": 200, "success": 171},
                                 "place": {"reached": 200, "success": 113}},
                   "first_failure": {"grasp": 28, "place": 45, "(terminal)": 0}}}
    _mkstore(runs / "stack-g1-rescore-42200", [("heldout_rescore", rescore)])
    hb = bs.heldout_blocks(runs, "stack-g1")
    assert hb["task"] == "stack"
    blocks = hb["blocks"]
    assert len(blocks) == 2  # campaign's own block + one rescore
    assert blocks[0]["block"] == 2000  # campaign held-out min
    assert blocks[1]["block"] == 42200 and blocks[1]["stage_attribution"]["stage_order"] == ["grasp", "place"]
    assert blocks[1]["paired"]["fixed"] == 19


def test_partial_write_is_skipped_not_fatal(tmp_path):
    """A mid-write artifact (unparseable JSON) and a half-written index line are
    both skipped; the readable rest still parses. This is the LIVE guarantee."""
    root = _campaign(tmp_path / "camp")
    # corrupt one artifact file in place
    victim = next((root / "artifacts").glob("*.json"))
    victim.write_text('{"generation": 1, "dev_gate":')  # truncated mid-write
    # append a half-written index line
    with (root / "index.jsonl").open("a") as fh:
        fh.write('{"seq": 9, "kind": "generation", "sha": "dead')
    _by_kind, skipped = bs.read_store_artifacts(root)
    assert skipped == 1  # the corrupted artifact
    # the truncated index line was dropped, not counted as an artifact
    d = bs.store_detail(root)
    assert d["skipped"] == 1
    assert "prereg" in d  # readable artifacts still parsed


def test_parse_ledger():
    # Mirrors STATUS.md's real markdown wrapping: the plan spans several lines,
    # each **bullet** classified independently, strongest state winning on
    # repeat mentions.
    text = (
        "**PHASE 3 区块预算(round 78 起):** phase 1/2 已烧段 held-out 1000-1999;\n"
        "**标定 40000-40999**(pass A 座高 40000-40199);\n"
        "**held-out 42000-42199 / 42200-42399**;\n"
        "选择 43000+ 需要时用; 44000+ 留后续 rung。\n"
        "**已烧(round 78-79):** 标定 40000-40799; stack-g1 dev 41000-41580; held-out 42000-42199。\n"
        "**已烧(round 85):** stack held-out 42200-42399。\n"
        "\n"
        "**frontier:** something 99999-99998 should be ignored below the section.\n"
    )
    ranges = bs.parse_ledger(text)
    by = {(r["lo"], r["hi"]): r["state"] for r in ranges}
    # 42000-42199 appears planned then burned -> burned wins
    assert by[(42000, 42199)] == "burned"
    assert by[(42200, 42399)] == "burned"
    assert by[(41000, 41580)] == "burned"
    assert by[(40000, 40999)] == "planned"  # only ever mentioned as plan
    # frontier line excluded
    assert (99999, 99998) not in by  # also hi<=lo would be dropped anyway
    assert all(r["lo"] < r["hi"] for r in ranges)


def test_parse_ledger_wrapped_entry():
    # A **已烧** bullet wrapped across physical lines (the round-96 STATUS.md
    # entry): ranges on continuation lines inherit the bullet's state instead
    # of reading planned. A continuation clause with its own 留 keyword
    # overrides the inherited burn, and the next **bullet** resets the carry.
    text = (
        "区块预算\n"
        "**已烧(round 96 夜)**: 复现块 #2 = 47400-47599(判定确立),\n"
        "#3 = 48000-48199(判定确立)。\n"
        "**已烧(round 90):** 41581-41880(探针);\n"
        "41881-41999 留 smoke/后续 bring-up。\n"
        "**held-out 50000-50199**;\n"
        "frontier\n"
    )
    by = {(r["lo"], r["hi"]): r["state"] for r in bs.parse_ledger(text)}
    assert by[(47400, 47599)] == "burned"
    assert by[(48000, 48199)] == "burned"  # wrapped line inherits 已烧
    assert by[(41581, 41880)] == "burned"
    assert by[(41881, 41999)] == "reserved"  # own 留 beats inherited burn
    assert by[(50000, 50199)] == "planned"  # new bullet resets the carry


def test_parse_ledger_weishao_unburns():
    # The round-101 trap: one giant **已烧** bullet also narrates that the
    # held-out block is NOT burned yet. 未烧 right after the range must beat
    # the line's 已烧, or the runtime burn-guard blocks the next headline.
    text = (
        "区块预算\n"
        "**已烧(round 101):** dev 49050-49349 真跑烧掉; held-out 49350-49549 本相未烧。\n"
        "frontier\n"
    )
    by = {(r["lo"], r["hi"]): r["state"] for r in bs.parse_ledger(text)}
    assert by[(49050, 49349)] == "burned"
    assert by[(49350, 49549)] == "planned"


def test_parse_rounds():
    text = (
        "intro\n"
        "## Round 87 - 2026-08-22 - frontier cleanup\nbody 87 line 1\nbody 87 line 2\n"
        "## Round 88 - 2026-08-22 - objective hypothesis\nbody 88\n"
    )
    rounds = bs.parse_rounds(text)
    assert [r["round"] for r in rounds] == [88, 87]  # latest first
    assert rounds[0]["title"] == "objective hypothesis"
    assert rounds[1]["date"] == "2026-08-22"
    assert "body 87 line 2" in rounds[1]["body"]


def test_missing_store_is_not_a_store(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert bs.is_store(empty) is False
    assert bs.list_stores(tmp_path) == []


def test_burned_blocks_derives_from_sealed_preregs_only(tmp_path):
    """The ledger is a projection of sealed preregistrations: two stores at two
    depths burn their dev (gate) and held-out blocks, non-prereg artifacts and
    calibration never burn, and no store at all is a refusal, not an empty set."""
    with pytest.raises(ValueError, match="no campaign store"):
        bs.burned_blocks(tmp_path)
    _mkstore(tmp_path / "only-rescore", [("heldout_rescore", {"block": 1})])
    assert bs.burned_blocks(tmp_path) == []  # stores exist, nothing sealed a prereg
    a = _mkstore(tmp_path / "a", [("preregistration",
                                  {"dev": list(range(100, 200)), "heldout": [300, 301, 305]})])
    b = _mkstore(tmp_path / "s" / "campaigns" / "b",
                 [("calibration", {"seeds": list(range(0, 50))}),
                  ("preregistration", {"dev": [500], "heldout": list(range(600, 610))})])
    sha = lambda root: json.loads(root.joinpath("index.jsonl").read_text().splitlines()[-1])["sha"]
    assert bs.burned_blocks(tmp_path) == [
        (100, 199, "gate", sha(a)), (300, 301, "heldout", sha(a)), (305, 305, "heldout", sha(a)),
        (500, 500, "gate", sha(b)), (600, 609, "heldout", sha(b))]


def test_burned_blocks_unions_status_md_prose(tmp_path):
    """Pre-store history lives only in STATUS.md; the derived guard must not be weaker."""
    runs = tmp_path / "runs"; store = runs / "s"; (store / "artifacts").mkdir(parents=True)
    (store / "index.jsonl").write_text("")
    (tmp_path / "STATUS.md").write_text("## 区块预算\n已烧: held-out 1000-1999。\n")
    rows = bs.burned_blocks(runs)
    assert (1000, 1999, "prose", "") in rows
