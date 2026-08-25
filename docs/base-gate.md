# base gate (双层测试)

The **base fast lane** is `pytest -m "not robosuite"` run **ISOLATED** — a fresh
process on a machine where robosuite is genuinely unimportable (the
`embodiment_robosuite` extra not installed, or the import blocked). Never a
subset of a full green run: a full run imports the card, and the
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

## current snapshot (2026-08-25, isolated, robosuite blocked)

```
pass       : 490 passed
skips      : 6 skipped
             [2] test_grasp_geometric.py:141  camera env unavailable
             [1] test_grasp_geometry.py:231   camera env unavailable
             [1] test_reducers.py:171         cloned weights not present
             [2] test_rsi_workload.py:592,609 runs/campaign-pj-scripted not present
wall time  : ~4.2s
AST green  : 17 passed (test_boundaries + test_kernel)
deselected : 28 robosuite-marked items
```

Full-suite parity (card present): `521 passed, 3 skipped`. base_profile sha is
byte-stable at `b905a5…` (folds to the value sealed in runs/round25-rerun) — the
manifest fold reproduces the old hard-coded mounts.

**Discipline: a commit that adds or removes tests refreshes this snapshot + the
two README counts IN THE SAME COMMIT.**

## fresh-clone variance

The snapshot above is defined on a checkout WITH the sealed `runs/` evidence
(not in git). A fresh clone legitimately shows MORE skips, never failures:

- +2 `test_plugin_doctor.py` verify-claim tests skip (sealed stores absent)
- +2 more skips where tests read sealed rescore/campaign artifacts
- the two 30-秒上手 commands in README work as written: the `dev` extra carries
  everything collection needs (including `mcp` for the both-faces tests)

A fresh clone that shows a FAILURE (not a skip) is a real regression.
