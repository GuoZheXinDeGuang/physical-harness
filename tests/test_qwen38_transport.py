"""The real-endpoint transports, exercised offline against a stub http.server.

Round 81 (M1): three properties that must hold before qwen38 ever answers --
model content round-trips through parse_proposal, a network fault raises
rather than masquerading as a refusal, and the audit hook records every
exchange including the ones validation rejects.
"""
import http.server
import json
import socket
import threading

import pytest

from governor.proposer import (
    TransportError,
    naive_transport,
    parse_proposal,
    qwen38_transport,
)
from plugins.rsi.repertoire import names as _strategy_names

STRATEGIES = tuple(_strategy_names())

VALID = {
    "feature": "observable.finger_gap", "op": "lt", "threshold": 0.005,
    "dwell": 1, "arm_after": 58, "reducer": "min", "recovery": "regrasp",
}


def _parse(raw):
    return parse_proposal(raw, generation=1, privilege_budget=0,
                          strategies=STRATEGIES,
                          recovery_sensor_sd=0.02, n_steps=100)


class _Stub(http.server.BaseHTTPRequestHandler):
    """OpenAI-shaped sglang stand-in: path-shaped model id, canned content."""

    #: launch_qwen38.sh sets no --served-model-name, so the published id is a
    #: filesystem path; the transport must probe it, never hardcode a name.
    model_id = "/home/models/Qwen3.8-27B-abliterated-AWQ-MTP"
    content = json.dumps(VALID)

    def do_GET(self):
        self._send({"data": [{"id": self.model_id}]})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self._send({"choices": [{"message": {"content": type(self).content}}]})

    def _send(self, payload):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass  # keep pytest output clean


@pytest.fixture
def stub_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_stub_content_round_trips_into_a_rule(stub_url):
    calls = []
    transport = qwen38_transport(base_url=stub_url, record=calls.append)
    rule = _parse(transport({"response_schema": {}, "attempt": 0}))
    assert rule.trigger.feature == "observable.finger_gap"
    assert len(calls) == 1
    # The id came from the /models probe, not a hardcoded friendly name.
    assert calls[0]["model"] == _Stub.model_id


def test_closed_port_raises_transport_error_not_a_string():
    # Bind-then-close guarantees a refused port; a fault must never surface as
    # a returned string parse_proposal would count against the model.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    transport = qwen38_transport(base_url=f"http://127.0.0.1:{port}/v1",
                                 model="m", timeout=1.0)
    with pytest.raises(TransportError):
        transport({"response_schema": {}, "attempt": 0})


def test_record_fires_on_every_call_including_rejected_ones(stub_url):
    calls = []
    transport = qwen38_transport(base_url=stub_url, record=calls.append)
    _Stub.content = "no json here, just prose"
    try:
        for attempt in range(2):  # LlmProposer's retry-after-refusal shape
            raw = transport({"response_schema": {}, "attempt": attempt})
            with pytest.raises(Exception, match="not valid JSON"):
                _parse(raw)
    finally:
        _Stub.content = json.dumps(VALID)
    assert [c["raw_response"] for c in calls] == ["no json here, just prose"] * 2
    assert [c["attempt"] for c in calls] == [0, 1]


def test_naive_transport_reads_the_brief_and_validates():
    calls = []
    transport = naive_transport(record=calls.append)
    brief = {"separations": [{"feature": "observable.finger_gap", "reducer": "min",
                              "peak_sigma": 9.0, "peak_step": 58}],
             "recovery_strategies": list(STRATEGIES), "attempt": 0}
    rule = _parse(transport(brief))
    assert rule.trigger.feature == "observable.finger_gap"
    assert rule.trigger.arm_after == 56  # two steps before the peak
    assert len(calls) == 1
