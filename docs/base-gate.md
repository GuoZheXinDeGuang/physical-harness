# base gate (R3, W6 双层测试)

The **base fast lane** is `pytest -m "not robosuite"` run **ISOLATED** — a fresh
process on a machine where robosuite is genuinely unimportable (the
`embodiment_robosuite` extra not installed, or the import blocked). Never a
subset of a full green run: a full run imports the card, and the round-69
collection-order pollution only stays fixed if the card is truly absent.

The merge gate: capture this snapshot before AND after any base-touching change;
a regression in any field blocks the merge. The eval battery is the RSI-scoring
gate, NOT the base lane.

## snapshot format

```
pass       : <N> passed
skips      : <M> skipped, each with its reason
wall time  : <T>s
AST green  : test_boundaries + test_kernel green (harness-imports-nothing +
             profiles-declarative)
```

## how to run isolated

Any process where `importlib.util.find_spec("robosuite") is None` triggers the
`tests/conftest.py` collection hook to auto-skip the `robosuite`-marked items.
Two ways:

- **venv without the extra**: `pip install -e .[dev]` (base deps only, no
  `embodiment_robosuite`), then `pytest -m "not robosuite"`.
- **blocked import** (in a fresh process): a `sitecustomize.py` that does
  `import sys; sys.modules["robosuite"] = sys.modules["mujoco"] = None`, on
  `PYTHONPATH`, then `pytest -m "not robosuite"`.

## R3 snapshot (2026-08-23, isolated, robosuite blocked)

```
pass       : 350 passed
skips      : 6 skipped
             [2] test_grasp_geometric.py:141  camera env unavailable
             [1] test_grasp_geometry.py:231   camera env unavailable
             [1] test_reducers.py:171         cloned weights not present
             [2] test_rsi_workload.py:584,601 runs/campaign-pj-scripted not present
wall time  : ~3.4s
AST green  : 17 passed (test_boundaries + test_kernel), card present and absent
deselected : 25 robosuite-marked items (self-skip under `-m robosuite`)
```

Full-suite parity (card present): `378 passed, 3 skipped` — unchanged by R3, the
marker is inert when robosuite imports.

## R5 snapshot (2026-08-23, isolated, robosuite blocked)

```
pass       : 363 passed  (+8 tests/test_manifest.py over R4's 355)
skips      : 6 skipped   (same reasons as R3)
wall time  : ~3.4s
AST green  : test_boundaries (third_party now READ from manifests, card-absent) +
             test_kernel green; discover() parses TOML, imports no card
deselected : 25 robosuite-marked items
```

Full-suite parity (card present): `391 passed, 3 skipped` (+8 over R4). base_profile
sha is byte-stable at `b905a5…` (still folds to the value sealed in
runs/round25-rerun) — the manifest fold reproduces the old hard-coded mounts.
