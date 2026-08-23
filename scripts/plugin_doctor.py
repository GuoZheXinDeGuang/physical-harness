#!/usr/bin/env python3
"""plugin_doctor: a card's 体检 (health check), mode-agnostic (GOAL v4.1 W2).

    PYTHONPATH=. .venv/bin/python scripts/plugin_doctor.py plugins/embodiment_robosuite

Two tiers, both cloned from machinery that already exists so the doctor invents
no new checking:

- Tier A (mount-time shape): parse the manifest, refuse ``actuation:real``, and
  for every declared mount ``load_provider`` it and mount it on a
  ``Kernel(CAPABILITIES)`` -- ``Kernel.provide`` IS the ``isinstance`` gate
  against the capability's declared contract, so a wrong-shaped provider fails
  here, exactly where the runtime would fail it.

- Tier B (one canonical fake-episode smoke per capability KIND): a kind->smoke
  dispatch table runs each mounted provider through its contract method(s) on a
  canonical episode. ``harness.fakes`` stands in for everything the card does not
  itself provide (an obs when the card mounts no env, say). Per-kind determinism
  policy: a percept/planner STAND-IN must be a pure function of its inputs
  (``required`` -- a sealed replay can't reproduce otherwise), while an LLM
  reasoner is validated for SHAPE only (``untrusted`` -- qwen is non-deterministic
  by nature; run the smoke once, never diff it).

``needs_sim`` gates the real-sim tier: a card that needs the simulator (its
third-party deps unimportable here) has its Tier A/B skipped, not failed -- the
base machine simply can't run its sim smokes. Everything else runs on the base
tier (fakes + determinism), so 体检 splits base/plugin the way W6 双层测试 asks.

Base-clean: imports only ``harness.*`` + stdlib at module load; a card's
providers (and any simulator they drag in) are imported lazily via
``load_provider`` at check time, so importing the doctor never imports a card.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tomllib import loads as _toml
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.definitions import CAPABILITIES
from harness.kernel import Kernel
from harness.manifest import card_mounts
from harness.registry import load_provider
from harness.spec import EpisodeSpec


class DoctorSkip(Exception):
    """A smoke that cannot run here but is not a failure -- reported SKIP, loudly.

    The qwen reasoner card raises this when its endpoint is unreachable (GPU
    held): Tier A still validated the shape, and the untrusted-determinism policy
    never diffs an LLM anyway, so an absent model is a skip, not a red -- exactly
    how scripts/round25_rerun degrades its qwen arm.
    """


#: Determinism policy per capability KIND. Absent = shape-only (run once).
_DETERMINISM = {
    "percept.model": "required",       # a percept stand-in must be pure in (seed, draw)
    "task.planner": "required",        # a plan is a symbol graph, same brief -> same graph
    "reasoner.proposer": "untrusted",  # an LLM reasoner: validate the shape, never diff it
}


# ── the canonical episode the smokes run against ────────────────────────────

@dataclass(frozen=True)
class _Ctx:
    spec: object
    obs: Mapping
    brief: dict


def _build_ctx(providers: dict, needs_sim: bool) -> _Ctx:
    """One episode's stand-in inputs: a real EpisodeSpec + the card's own env obs
    when it needs the sim, else a fake spec + the null env's canned obs. The obs
    is sourced from the mounted env if the card declares one (so a real percept
    reads the real key), otherwise from ``harness.fakes`` -- the doctor's
    stand-in for what the card does not provide."""
    if needs_sim:
        spec: object = EpisodeSpec(seed=90001, task="lift")
    else:
        spec = SimpleNamespace(seed=90001, task="fake", horizon=5)
    env = providers.get("embodiment.env")
    if env is not None:
        handle = env.make_env(spec)
        obs = handle.reset()
        getattr(handle, "close", lambda: None)()
    else:
        obs = {"object": (0.0, 0.0, 0.0)}
    brief = {"task": getattr(spec, "task", "fake"), "scene": {}, "catalogue": []}
    return _Ctx(spec, obs, brief)


# ── per-kind smokes (cloned from the plugins' own self-checks) ───────────────
# Each returns a JSON-comparable value so a determinism-required kind can be
# diffed across two runs; shape assertions live inside.

def _smoke_env(prov, ctx: _Ctx):
    assert isinstance(prov.tasks(), tuple), "tasks() must return a tuple"
    assert isinstance(prov.object_key(ctx.spec), str), "object_key must return a str"
    assert isinstance(ctx.obs, Mapping) and ctx.obs, "make_env().reset() gave no obs"
    return "ok"


def _smoke_policy(prov, ctx: _Ctx):
    action = tuple(prov.make_driver(ctx.spec).act(ctx.obs))
    assert len(action) > 0, "driver.act returned an empty action"
    return "ok"


def _smoke_percept(prov, ctx: _Ctx):
    est = prov.object_estimate(ctx.obs, ctx.spec, 0.02, 0)
    return [float(v) for v in list(est)]


def _smoke_planner(prov, ctx: _Ctx):
    plan = prov.plan(ctx.brief)
    assert plan.get("goal") is not None and plan.get("nodes"), "plan lacks goal/nodes"
    return json.dumps(plan, sort_keys=True, default=str)


def _smoke_reasoner(prov, ctx: _Ctx):
    # A model-backed reasoner exposes `available()` (probes its endpoint); when it
    # is down, skip loudly rather than red -- the shape passed Tier A and the
    # untrusted policy never exercises the live proposal as a gate anyway.
    probe = getattr(prov, "available", None)
    if probe is not None and not probe():
        raise DoctorSkip("reasoner endpoint unreachable -- probed and skipped "
                         "(untrusted: shape validated Tier A, live proposal not run)")
    out = prov.propose(ctx.brief)
    assert isinstance(out, Mapping), "propose must return a Mapping"
    return "ok"  # untrusted: shape only, never diffed


def _smoke_skill(prov, ctx: _Ctx):
    rec = {"kind": "doctor_probe", "value": 1}
    digest = prov.publish(rec)
    assert digest == prov.publish(rec), "publish is not content-addressed (idempotent)"
    assert isinstance(prov.skills(), tuple), "skills() must return a tuple"
    return "ok"


def _smoke_scene(prov, ctx: _Ctx):
    snap = prov.snapshot(ctx.obs)
    assert isinstance(snap, Mapping) and ("nodes" in snap or "objects" in snap), \
        "snapshot must return a scene mapping"
    return "ok"


_SMOKES = {
    "embodiment.env": _smoke_env,
    "policy.driver": _smoke_policy,
    "percept.model": _smoke_percept,
    "task.planner": _smoke_planner,
    "reasoner.proposer": _smoke_reasoner,
    "graph.skill": _smoke_skill,
    "graph.scene": _smoke_scene,
}


# ── report ──────────────────────────────────────────────────────────────────

@dataclass
class Result:
    tier: str      # "A" | "B"
    name: str
    status: str    # "PASS" | "FAIL" | "SKIP"
    detail: str = ""


@dataclass
class Report:
    plugin: str
    results: list = field(default_factory=list)

    @property
    def green(self) -> bool:
        return all(r.status != "FAIL" for r in self.results)

    def add(self, tier: str, name: str, status: str, detail: str = "") -> None:
        self.results.append(Result(tier, name, status, detail))


def check(plugin_dir: str | Path) -> Report:
    """Run Tier A + Tier B on one card directory and return its report card."""
    plugin_dir = Path(plugin_dir)
    rep = Report(plugin_dir.name)
    data = _toml((plugin_dir / "manifest.toml").read_text())

    if data.get("actuation", "sim") == "real":
        rep.add("A", "actuation", "FAIL",
                "actuation:real refused -- a real actuator needs a separate "
                "authenticated runtime, never the sim doctor")
        return rep

    needs_sim = bool(data.get("needs_sim", False))
    if needs_sim:
        missing = [r for r in data.get("third_party", ())
                   if importlib.util.find_spec(r.split(".")[0]) is None]
        if missing:
            rep.add("B", "needs_sim", "SKIP",
                    f"gated: simulator absent here ({missing}) -- run on a sim machine")
            return rep

    mounts = card_mounts(data)
    if not mounts:
        rep.add("A", "mounts", "SKIP", "task-only card: no capability mounts to check")
        return rep

    # Tier A: load + isinstance, reusing Kernel.provide's contract gate.
    kernel = Kernel(CAPABILITIES)
    providers: dict[str, object] = {}
    for m in mounts:
        try:
            prov = load_provider(m.provider, m.params)
            kernel.provide(m.capability, prov, ref=m.provider, params=dict(m.params))
        except Exception as exc:  # noqa: BLE001 -- any load/shape failure is a red
            rep.add("A", m.capability, "FAIL", f"{type(exc).__name__}: {exc}")
            continue
        providers[m.capability] = prov
        rep.add("A", m.capability, "PASS", m.provider)

    # Tier B: one canonical fake-episode smoke per mounted, conforming kind.
    ctx = _build_ctx(providers, needs_sim)
    for cap, prov in providers.items():
        smoke = _SMOKES.get(cap)
        if smoke is None:  # e.g. exec.rollouts / ground_truth: no episode smoke
            continue
        policy = _DETERMINISM.get(cap)
        try:
            out = smoke(prov, ctx)
            if policy == "required":
                again = smoke(prov, ctx)
                if out != again:
                    rep.add("B", cap, "FAIL",
                            f"non-deterministic (policy: required): {out!r} != {again!r}")
                    continue
            rep.add("B", cap, "PASS",
                    "deterministic" if policy == "required" else (policy or "shape"))
        except DoctorSkip as skip:
            rep.add("B", cap, "SKIP", str(skip))
        except Exception as exc:  # noqa: BLE001 -- a smoke that raises is a red
            rep.add("B", cap, "FAIL", f"{type(exc).__name__}: {exc}")
    return rep


def verify(store: str | Path) -> Report:
    """验货: read a published skill store and pass/fail the acceptance claim.

    The counterpart to 体检 (``check``): 体检 is mode-agnostic and asks "does this
    card mount and behave"; 验货 asks "did an evolution-mode campaign actually
    earn a promotable skill here" and is the execution-mode admission ticket
    (GOAL v4.1). No new statistics -- it only READS the ``SkillRecord``s the
    existing evidence machine published to ``store`` (a flat ``<digest>.json``
    graph.skill root -- ``rt.skills_root``, or an acceptance campaign's
    ``<out>/skills``) and folds their fields into four checks: a prereg hash is
    present, at least one skill was promoted (a record IS a promotion --
    ``workload.run`` publishes only promoted generations), the held-out judgement
    was established (``heldout_judgement_established`` True, i.e. p<alpha &
    fixed>broken on the held-out block), and the transfer ablation was run.
    """
    store = Path(store)
    rep = Report(f"{store.name} (验货)")
    records = [json.loads(f.read_text()) for f in sorted(store.glob("*.json"))]
    rep.add("V", "promoted skill", "PASS" if records else "FAIL",
            f"{len(records)} published skill record(s)")
    rep.add("V", "prereg_sha", "PASS" if records and all(r.get("prereg_sha") for r in records)
            else "FAIL", "content hash present on every record")
    established = [r for r in records if r.get("heldout_judgement_established") is True]
    rep.add("V", "heldout judgement", "PASS" if established else "FAIL",
            "established (p<alpha & fixed>broken on held-out)")
    ablation = [r for r in records if (r.get("bundle_evidence") or {}).get("ablation")]
    rep.add("V", "ablation", "PASS" if ablation else "FAIL", "transfer ablation curve present")
    return rep


def _fmt(rep: Report) -> str:
    lines = [f"plugin_doctor: {rep.plugin} -- {'GREEN' if rep.green else 'RED'}"]
    for r in rep.results:
        lines.append(f"  [{r.status:4}] tier {r.tier} {r.name}: {r.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plugin_dir", nargs="?",
                    help="the card directory to 体检 (holds manifest.toml)")
    ap.add_argument("--verify", metavar="STORE",
                    help="验货: pass/fail a published skill store instead of 体检 a card")
    args = ap.parse_args(argv)
    if args.verify is not None:
        rep = verify(args.verify)
    elif args.plugin_dir is not None:
        rep = check(args.plugin_dir)
    else:
        ap.error("give a card directory to 体检, or --verify STORE to 验货")
    print(_fmt(rep))
    return 0 if rep.green else 1


if __name__ == "__main__":
    sys.exit(main())
