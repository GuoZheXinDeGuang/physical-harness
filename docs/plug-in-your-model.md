# Plug in your model

Three seams take an external model, all already built and tested on this
branch. Each is a manifest edit plus one verification command — no kernel
change, ever. The closed list of plug points is ARCHITECTURE.md §3.1; the
design rationale is [vlm-graph-paper-plan.md](vlm-graph-paper-plan.md).

| You have | Seam | Recipe |
|---|---|---|
| a VLM that writes task plans | `task.planner` via a `[task_bindings.*]` planner ref | §1 |
| a VLA that outputs actions | `policy.driver` over a websocket policy server | §2 |
| embodiment-specific repair motions | `[recoveries.*]` in your embodiment card | §3 |

## 1. Swap the planner for your VLM

A brief's task string resolves to one `[task_bindings.<task>]` row in some
card's manifest; the `planner` ref there is mounted as `task.planner` for that
run. The live example is `plugins/planner_vlm/manifest.toml`:

```toml
[task_bindings.stack_vlm]
policy    = "plugins.policies:stack_scripted_provider"   # SAME policy as stack
planner   = "plugins.planner_vlm:provider"               # only the planner swapped
catalogue = "plugins.planner_vlm:CATALOGUE"              # card-authored vocabulary
oracles   = "plugins.planner_vlm:ORACLES"                # declared verify predicates
```

That is the whole A/B channel: `{"task": "stack"}` runs the deterministic
planner, `{"task": "stack_vlm"}` runs the VLM, same policy, same everything
else. Submit both through `submit_brief` (scratch seeds 42xxxx never burn the
ledger):

```json
{"kind": "task", "task": "stack_vlm", "seed": 424242}
```

### Point it at your model

The planner talks to the model through the one `ModelEndpoint` seam — any
OpenAI-compatible `/v1/chat/completions` server. Two presets live in
`plugins/model_endpoint/__init__.py:PRESETS`:

- `local_sglang` — `http://127.0.0.1:30000/v1`, key env `QWEN38_API_KEY`
  (optional), `model = None` resolved lazily from `GET /models`. **Any**
  OpenAI-compatible serving on that port (sglang, vLLM, llama.cpp) works with
  zero edits.
- `deepseek` — `https://api.deepseek.com/v1`, `model = deepseek-chat`;
  `export DEEPSEEK_API_KEY=...`. The key rides an env var *name*, never a
  value — secrets stay out of the hash chain.

One gotcha, verified in code: the runtime's task path mounts the binding's
planner ref **with no params**, and the planner resolves the endpoint by ref
with its *own* defaults (`endpoint_params = {"preset": "local_sglang"}` in
`plugins/planner_vlm/__init__.py`). Editing `plugins/model_endpoint/manifest.toml`
params does **not** reroute the planner — those params only bite if something
kernel-mounts `model.endpoint`, and nothing does yet. To use a hosted API or a
different port, change the planner's `endpoint_params` default (one line;
per-field overrides like `{"preset": "local_sglang", "base_url": "http://host:8001/v1"}`
are supported). Endpoint identity enters the plan sha: a different endpoint is
a different experiment.

### What your VLM may and may not do

The provider prompts with (goal, catalogue, oracles, scene, budget, last
fault) and demands one strict-JSON graph `{goal, nodes[], verify[]}`. It is
untrusted by construction — `plugins/task/validate.py:validate_plan` runs on
every graph before dispatch and refuses: unknown skills/args/arg types
(planners select from the card-authored catalogue, never invent), non-earlier
`after` edges, empty `nodes`, empty or unknown-predicate `verify`, any
manipulate/segment node not covered by a verify, and any replan that drops or
rewrites a completed node. A refused graph costs one replan (the
`invalid_plan` fold-back), never a crash; an unparseable reply gets exactly
one re-ask carrying the parse error, then returns an empty-nodes graph the
validator is guaranteed to refuse — the card never silently invents a plan.

`deterministic = False` on the provider is the loud opt-in to plugin_doctor's
exemption: an LLM planner is shape-validated, never double-run-diffed. What it
still promises is generate-once-then-frozen — the first graph for an
(endpoint, task, seed, fault) key is cached for the process lifetime, so a
same-process replay mounts the byte-same graph.

### Register a parallel binding for A/B

Copy the `planner_vlm` card shape into your own card dir (cards never import
each other — refs only): `plugins/planner_yourvlm/{manifest.toml,__init__.py}`
with a `[task_bindings.stack_yourvlm]` naming your provider. Binding names are
unioned across cards and duplicates fail loud. Keep the catalogue restricted
to skills the bound policy can actually drive — offering the live model a
skill its channel cannot execute is a measured failure mode, not a
hypothetical.

Verify:

```bash
PYTHONPATH=. .venv/bin/python scripts/plugin_doctor.py plugins/planner_vlm
```

(Tier A loads every binding ref through the real contract gates; the planner
smoke probes `available()` and SKIPs loudly when no endpoint answers. The
offline logic is covered by `python -m pytest tests/test_planner_vlm.py
tests/test_model_endpoint.py` — no endpoint needed.)

## 2. Serve your VLA behind the socket

