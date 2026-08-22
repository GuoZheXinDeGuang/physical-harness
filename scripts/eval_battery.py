#!/usr/bin/env python3
"""RUNG 6: the eval-battery smoke gate (R4 seed).

Three battery items, one pass/fail exit, one content-addressed result artifact.
There is NO paired before/after architecture-change comparison here -- that
meta-RSI 門禁 (with its own preregistration + evidence ledger) is deferred to
round 95, triggered by the first real architecture-change proposal. This rung
ships only the battery-as-smoke-gate, a CI reflex over what already exists.

    MUJOCO_GL=egl PYTHONPATH=. .venv/bin/python scripts/eval_battery.py
    (--no-soak runs only the two cheap reconstruction items; --out DIR keeps
     the result artifact instead of writing it to a fresh temp dir)

THE BATTERY (and, honestly, what each item does and does NOT re-verify):

  1. demo-parity      Reconstruct the sealed demo campaign's promoted bundle from
                      its generation chain and prove it reproduces the sealed
                      artifacts BYTE FOR BYTE: each generation's rule canonical,
                      re-grown through today's Bundle.append, must hash to the
                      sealed child_sha; the final bundle sha must equal the
                      campaign_result's final_sha; the archived preregistration
                      must still hash to the preregistration_sha it was gated
                      under; and the store's session chain must verify().
                      RE-VERIFIES: the reconstruction/parity path (parity_check +
                      rescore_heldout pure functions) and the sealed store's
                      internal content integrity, through the current
                      Bundle/Rule/Trigger/RecoverySpec code.
                      DOES NOT: re-run a single rollout, so it does NOT re-verify
                      that fresh simulation of that bundle still produces the
                      sealed dev/held-out RATES.

  2. stack-3block     The same reconstruction over the sealed stack-g1 campaign,
                      plus the three-held-out-block integrity cross-check the
                      headline discipline asks for: all THREE sealed held-out
                      blocks -- the campaign's own (block 42000, in
                      campaign_result) and the two sealed rescores (42200, 42400)
                      -- must reference the ONE reproduced bundle_sha and the ONE
                      sealed preregistration_sha.
                      RE-VERIFIES: that the three sealed blocks are all
                      reproductions of the same reconstructable bundle+prereg (a
                      rescore silently derived from a different bundle would be
                      caught here).
                      DOES NOT: re-score any block -- the sealed paired numbers
                      are trusted, not recomputed.

  3. soak             The live closed-loop M4#7 gate (scripts/soak.py): 50 briefs,
                      six fault classes, a mid-run kill+reboot, drained through
                      the REAL resident runtime with REAL MuJoCo rollouts. This is
                      the ONLY item that runs the simulator.
                      RE-VERIFIES: rungs 0+1 end to end -- process survives every
                      fault, one note per well-formed brief, malformed->failed/,
                      chain verify() True after restart.
                      DOES NOT: assert any task's SUCCESS RATE (soak only asserts
                      liveness + bookkeeping, not that stack still stacks).

SCALE NOTE. Items 1 and 2 are the CHEAP verification tier on purpose. A full
three-block stack REPRODUCTION by fresh rollout (parity_check.run_parity_check /
rescore_heldout.run_rescore / a fresh stack_campaign) is hours of simulation --
too heavy for a gate. So the gate reconstructs-and-compares against sealed
artifacts, which catches code drift away from those artifacts in milliseconds,
and leaves the fresh-rollout reproduction to a manual overnight RECORD. Only the
soak, already bounded at N=50, runs live.

The result artifact is one content-addressed ``eval_battery`` row in a
``CampaignStore`` (sha of the payload IS its filename) -- the same store shape
every campaign uses. It records each item's verdict, the reproduced shas, and
the soak's rc + runtime, so a battery run is a citable evidence blob.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
VENV_PY = REPO / ".venv" / "bin" / "python"
SOAK = REPO / "scripts" / "soak.py"

from harness.events import SessionLog
from plugins.rsi.campaign import CampaignStore, sha_json
from scripts.parity_check import read_store_artifacts, rebuild_preregistration
from scripts.rescore_heldout import rebuild_final_bundle

DEMO_STORE = REPO / "runs" / "demo"
STACK_STORE = REPO / "runs" / "stack-g1"
STACK_RESCORES = (REPO / "runs" / "stack-g1-rescore-42200",
                  REPO / "runs" / "stack-g1-rescore-42400")


def _reconstruct(store: Path) -> dict:
    """CHEAP tier: rebuild a sealed campaign's promoted bundle and prove it
    reproduces the sealed artifacts. NO rollouts.

    ``rebuild_final_bundle`` re-grows the promoted chain through the real
    ``Bundle.append`` and RAISES if any generation's rule canonical no longer
    hashes to its sealed ``child_sha`` -- that raise IS the drift detector. We
    then cross-check the final bundle sha against ``final_sha`` and the archived
    preregistration against the ``preregistration_sha`` it was gated under, and
    verify the store's session chain.
    """
    arch = read_store_artifacts(store)
    prereg_payload = arch["preregistration"][0]
    prereg = rebuild_preregistration(prereg_payload)
    bundle = rebuild_final_bundle(prereg, arch.get("generation", []))  # raises on child_sha drift
    bundle_sha = bundle.sha()
    prereg_sha = sha_json(prereg_payload)
    result = arch.get("campaign_result", [None])[0]
    checks = {
        "child_sha_reproduced": True,  # rebuild_final_bundle asserted it per generation
        "final_sha_matches": bool(result and result["final_sha"] == bundle_sha),
        "prereg_sha_consistent": bool(result and result["preregistration_sha"] == prereg_sha),
        "chain_ok": SessionLog.load(store / "session-log").verify(),
    }
    return {"store": str(store.relative_to(REPO)), "bundle_sha": bundle_sha,
            "prereg_sha": prereg_sha, "checks": checks, "ok": all(checks.values())}


def demo_parity() -> dict:
    return _reconstruct(DEMO_STORE)


def stack_three_block() -> dict:
    """stack-g1 reconstruction + the three-held-out-block trace cross-check."""
    base = _reconstruct(STACK_STORE)
    arch = read_store_artifacts(STACK_STORE)
    heldout = arch["campaign_result"][0].get("heldout") or {}
    blocks = [{"source": "campaign_result", "block": 42000, "n": heldout.get("n"),
               "bundle_sha_match": arch["campaign_result"][0]["final_sha"] == base["bundle_sha"],
               "prereg_sha_match": True}]
    for rs in STACK_RESCORES:
        pl = read_store_artifacts(rs)["heldout_rescore"][0]
        blocks.append({"source": rs.name, "block": pl["block"], "n": pl["n"],
                       "bundle_sha_match": pl["bundle_sha"] == base["bundle_sha"],
                       "prereg_sha_match": pl["source_preregistration_sha"] == base["prereg_sha"]})
    three_ok = len(blocks) == 3 and all(b["bundle_sha_match"] and b["prereg_sha_match"] for b in blocks)
    return {**base, "held_out_blocks": blocks, "three_blocks_trace_one_bundle": three_ok,
            "ok": base["ok"] and three_ok}


def run_soak(env: dict) -> dict:
    """LIVE tier: the M4#7 soak, unmodified, as a subprocess. Its exit code is the
    verdict; we keep the report tail as evidence in the artifact."""
    t0 = time.monotonic()
    proc = subprocess.run([str(VENV_PY), str(SOAK)], cwd=str(REPO), env=env,
                          capture_output=True, text=True, check=False)
    dt = round(time.monotonic() - t0, 1)
    out_tail = proc.stdout.strip().splitlines()[-14:]
    return {"rc": proc.returncode, "seconds": dt, "ok": proc.returncode == 0,
            "report_tail": out_tail,
            "stderr_tail": proc.stderr.strip().splitlines()[-8:] if proc.returncode else []}


def _print_item(name: str, item: dict) -> None:
    status = "PASS" if item["ok"] else "FAIL"
    print(f"[{status}] {name}", end="")
    if name == "soak" and "rc" in item:
        print(f"  rc={item['rc']} {item['seconds']}s")
        for line in item.get("report_tail", []):
            print(f"        {line}")
    elif name == "soak":
        print("  (skipped)")
    else:
        print(f"  bundle={item['bundle_sha'][:12]} checks={item['checks']}")
        for b in item.get("held_out_blocks", []):
            print(f"        block {b['block']} (n={b['n']}) via {b['source']}: "
                  f"bundle_match={b['bundle_sha_match']} prereg_match={b['prereg_sha_match']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=None,
                    help="dir for the content-addressed result artifact "
                         "(default: a fresh temp dir; never written into sealed runs/)")
    ap.add_argument("--no-soak", action="store_true",
                    help="run only the two cheap reconstruction items (no simulator)")
    args = ap.parse_args(argv)

    env = {**os.environ, "MUJOCO_GL": "egl", "PYTHONPATH": str(REPO)}

    items: dict[str, dict] = {}
    items["demo_parity"] = demo_parity()
    items["stack_three_block"] = stack_three_block()
    items["soak"] = ({"ok": True, "skipped": True} if args.no_soak else run_soak(env))

    verdict_ok = all(v["ok"] for v in items.values())
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False).stdout.strip()
    payload = {"battery": "m4-rung6-smoke", "head": head, "time": time.time(),
               "no_soak": args.no_soak, "verdict": "PASS" if verdict_ok else "FAIL",
               "items": items}

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="eval-battery-"))
    digest = CampaignStore(out).put("eval_battery", payload)

    print("\n==== EVAL BATTERY ====")
    for name in ("demo_parity", "stack_three_block", "soak"):
        _print_item(name, items[name])
    print(f"\nverdict   : {payload['verdict']}")
    print(f"artifact  : {out}/artifacts/{digest[:12]}... (kind=eval_battery)")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
