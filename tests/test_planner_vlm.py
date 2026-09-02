"""The planner_vlm card: VLM graph generation behind the untrusted-planner boundary.

Everything runs against a canned local HTTP endpoint (the test_model_endpoint
server pattern) -- no real model anywhere. Properties:

- a canned strict-JSON reply becomes a plan that passes validate_plan whole
  (including the verify-coverage rule);
- two unparseable replies (initial + the one re-ask carrying the parse error)
  yield a graph validate_plan REFUSES -- the invalid_plan fold-back channel,
  never a silently invented graph;
- generate-once-then-frozen: the same (task, seed) replays a byte-identical
  graph from the process cache without re-asking the model;
- a replan prompt echoes the completed nodes verbatim (the replan-stability
  contract handed to the model);
- plugin_doctor: deterministic=False is exempt (shape validated, not diffed),
  a dead endpoint is a graceful SKIP, and the committed card's stack_vlm
  binding passes Tier A while leaving the discover() fold collision-free.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import plugins.planner_vlm as pv
from harness import protocol
from harness.skill_library import RECORDS
from harness.contracts import TaskPlanner
from harness.manifest import discover
from plugins.planner_vlm import provider
from plugins.task.planner_stack import CATALOGUE, ORACLES
from plugins.task.validate import validate_plan
from scripts.plugin_doctor import check

_DEAD = "http://127.0.0.1:9/v1"

#: The canned good graph: two pick nodes, full verify coverage.
GOOD = {
    "goal": "clear the table",
    "nodes": [
        {"id": "pick-can", "skill": "pick", "args": {"object": "can"}, "after": []},
        {"id": "pick-milk", "skill": "pick", "args": {"object": "milk"},
         "after": ["pick-can"]},
    ],
    "verify": [
        {"after": "pick-can", "predicate": "pick_success"},
        {"after": "pick-milk", "predicate": "pick_success"},
    ],
}


class _Server(BaseHTTPRequestHandler):
    """OpenAI-shaped fake: GET /models + POST /chat/completions off a reply queue."""

    replies: list[str] = []
    requests: list[dict] = []

    def do_GET(self):
        self._reply({"data": [{"id": "fake-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Server.requests.append(body)
        text = _Server.replies.pop(0) if _Server.replies else json.dumps(GOOD)
        self._reply({"choices": [{"message": {"content": text}}]})

    def _reply(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def endpoint_url():
    _Server.replies, _Server.requests = [], []
    server = HTTPServer(("127.0.0.1", 0), _Server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    thread.join()


def _planner(url: str) -> pv.VlmPlanner:
    return provider(endpoint_params={"base_url": url})


def _brief(seed: int = 0, **extra) -> dict:
    return {"task": "clear_table", "seed": seed, "scene": {}, "budget": 3,
            "catalogue": CATALOGUE, "oracles": ORACLES, **extra}


def _payload(request: dict) -> dict:
    """The JSON planning payload inside the prompt's user message."""
    content = request["messages"][1]["content"]
    start = content.find("{")
    return json.loads(content[start:content.rfind("}") + 1])


# --- generation: canned JSON -> a graph the validator admits whole -----------

def test_canned_reply_yields_a_plan_validate_plan_admits(endpoint_url):
    p = _planner(endpoint_url)
    assert isinstance(p, TaskPlanner)
    plan = p.plan(_brief())
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert ok, msg
    planner = plan.pop("planner")
    assert json.dumps(plan, sort_keys=True) == json.dumps(GOOD, sort_keys=True)
    # provenance for task.plan: the prompt bytes the endpoint saw, by content id
    assert planner["provider"] == "plugins.model_endpoint:provider"
    assert planner["prompt_sha"] == protocol.content_id(_Server.requests[0]["messages"])
    # the prompt carried the SkillRecord projection, select-only
    sent = _payload(_Server.requests[0])
    assert [c["name"] for c in sent["skills"]] == ["pick", "stack"]
    assert sent["skills"][0] == {"name": "pick", "kind": "segment", "args": {"object": "str"},
                                 "requires": [], "ensures": []}
    assert sent["output_schema"] == protocol.VLM_OUTPUT_SCHEMA
    assert sent["oracles"] == list(ORACLES) and sent["budget"] == 3
    assert _Server.requests[0]["response_format"] == {"type": "json_object"}


def test_fenced_or_preambled_reply_still_parses(endpoint_url):
    _Server.replies = ["thinking...\n```json\n" + json.dumps(GOOD) + "\n```"]
    plan = _planner(endpoint_url).plan(_brief(seed=1))
    assert validate_plan(plan, CATALOGUE, ORACLES)[0]


# --- parse failure: one re-ask, then the rejectable graph --------------------