`plugins/policy_vla_remote/` is a `policy.driver` card that speaks the
StarVLA/openpi websocket policy-server protocol: first frame after connect is
the server's metadata dict, then msgpack-packed dicts whose ndarrays ride the
`__ndarray__` extension (pickle refused by construction). The VLA stack —
torch, flash-attn, pinned transformers — stays in **its own venv/process**
behind the socket. This is not optional hygiene: the harness base is
numpy>=1.26 with no torch, and the sim venvs already cannot share ABIs
(venv-per-sim); the socket is the same isolation move applied to the policy
seam. Harness-side transport is three vendored MIT files plus:

```bash
uv pip install -e ".[policy_remote]"    # websockets + msgpack only
```

**openpi works as-is**: `serve_policy.py` from our openpi checkout speaks this
exact protocol (default port 8000, which is also the provider's default —
`host="127.0.0.1", port=8000`).

### The handshake is a contract check

The card's manifest params declare the TRAINING observation contract; at
connect, `reconcile()` checks them against the server's first-frame metadata:

| manifest param | handshake key echoed by the server |
|---|---|
| `image_size` | `training_obs_image_size` |
| `views` | `camera_views` (StarVLA never echoes it — lands in `unverified`) |
| `chunk` | `action_chunk_size` |
| `unnorm_key` | `default_unnorm_key` (also OK if listed in `available_unnorm_keys`) |

A mismatch on any echoed key **raises at mount** — train/test drift fails
loud, never as a silently lower success rate. Keys the server does not echo
land in `handshake["unverified"]` (openpi servers often send empty metadata —
legal, but the gate can then verify nothing), and the whole handshake record
rides the driver into episode evidence. The values in the committed manifest
are templates for the openpi LIBERO π0.5 convention — set them per checkpoint.

### Wrapping your own model

Reuse the vendored server in your model venv (it imports only
websockets + msgpack + numpy):

```python
from plugins.policy_vla_remote.websocket_policy_server import WebsocketPolicyServer

WebsocketPolicyServer(
    policy=my_policy,          # any object with predict_action(**obs) -> {"actions": ...}
    host="0.0.0.0", port=8000,
    metadata={"training_obs_image_size": [224, 224], "action_chunk_size": 10,
              "default_unnorm_key": "my_dataset"},   # echo the table above
).serve_forever()
```

`actions` must be `[T, D]` (or `[B, T, D]`; first batch element is taken) and
**already un-normalized** — norm stats never cross the boundary; they stay
with the checkpoint on the server side. The driver runs one inference per
chunk and pops one action per step.

The card ships `enabled = false` because `plugins/policies` owns
`policy.driver` (one card per seam); flip it on and disable the incumbent when
a lane goes live. The end-to-end lane (LIBERO embodiment + π0.5 episodes,
paper plan §5.6–7) is not wired on this branch yet — what is verified today is
the transport, the handshake gate, and the driver.

Verify (with your server running):

```bash
PYTHONPATH=. .venv/bin/python -c "from plugins.policy_vla_remote import provider; \
print(provider(image_size=[224, 224], chunk=10).connect())"
```

(Prints the sealed handshake record, or raises with the full server metadata
on a contract mismatch. Offline: `python -m pytest tests/test_policy_vla_remote.py`
runs the codec, gate, driver, and a live in-process socket round trip;
`scripts/plugin_doctor.py plugins/policy_vla_remote` SKIPs loudly when nothing
listens.)

## 3. Register recovery primitives

RSI repairs are embodiment vocabulary, so they are declared by the embodiment
card that speaks it — `[recoveries.<name>] ref = "module:attr"` in your card's
manifest.toml, folded by `harness.manifest.discover()` exactly like mounts
(duplicate names fail loud). `plugins/embodiment_robosuite/manifest.toml` and
its `recoveries.py` are the template:

```toml
[recoveries.regrasp]
ref = "plugins.embodiment_robosuite.recoveries:REGRASP"
```

Each ref must satisfy `harness.contracts.RecoveryStrategy` — a frozen
dataclass works:

```python
name: str                                          # MUST equal the [recoveries.<name>] key
steps: tuple[tuple[str, int, float, float], ...]   # (phase, duration, dx, dy)
rationale: str
length: int          # property: step-duration upper bound
uses_feedback: bool  # property: any servo_* phase present
```

`plugins/rsi/repertoire.py` resolves every ref at load and isinstance-checks
it against the Protocol — a wrong shape or a name/key mismatch fails there,
never mid-repair. `steps` phases are your card's own vocabulary (robosuite's
above/descend/close/lift/… come from `harness/spec_tabletop.py`); a strategy
is never borrowed across embodiments.

**No declarations = an honest refusal, not a fallback.** A card with no
`[recoveries.*]` answers `strategies_for(card) == []`, and the RSI chain's
`c5_recovery_primitive` gate reports verbatim that this embodiment has no
registered recovery primitive — pointing at the robosuite servo primitives as
templates, never improvising an action to fill the gap. (A `hasattr` probe was
rejected on evidence: the robocasa kitchen driver defines `retarget` /
`on_handback` as documented no-ops, so method presence is not primitive
presence.)

Verify:

```bash
PYTHONPATH=. .venv/bin/python -c "from plugins.rsi import repertoire; \
print(repertoire.strategies_for('embodiment_yourcard'))"
```

(Runs the fold plus the isinstance and name gates over every declared
recovery; your card's names print, `[]` means nothing registered.)
