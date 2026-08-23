"""The 机箱 view (board/cards.py) is the SAME function on both faces.

Round-95 discipline (test_storecli.py): the CLI ``cards`` subcommand's stdout,
the MCP ``list_cards`` tool result, and ``board.cards.list_cards()`` must be the
one byte-identical dict -- no second reader, no reinterpretation. And the read
must never import a plugin: listing the chassis parses manifests as data only.

Runs against the REAL plugins/ tree (production manifest shapes), not a mock.
"""

from __future__ import annotations

import json
import sys

from board import cards as bc
from board import mcp_server as ms
from board import storecli


def _run(capsys, *argv) -> tuple[int, str]:
    code = storecli.main(list(argv))
    return code, capsys.readouterr().out.rstrip("\n")


def test_both_faces_are_byte_identical_to_list_cards(capsys):
    expected = bc.list_cards()
    assert expected, "the real chassis has seated cards -- identity is not of-empty"

    code, out = _run(capsys, "cards")
    assert code == 0
    assert out == json.dumps(expected)          # CLI face
    assert json.dumps(ms.list_cards()) == json.dumps(expected)  # MCP face


def test_list_cards_shape_and_defaults():
    by_name = {c["name"]: c for c in bc.list_cards()}
    # a sim card that declares needs_sim, and its contributions folded by name
    emb = by_name["embodiment_robosuite"]
    assert emb["actuation"] == "sim" and emb["needs_sim"] is True
    assert emb["contributes"]["mounts"] == ["embodiment.env", "percept.model"]
    assert emb["contributes"]["bundles"] == ["sawyer"]
    assert emb["dir"] == "plugins/embodiment_robosuite"
    # an inactive card is still listed (chassis view is total, unlike discover)
    assert by_name["model_qwen"]["manifest"]["enabled"] is False
    # a card with no chassis flags gets the manifest.py defaults
    assert by_name["task"]["actuation"] == "sim" and by_name["task"]["needs_sim"] is False
    assert by_name["task"]["contributes"]["task_bindings"] == ["clear_table", "stack"]


def test_list_cards_imports_no_plugin_code():
    """Parse-as-data only: calling it lands no plugins.* module in sys.modules."""
    before = {m for m in sys.modules if m.startswith("plugins")}
    bc.list_cards()
    after = {m for m in sys.modules if m.startswith("plugins")}
    assert after - before == set()
