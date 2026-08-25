"""The Skill Vault fold (board/vault.py) is a deterministic read over sealed data.

Four properties, all over the LIVE repo tree (the fold's only honest fixture is
the sealed evidence itself -- like the plugin_doctor --verify-claim tests, these
skip when a fresh clone lacks runs/):

- fold over real runs/ yields the known nodes + edges (place descends from stack,
  skill_place claims place, the privileged/observable REQUIRES split, SUPERSEDES);
- determinism: two folds are byte-identical (json.dumps sort_keys);
- face byte-equivalence: build_graph == storecli stdout == mcp tool, all 3 fns;
- vault_doctor red/green: a reserved key / unknown node / dangling see_also each
  fails loud, a valid additive annotation attaches and overwrites no derived field.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from board import mcp_server as ms
from board import storecli
from board import vault as bv

REPO = Path(bv.__file__).resolve().parent.parent
RUNS = REPO / "runs"

STACK = "57162e40d2bd4a0d59973d8c51d19f7267b682ba582c7b5c84568b334f02d41d"
ADC = "adc5578932681b6607737cdee40164c472e1bde277b0637a3b2c02623a3c4440"
EB = "eb46481a88b93cf9db9e774734fdde063725557d83f1abffe3033cd33a45a40f"

# the sealed evidence is not in git; a fresh clone legitimately skips (base-gate
# fresh-clone variance), never fails.
_sealed = pytest.mark.skipif(
    not (RUNS / "stack-g1" / "skills" / f"{STACK}.json").exists(),
    reason="sealed runs/ evidence not in this checkout")


@_sealed
def test_fold_over_real_runs():
    g = bv.build_graph(RUNS)
    by_id = {n["id"]: n for n in g["nodes"]}
    edges = {(e["rel"], e["src"], e["dst"]) for e in g["edges"]}

    # the three mounted skills + the 11 cards + the 9 capabilities are all present.
    assert {STACK, ADC, EB} <= set(by_id)
    assert sum(n["kind"] == "package" for n in g["nodes"]) == 11
    assert sum(n["kind"] == "capability" for n in g["nodes"]) == 9

    stack = by_id[STACK]
    assert stack["kind"] == "skill" and stack["task"] == "stack"
    assert stack["status"] == "promoted" and stack["privilege"] == 0
    assert stack["evidence"]["heldout"]["governed_rate"] == 0.65  # verbatim
    assert stack["evidenced_by"] == "stack-g1"

    assert by_id[EB]["privilege"] == 1 and by_id[EB]["status"] == "promoted"
    # gen-2 place descends from gen-1 place (child_sha chain) AND from stack (prereg)
    assert ("DESCENDS_FROM", EB, ADC) in edges
    assert ("DESCENDS_FROM", ADC, STACK) in edges
    assert ("DESCENDS_FROM", EB, STACK) in edges

    # package <-> skill references (the operator's 互相包含引用)
    assert ("CLAIMS", "plugins/skill_place", ADC) in edges
    assert ("CLAIMS", "plugins/skill_place", EB) in edges
    assert ("CLAIMS", "plugins/task", STACK) in edges
    assert ("BINDS", "plugins/task", "stack") in edges
    assert ("PROVIDES", "plugins/embodiment_robosuite", "embodiment.env") in edges

    # the transfer story: privileged trigger REQUIRES ground_truth, observable percept.
    assert ("REQUIRES", EB, "embodiment.ground_truth") in edges
    assert ("REQUIRES", STACK, "percept.model") in edges
    # the packaging duplicate-seam: enabled reasoner over disabled model_qwen.
    assert ("SUPERSEDES", "plugins/reasoner", "plugins/model_qwen") in edges

    # every capability the skills/packages point at resolves to a real node.
    assert by_id["embodiment.ground_truth"]["privileged"] is True


@_sealed
def test_node_page_has_both_directions():
    g = bv.build_graph(RUNS)
    page = bv.node(g, STACK)
    out = {(e["rel"], e["dst"]) for e in page["out"]}
    back = {(e["rel"], e["src"]) for e in page["backlinks"]}
    assert ("REQUIRES", "percept.model") in out           # skill -> capability
    assert ("EVIDENCED_BY", "stack-g1") in out            # skill -> store
    assert ("CLAIMS", "plugins/task") in back             # package -> this skill
    assert ("DESCENDS_FROM", ADC) in back                 # descendant -> this skill


def test_unknown_node_is_an_error():
    g = bv.build_graph(RUNS)
    assert bv.node(g, "nope") == {"error": "unknown node"}
    assert bv.neighbors(g, "nope") == {"error": "unknown node"}


@_sealed
def test_determinism_byte_identical():
    a = json.dumps(bv.build_graph(RUNS), sort_keys=True)
    b = json.dumps(bv.build_graph(RUNS), sort_keys=True)
    assert a == b


@_sealed
def test_faces_byte_equivalent():
    """All three faces are the SAME function (round-95 discipline)."""
    ms.configure(RUNS)
    g = bv.build_graph(RUNS)

    def cli(*argv):
        r = subprocess.run([sys.executable, "-m", "board.storecli", *argv,
                            "--runs", str(RUNS)], capture_output=True, text=True, cwd=REPO)
        assert r.returncode == 0, r.stderr
        return r.stdout.rstrip("\n")

    assert cli("vault") == json.dumps(g) == json.dumps(ms.vault())
    assert cli("vault_node", EB) == json.dumps(bv.node(g, EB)) == json.dumps(ms.vault_node(EB))
    assert (cli("vault_neighbors", EB, "--relation", "DESCENDS_FROM")
            == json.dumps(bv.neighbors(g, EB, "DESCENDS_FROM"))
            == json.dumps(ms.vault_neighbors(EB, "DESCENDS_FROM")))


# --- vault_doctor: additive annotations can add context, never contradict -----


def _graph_with(tmp_path, ann):
    """A tiny graph over a fake node id + a sidecar dir carrying one annotation."""
    graph = {"nodes": [{"kind": "skill", "id": "n1"}, {"kind": "skill", "id": "n2"}],
             "edges": []}
    ann_dir = tmp_path / "ann"
    ann_dir.mkdir()
    (ann_dir / "n1.json").write_text(json.dumps(ann))
    return graph, ann_dir


def test_vault_doctor_green_valid_annotation_attaches(tmp_path):
    graph, ann_dir = _graph_with(tmp_path, {"note": "hand link", "see_also": ["n2"]})
    assert bv.vault_doctor(graph, ann_dir) == []
    # the loader attaches under node.annotations and touches no derived field.
    nodes = [{"kind": "skill", "id": "n1", "status": "promoted"}]
    bv._attach_annotations(nodes, ann_dir)
    assert nodes[0]["annotations"] == {"note": "hand link", "see_also": ["n2"]}
    assert nodes[0]["status"] == "promoted"  # derived field untouched


def test_vault_doctor_red_reserved_key(tmp_path):
    graph, ann_dir = _graph_with(tmp_path, {"status": "retired"})  # derived key
    errs = bv.vault_doctor(graph, ann_dir)
    assert errs and "additive set" in errs[0]
    # and a reserved key is NOT loaded over the node's derived field.
    nodes = [{"kind": "skill", "id": "n1", "status": "promoted", "annotations": None}]
    bv._attach_annotations(nodes, ann_dir)
    assert nodes[0]["annotations"] is None and nodes[0]["status"] == "promoted"


def test_vault_doctor_red_unknown_node(tmp_path):
    graph = {"nodes": [{"kind": "skill", "id": "n2"}], "edges": []}
    ann_dir = tmp_path / "ann"
    ann_dir.mkdir()
    (ann_dir / "ghost.json").write_text(json.dumps({"note": "x"}))
    errs = bv.vault_doctor(graph, ann_dir)
    assert errs and "unknown node" in errs[0]


def test_vault_doctor_red_dangling_see_also(tmp_path):
    graph, ann_dir = _graph_with(tmp_path, {"see_also": ["does_not_exist"]})
    errs = bv.vault_doctor(graph, ann_dir)
    assert errs and "see_also target" in errs[0]