def test_double_parse_failure_returns_a_graph_the_validator_refuses(endpoint_url):
    _Server.replies = ["not json at all", "still } not { json"]
    plan = _planner(endpoint_url).plan(_brief(seed=2))
    ok, msg = validate_plan(plan, CATALOGUE, ORACLES)
    assert not ok and "nodes" in msg          # rides the invalid_plan channel
    assert "unparseable" in plan["goal"]      # the error surfaces in plan_built
    assert len(_Server.requests) == 2         # exactly one re-ask, no third try
    # the re-ask carries the parse error back to the model
    retry_msgs = _Server.requests[1]["messages"]
    assert "failed strict JSON parsing" in retry_msgs[-1]["content"]
    assert retry_msgs[-2]["content"] == "not json at all"


def test_bad_json_once_then_good_recovers_on_the_re_ask(endpoint_url):
    _Server.replies = ["garbage", json.dumps(GOOD)]
    plan = _planner(endpoint_url).plan(_brief(seed=3))
    assert validate_plan(plan, CATALOGUE, ORACLES)[0]
    assert len(_Server.requests) == 2


# --- frozen-graph cache: generate once per (task, seed), byte-identical ------

def test_same_task_seed_replays_byte_identical_without_reasking(endpoint_url):
    p = _planner(endpoint_url)
    first = p.plan(_brief(seed=7))
    calls = len(_Server.requests)
    again = p.plan(_brief(seed=7))
    assert json.dumps(first, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert len(_Server.requests) == calls     # frozen: the model was not re-asked
    # a FRESH provider in the same process still replays the frozen graph
    fresh = _planner(endpoint_url).plan(_brief(seed=7))
    assert json.dumps(fresh, sort_keys=True) == json.dumps(first, sort_keys=True)
    assert len(_Server.requests) == calls
    # a different seed is a fresh generation
    p.plan(_brief(seed=8))
    assert len(_Server.requests) == calls + 1


def test_a_replan_fault_generates_fresh_and_echoes_done_nodes(endpoint_url):
    p = _planner(endpoint_url)
    p.plan(_brief(seed=9))
    calls = len(_Server.requests)
    fault = {"kind": "node_failure", "node": "pick-milk",
             "nodes_done": ["pick-can"], "nodes_left": ["pick-milk"]}
    p.plan(_brief(seed=9, fault=fault))
    assert len(_Server.requests) == calls + 1  # fault in the key -> not frozen
    sent = _payload(_Server.requests[-1])
    assert sent["fault"]["node"] == "pick-milk"
    # the replan-stability contract, handed to the model verbatim
    assert sent["completed_nodes"] == [
        {"id": "pick-can", "skill": "pick", "args": {"object": "can"}}]


# --- plugin_doctor: exemption, graceful skip, and the committed card ---------

def _card(tmp_path, base_url: str):
    d = tmp_path / "vlm_card"
    d.mkdir()
    (d / "manifest.toml").write_text(
        '[mounts."task.planner"]\n'
        'ref = "plugins.planner_vlm:provider"\n'
        f'params = {{ endpoint_params = {{ base_url = "{base_url}" }} }}\n')
    return d


def test_doctor_exempts_the_vlm_planner_from_the_determinism_diff(tmp_path, endpoint_url):
    rep = check(_card(tmp_path, endpoint_url))
    assert rep.green, [(r.tier, r.name, r.detail) for r in rep.results
                       if r.status == "FAIL"]
    b = [r for r in rep.results if r.tier == "B" and r.name == "task.planner"]
    assert b and b[0].status == "PASS"
    assert "shape validated, not diffed" in b[0].detail


def test_doctor_skips_when_the_endpoint_is_down(tmp_path):
    rep = check(_card(tmp_path, _DEAD))
    assert rep.green                          # a SKIP is never a red
    b = [r for r in rep.results if r.tier == "B" and r.name == "task.planner"]
    assert b and b[0].status == "SKIP" and "unreachable" in b[0].detail


def test_committed_card_binding_passes_tier_a_and_folds_cleanly():
    rep = check("plugins/planner_vlm")
    a = [r for r in rep.results if r.name == "task:stack_vlm"]
    assert a and a[0].status == "PASS", [(r.name, r.detail) for r in rep.results]
    reg = discover()                          # loud on any collision
    assert reg.task_bindings["stack_vlm"]["planner"] == "plugins.planner_vlm:provider"
    # the A/B channel shares stack's policy verbatim; stack itself is unmoved
    assert reg.task_bindings["stack_vlm"]["policy"] == \
        reg.task_bindings["stack"]["policy"]
    assert reg.task_bindings["stack"]["planner"] == "plugins.task.planner_stack:provider"
    # the card-owned catalogue offers ONLY what the binding's policy can drive
    assert set(pv.CATALOGUE) == {"stack"} and pv.ORACLES == ("stack_success",)


# --- SkillRecord projection + the fake endpoint (GPU-less VLM path) ---------

_FACTS = ("reachable(cubeA)", "gripper_free()", "stable_support(cubeB)")
_OBJECTS = ("cubeA", "cubeB")
#: A graph legal against the REAL grasp/place_on records: place_on requires
#: holding(cubeA) which only grasp ensures -- Supported is not vacuous here.
LEGAL = {
    "goal": "put cubeA on cubeB",
    "nodes": [
        {"id": "g", "skill": "grasp", "args": {"object": "cubeA"}, "after": []},
        {"id": "p", "skill": "place_on", "args": {"object": "cubeA", "target": "cubeB"},
         "after": ["g"]},
    ],
    "verify": [{"after": "g", "predicate": "stack_success"},
               {"after": "p", "predicate": "stack_success"}],
    "rationale": "grasp ensures holding(cubeA); place_on needs it.",
}


def _records():
    return {k: RECORDS[k] for k in ("grasp", "place_on")}


def test_projection_is_deterministic_and_plain():
    a = protocol.vlm_projection(_records(), _FACTS, _OBJECTS, ["g"], {"node": "p"})
    b = protocol.vlm_projection({k: protocol.to_plain(v) for k, v in reversed(_records().items())},
                                reversed(_FACTS), reversed(_OBJECTS), ("g",), {"node": "p"})
    assert protocol.content_id(a) == protocol.content_id(b)
    assert list(a) == ["skills", "facts", "objects", "done", "fault", "output_schema"]
    assert a["skills"][0]["name"] == "grasp" and "evidence" not in a["skills"][0]
    assert a["facts"][0] == "gripper_free()"
    shown = protocol.vlm_projection(_records(), (), (), (), None, show_evidence=True)
    assert shown["skills"][0]["evidence"] == {}       # no evidence rows yet
    assert protocol.content_id(shown) != protocol.content_id(a)
    assert protocol.evidence_interval(protocol.Evidence(n=0, k=0)) == [0.0, 1.0]
    lo, hi = protocol.evidence_interval(protocol.Evidence(n=20, k=18))
    assert 0.68 < lo < 0.9 < hi < 1.0


def _fake_planner(tmp_path, reply: str) -> pv.VlmPlanner:
    f = tmp_path / "reply.json"
    f.write_text(reply)
    return provider(endpoint="plugins.model_endpoint:fake_provider",
                    endpoint_params={"path": str(f)})


def _record_brief(seed: int) -> dict:
    return {"task": "stack_vlm", "seed": seed, "scene": {}, "budget": 3,
            "catalogue": {"grasp": {"object": str},
                          "place_on": {"object": str, "target": str}},
            "oracles": ("stack_success",),
            "records": {k: protocol.to_plain(v) for k, v in _records().items()},
            "facts": _FACTS, "objects": _OBJECTS}


def test_fake_endpoint_round_trips_a_legal_graph_through_plan(tmp_path):
    p = _fake_planner(tmp_path, json.dumps(LEGAL))
    assert p.available()
    brief = _record_brief(seed=41)
    plan = p.plan(brief)
    assert plan["planner"]["provider"] == "plugins.model_endpoint:fake_provider"
    assert len(plan["planner"]["prompt_sha"]) == 64
    assert plan["rationale"] == LEGAL["rationale"]
    assert validate_plan(plan, brief["catalogue"], brief["oracles"])[0]
    graph = {"mission": plan["goal"], "seed": 41, "tasks": [{"id": "t0", "goal": []}],
             "nodes": [{**n, "task": "t0"} for n in plan["nodes"]]}
    ok, problems = protocol.validate_graph(graph, _records(), _FACTS, _OBJECTS)
    assert ok, problems
    # the same graph with the supporter dropped is refused: Supported was live
    graph["nodes"][1]["after"] = []
    ok, problems = protocol.validate_graph(graph, _records(), _FACTS, _OBJECTS)
    assert not ok and any("supported" in m for m in problems)
    # a same-process replay is frozen: same bytes, prompt_sha included
    assert p.plan(brief) == plan


def test_fake_endpoint_env_var_selects_the_reply(tmp_path, monkeypatch):
    f = tmp_path / "canned.json"
    f.write_text(json.dumps(LEGAL))
    monkeypatch.setenv("PH_MODEL_ENDPOINT_FAKE", str(f))
    p = provider(endpoint="plugins.model_endpoint:fake_provider", endpoint_params={})
    assert validate_plan(p.plan(_record_brief(seed=42)), {"grasp": {"object": str},
                         "place_on": {"object": str, "target": str}}, ("stack_success",))[0]


def test_fake_endpoint_garbage_reply_yields_a_graph_the_validator_refuses(tmp_path):
    p = _fake_planner(tmp_path, "sorry, I cannot plan that {")
    plan = p.plan(_record_brief(seed=43))
    ok, msg = validate_plan(plan, {"grasp": {"object": str}}, ("stack_success",))
    assert not ok and "nodes" in msg and "unparseable" in plan["goal"]
    assert plan["planner"]["prompt_sha"]
