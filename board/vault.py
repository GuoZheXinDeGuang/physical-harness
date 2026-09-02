#!/usr/bin/env python3
"""The Skill Vault: a deterministic READ fold over shapes the harness already seals.

Not a store, not a writer, not a second authority. ``build_graph`` is a pure
function of the sealed tree -- SkillRecords (``runs/*/skills/*.json``), campaign
preregistrations (lineage), manifest cards (``plugins/*/manifest.toml`` via
board.cards), session chains (``runs/*/session-log`` via board.store) and the
fixed capability catalog (``harness.definitions.CAPABILITIES``) -- re-presented
as a typed, backlinked wiki graph. Re-fold an unchanged tree, get a byte-identical
graph (nodes sorted by ``(kind, id)``, edges by ``(rel, src, dst)``). It invents
no statistic: every number is verbatim from ``bundle_evidence``/``effects``; the
only derived scalar is ``board.store._delta`` (governed_rate - base_rate).

Five node kinds (skill / class / benchmark / package / capability), one edge
kind with a fixed 14-relation vocabulary (DESCENDS_FROM, GOVERNS, REQUIRES,
PROVIDES, BINDS, EVIDENCED_BY, CLAIMS, SUPERSEDES, MOUNTED_IN, IN_CLASS,
DEPENDS_ON, INSTANCE_OF, BOUND_TO, EVIDENCED_ON); each edge names its mechanical ``rule`` and
the ``via`` artifact it was read from, so an auditor re-derives it. Skill nodes
come from two doors: legacy promoted records (``runs/*/skills``, id = digest,
status from the store) and the static library (``skill-library/records``,
id = ``skill:<name>``, status ``library``); the symbolic half of a library record
(class / requires / ensures) is folded with harness.protocol, never a plugin.

Stays plugin-free like the rest of board/: it reads the sealed artifacts as data
via board.store (never imports plugins.rsi), so folding the chassis can never run
a plugin. The three agent faces (board/mcp_server.py, board/storecli.py, and the
fork @Remote bridge) are byte-thin passthroughs into build_graph/node/neighbors,
the same MCP-与-CLI-同一函数 discipline as the rest of the board.
"""

from __future__ import annotations

import json
from pathlib import Path

from board import cards as bc
from board import store as bs
from harness.definitions import CAPABILITIES
from harness.manifest import PLUGINS_ROOT
from harness.protocol import (SkillRecordV0, skill_benchmarks, skill_class, skill_dependencies,
                              skill_instances)

SCHEMA_VERSION = 1

#: Optional additive annotation sidecars (see vault_doctor); absent by default.
ANNOTATIONS_DIR = Path("docs/vault/annotations")
ANNOTATION_KEYS = {"note", "tags", "see_also"}

#: The static skill library (harness/skill_library.py loads it; here it is data).
LIBRARY_ROOT = Path(__file__).resolve().parent.parent / "skill-library" / "records"

#: REQUIRES target: the feature namespace IS the declaration (harness/features.py).
_FEATURE_CAP = {"privileged": "embodiment.ground_truth", "observable": "percept.model"}


# --- skill nodes -------------------------------------------------------------


def _skill_records(runs: Path):
    """Fold ``runs/*/skills/*.json`` into (records, evidenced, store_digests).

    ``records`` maps digest -> the record body (content-addressed, so identical
    across roots -- the sealed store copy and the live session mount are two
    facts about ONE node; dedupe by digest). ``evidenced`` maps digest -> the
    sealed store it lives in (EVIDENCED_BY; a session root is a mount, not
    evidence). ``store_digests`` maps store name -> its digests, for lineage.
    Mid-write/unreadable records are skipped, never fatal (board.store discipline).
    """
    records: dict[str, dict] = {}
    candidates: dict[str, list[str]] = {}
    store_digests: dict[str, list[str]] = {}
    for skills_dir in sorted(runs.glob("*/skills")):
        store_dir = skills_dir.parent
        is_store = bs.is_store(store_dir)
        for f in sorted(skills_dir.glob("*.json")):
            digest = f.stem
            try:
                rec = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            records.setdefault(digest, rec)
            if is_store:
                candidates.setdefault(digest, []).append(store_dir.name)
                store_digests.setdefault(store_dir.name, []).append(digest)
    # A digest can live in several stores (campaigns copy records in as seeds).
    # EVIDENCED_BY must point at the MINTING store, not a seed copy: a store
    # that sealed generation artifacts is an origin; seed-only copies (e.g. a
    # calibration store's skills/) have none. Sorted-first keeps it byte-stable.
    # ponytail: store-level minting test; go digest-level (match the generation
    # rows to the digest) if a generation-bearing store ever carries foreign
    # seed copies in its skills/.
    minting: dict[str, bool] = {}
    for name in sorted(store_digests):
        try:
            minting[name] = bool((bs.store_detail(str(runs / name)) or {}).get("generations"))
        except Exception:  # unreadable store: treat as non-minting, never fatal
            minting[name] = False
    evidenced: dict[str, str] = {}
    for digest, stores in candidates.items():
        minted = [s for s in stores if minting.get(s)]
        evidenced[digest] = sorted(minted)[0] if minted else sorted(stores)[0]
    return records, evidenced, store_digests


def _skill_node(digest: str, rec: dict, status: str, evidenced_by: str | None) -> dict:
    be = rec.get("bundle_evidence") or {}
    rec_rec = rec.get("recovery") or {}
    pre = rec.get("preconditions") or {}
    heldout = be.get("heldout") or {}
    return {
        "kind": "skill",
        "id": digest,
        "task": rec.get("task"),
        "skill_kind": rec.get("kind"),
        "generation": rec.get("generation"),
        "policy": rec.get("policy"),
        "label": f"{rec.get('task')} · {rec_rec.get('name')} (g{rec.get('generation')})",
        "trigger": pre,  # preconditions verbatim -- the affordance predicate
        "recovery": {"name": rec_rec.get("name"), "strategy": rec_rec.get("strategy"),
                     "steps": len(rec_rec.get("program") or []),
                     "max_invocations": rec_rec.get("max_invocations"),
                     "sensor_sd": rec_rec.get("sensor_sd")},
        "privilege": be.get("declared_privilege"),
        "evidence": {  # ALL verbatim; heldout_delta is the one derived scalar
            "heldout": heldout,
            "judgement": be.get("judgement"),
            "judgement_dev": rec.get("judgement_dev"),
            "dev_gate": (rec.get("effects") or {}).get("dev_gate_vs_parent"),
            "ablation": be.get("ablation"),
            "heldout_delta": bs._delta(heldout),
        },
        "heldout_judgement_established": rec.get("heldout_judgement_established"),
        "status": status,
        "anchors": {k: rec.get(k) for k in ("bundle_sha", "prereg_sha", "mount_plan_sha")},
        "evidenced_by": evidenced_by,
        "annotations": None,
    }


# --- library skill / class / benchmark nodes --------------------------------


def _library_records(library):
    """``skill-library/records/*.json`` as data: (name -> SkillRecordV0, plan-kind
    raw dicts). Unreadable/unparsable files are skipped, never fatal (the loud
    loader is harness.skill_library; plans feed skill_benchmarks only)."""
    skills: dict[str, SkillRecordV0] = {}
    plans: list[dict] = []
    for f in sorted(Path(library).glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("kind") == "plan":
            plans.append(rec)
            continue
        try:
            skills.setdefault(rec["name"], SkillRecordV0.from_dict(rec))
        except (KeyError, TypeError, ValueError):
            continue
    return skills, plans


def _library_node(rec: SkillRecordV0, cls: str) -> dict:
    bindings = {}
    for emb, b in sorted(rec.bindings.items()):
        policies = (b or {}).get("policies") or {"scripted": {}}
        bindings[emb] = {key: {"transport": p.get("transport", "inproc"), "ref": p.get("ref"),
                               "checkpoint_sha": p.get("checkpoint_sha")}
                         for key, p in sorted(policies.items())}
    evidence = {emb: {"n": ev.n, "k": ev.k,
                      "by_executor": {key: {"n": e.get("n"), "k": e.get("k")}
                                      for key, e in sorted(ev.by_executor.items())}}
                for emb, ev in sorted(rec.evidence.items())}
    return {
        "kind": "skill",
        "id": f"skill:{rec.name}",
        "name": rec.name,
        "skill_kind": rec.kind,
        "class": cls,
        "description": rec.description,
        "args": dict(rec.args),
        "requires": list(rec.requires),
        "ensures": list(rec.ensures),
        "clobbers": list(rec.clobbers),
        "limits": dict(rec.limits),
        "failure_modes": list(rec.failure_modes),
        "bindings": bindings,
        "evidence": evidence,
        "status": "library",
        "annotations": None,
    }


def _benchmark_cards(cards: list[dict]) -> dict[str, dict]:
    """``[benchmarks.<name>]`` tables across cards, name -> spec (+ ``card`` dir)."""
    out: dict[str, dict] = {}
    for c in cards:
        for name, spec in (c["manifest"].get("benchmarks") or {}).items():
            out.setdefault(name, dict(spec, card=c["dir"]))
    return out


# --- package + capability nodes ---------------------------------------------


def _package_nodes(cards: list[dict]):
    """(package nodes, provider-map). provider-map: capability -> list of
    (card_dir, enabled) for SUPERSEDES."""
    nodes = []
    providers: dict[str, list[tuple[str, bool]]] = {}
    for c in cards:
        m = c["manifest"]
        claim = m.get("claim") or {}
        sealed = claim.get("sealed") or {}
        # [claim] verbatim minus the nested [claim.sealed] table
        claim_flat = {k: v for k, v in claim.items() if k != "sealed"} or None
        enabled = bool(m.get("enabled", True))
        provides = c["contributes"]["mounts"]
        for cap in provides:
            providers.setdefault(cap, []).append((c["dir"], enabled))
        nodes.append({
            "kind": "package",
            "id": c["dir"],
            "name": c["name"],
            "provides": provides,
            "binds": {"tasks": c["contributes"]["task_bindings"],
                      "campaigns": c["contributes"]["campaigns"]},
            "bundles": c["contributes"]["bundles"],
            "actuation": c["actuation"],
            "needs_sim": c["needs_sim"],
            "third_party": sorted(m.get("third_party") or []),
            "enabled": enabled,
            "claim": claim_flat,
            "claim_sealed": sealed or None,
            "annotations": None,
        })
    return nodes, providers


def _capability_nodes() -> list[dict]:
    return [{
        "kind": "capability",
        "id": d.name,
        "contract": d.contract.__name__,
        "privileged": d.privileged,
        "doc": d.doc,
        "annotations": None,
    } for d in CAPABILITIES]


# --- the fold ---------------------------------------------------------------


def build_graph(runs="runs", plugins=PLUGINS_ROOT, annotations=ANNOTATIONS_DIR,
                library=LIBRARY_ROOT) -> dict:
    """The whole vault: ``{schema_version, generated_from, nodes[], edges[]}``.

    Pure fold over the sealed tree; re-run on an unchanged tree -> byte-identical
    (json.dumps(sort_keys=True)). ``generated_from`` is provenance for cache/debug
    (store dir NAMES + mtimes), never hashed into a node's identity.
    """
    runs = Path(runs)
    records, evidenced, store_digests = _skill_records(runs)
    cards = bc.list_cards(Path(plugins))
    package_nodes, providers = _package_nodes(cards)

    # claim.sealed digests across cards: the acceptance seal that gates promotion.
    claimed: set[str] = set()
    claims_edges: list[tuple[str, str, str]] = []  # (card_dir, digest, sealed_store)
    for c in cards:
        sealed = (c["manifest"].get("claim") or {}).get("sealed") or {}
        store = sealed.get("store")
        for dig in sealed.get("skills") or []:
            claimed.add(dig)
            claims_edges.append((c["dir"], dig, store))

    edges: set[tuple] = set()  # (rel, src, dst, rule, via, evidence|None)

    def add(rel, src, dst, rule, via, evidence=None):
        edges.add((rel, src, dst, rule, via, evidence))

    # DESCENDS_FROM (within-store generation chain) + REQUIRES + EVIDENCED_BY
    for store, digs in store_digests.items():
        ordered = sorted(digs, key=lambda d: (records[d].get("generation") or 0, d))
        for prev, cur in zip(ordered, ordered[1:]):
            if (records[cur].get("generation") or 0) > (records[prev].get("generation") or 0):
                add("DESCENDS_FROM", cur, prev, "generation.child_sha_chain",
                    f"runs/{store}/index")
    for dig, rec in records.items():
        feat = (rec.get("preconditions") or {}).get("feature") or ""
        ns = feat.split(".")[0]
        cap = _FEATURE_CAP.get(ns)
        priv = (rec.get("bundle_evidence") or {}).get("declared_privilege") or 0
        if cap and (ns != "privileged" or priv > 0):
            add("REQUIRES", dig, cap, "preconditions.feature.namespace",
                f"runs/{evidenced.get(dig, '?')}/skills/{dig}.json")
        store = evidenced.get(dig)
        if store:
            add("EVIDENCED_BY", dig, store, "skills root sealed store", f"runs/{store}/skills")

    # DESCENDS_FROM (cross-store lineage via prereg parent_store)
    for store, digs in store_digests.items():
        bk, _ = bs.read_store_artifacts(runs / store)
        prereg = (bk.get("preregistration") or [None])[0]
        parent = prereg and prereg.get("parent_store")
        if not parent:
            continue
        parent_name = Path(parent).name
        for pdig in store_digests.get(parent_name, []):
            for cdig in digs:
                # Prereg ancestry applies to records this store MINTED. Seed
                # copies keep their own lineage; linking them here fabricates
                # descent (and self-edges) that falsely retires the parents.
                if evidenced.get(cdig) != store or cdig == pdig:
                    continue
                add("DESCENDS_FROM", cdig, pdig, "prereg.parent_store+parent_final_sha",
                    f"runs/{store}/prereg")

    # PROVIDES / BINDS / CLAIMS / SUPERSEDES (packages)
    for c in cards:
        for cap in c["contributes"]["mounts"]:
            add("PROVIDES", c["dir"], cap, "manifest.mounts", c["dir"])
        for task in sorted(set(c["contributes"]["task_bindings"]) | set(c["contributes"]["campaigns"])):
            add("BINDS", c["dir"], task, "manifest.task_bindings/campaigns", c["dir"])
    for card_dir, dig, store in claims_edges:
        add("CLAIMS", card_dir, dig, "manifest.claim.sealed.skills",
            f"{store}" if store else card_dir)
    for cap, provs in providers.items():
        enabled = [d for d, en in provs if en]
        disabled = [d for d, en in provs if not en]
        for e in enabled:
            for d in disabled:
                add("SUPERSEDES", e, d, "duplicate mount seam, enabled over disabled", cap)

    # GOVERNS + MOUNTED_IN (session chains)
    for sess_dir in sorted(runs.iterdir()):
        if not sess_dir.is_dir() or not bs.is_session(sess_dir):
            continue
        rows = bs.read_session(sess_dir)["rows"]
        via = f"runs/{sess_dir.name}/session-log"
        for boot in rows.get("runtime.boot") or []:
            for dig in boot.get("skills_manifest") or []:
                add("MOUNTED_IN", dig, sess_dir.name, "runtime.boot.skills_manifest", via)
        for r in rows.get("task.plan_complete") or []:
            for nid, nd in (r.get("nodes") or {}).items():
                for dig in ((nd.get("governance") or {}).get("skills")) or []:
                    add("GOVERNS", dig, f"{sess_dir.name}/{nid}",
                        "plan_complete.node.governance.skills", via)

    # library skills: IN_CLASS / DEPENDS_ON / INSTANCE_OF / BOUND_TO / EVIDENCED_ON
    library_recs, plans = _library_records(library)
    benchmarks = _benchmark_cards(cards)
    package_ids = {p["id"] for p in package_nodes}
    classes: dict[str, int] = {}
    instances: dict[str, int] = {}
    for inst, generic in skill_instances(library_recs):
        instances[generic] = instances.get(generic, 0) + 1
        add("INSTANCE_OF", f"skill:{inst}", f"skill:{generic}", "name prefix within class",
            f"skill-library/records/{inst}.json")
    library_nodes = []
    for name, rec in library_recs.items():
        via = f"skill-library/records/{name}.json"
        cls = skill_class(rec)
        node = _library_node(rec, cls)
        if name in instances:
            node["instances"] = instances[name]
        library_nodes.append(node)
        classes[cls] = classes.get(cls, 0) + 1
        add("IN_CLASS", f"skill:{name}", f"class:{cls}", "declared class", via)
        for b in rec.bindings.values():
            for p in ((b or {}).get("policies") or {}).values():
                module = (p.get("ref") or "").split(":")[0]
                card = "plugins/" + module.split(".")[1] if module.startswith("plugins.") else None
                if card in package_ids:
                    add("BOUND_TO", f"skill:{name}", card, "bindings ref module -> card dir", via)
    for src, dst, rule in skill_dependencies(library_recs):
        if src in library_recs and dst in library_recs:  # plan "uses" rows have no plan node
            add("DEPENDS_ON", f"skill:{src}", f"skill:{dst}",
                {"causal": "requires∩ensures", "uses": "plan uses"}.get(rule, rule),
                f"skill-library/records/{src}.json")
    for name, benches in skill_benchmarks(list(library_recs.values()) + plans, benchmarks).items():
        for bench in benches:
            ev = library_recs[name].evidence.get(benchmarks[bench].get("embodiment"))
            add("EVIDENCED_ON", f"skill:{name}", f"benchmark:{bench}", "harness.protocol.skill_benchmarks",
                benchmarks[bench]["card"], (ev.n, ev.k) if ev else None)
    class_nodes = [{"kind": "class", "id": f"class:{c}", "name": c, "skills": n, "annotations": None}
                   for c, n in classes.items()]
    benchmark_nodes = [{"kind": "benchmark", "id": f"benchmark:{b}", "name": b,
                        "embodiment": spec.get("embodiment"), "tasks": list(spec.get("tasks") or []),
                        "arms": list(spec.get("arms") or []), "card": spec["card"], "annotations": None}
                       for b, spec in benchmarks.items()]

    # status: promoted (claimed + judgement-established) / candidate; then retire
    # a promoted record a same-lineage promoted successor DESCENDS_FROM.
    status: dict[str, str] = {}
    for dig, rec in records.items():
        status[dig] = ("promoted" if dig in claimed and rec.get("heldout_judgement_established")
                       else "candidate")
    desc = [(s, d) for (rel, s, d, *_rest) in edges if rel == "DESCENDS_FROM"]
    for src, dst in desc:
        if status.get(dst) == "promoted" and status.get(src) == "promoted" and _same_lineage(records, src, dst):
            status[dst] = "retired"

    skill_nodes = [_skill_node(d, records[d], status[d], evidenced.get(d)) for d in records]
    nodes = skill_nodes + library_nodes + class_nodes + benchmark_nodes + package_nodes + _capability_nodes()
    nodes.sort(key=lambda n: (n["kind"], n["id"]))

    if annotations is not None:
        _attach_annotations(nodes, annotations)

    edge_list = [{"rel": rel, "src": src, "dst": dst, "rule": rule, "via": via,
                  **({"n": nk[0], "k": nk[1]} if nk else {})}
                 for (rel, src, dst, rule, via, nk) in edges]
    # Full-tuple key: (rel,src,dst) can repeat with different rule/via, and the
    # collection set's iteration order varies per process (hash randomization).
    edge_list.sort(key=lambda e: (e["rel"], e["src"], e["dst"], e["rule"], e["via"]))

    mtimes = {s: bs.store_mtime(runs / s) for s in sorted(store_digests)}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": {"runs": Path(runs).name, "plugins": Path(plugins).name,
                           "store_mtimes": mtimes},
        "nodes": nodes,
        "edges": edge_list,
    }


def _same_lineage(records: dict, a: str, b: str) -> bool:
    """Same (task, policy, recovery_name) -- a successor retires a predecessor
    only within one repair lineage (place/replace does NOT retire stack/regrasp)."""
    def key(d):
        r = records[d]
        return (r.get("task"), r.get("policy"), (r.get("recovery") or {}).get("name"))
    return key(a) == key(b)


# --- node / neighbors views (the wiki page) ---------------------------------


def node(graph: dict, id: str) -> dict:
    """One node + its ``out`` edges and ``backlinks`` (in-edges) -- the wiki page."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    if id not in by_id:
        return {"error": "unknown node"}
    out = dict(by_id[id])
    out["out"] = [e for e in graph["edges"] if e["src"] == id]
    out["backlinks"] = [e for e in graph["edges"] if e["dst"] == id]
    return out


def neighbors(graph: dict, id: str, relation: str | None = None) -> dict:
    """Adjacency (both directions) for one node, optionally one ``rel``."""
    if id not in {n["id"] for n in graph["nodes"]}:
        return {"error": "unknown node"}
    adj = [e for e in graph["edges"] if e["src"] == id or e["dst"] == id]
    if relation:
        adj = [e for e in adj if e["rel"] == relation]
    return {"id": id, "relation": relation, "neighbors": adj}


# --- additive annotation sidecars (doctor-guarded, never overwrite derived) --


def _annotation_id(path: Path, ann_dir: Path) -> str:
    return path.relative_to(ann_dir).with_suffix("").as_posix()


def _attach_annotations(nodes: list[dict], ann_dir) -> None:
    """Attach a valid sidecar under ``node.annotations`` (a separate key -- it can
    never overwrite a derived field). Invalid/unknown files are left to
    vault_doctor to flag; the fold is silent so a stray file cannot crash it."""
    ann_dir = Path(ann_dir)
    if not ann_dir.is_dir():
        return
    by_id: dict[str, dict] = {}
    for p in ann_dir.rglob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and set(data) <= ANNOTATION_KEYS:
            by_id[_annotation_id(p, ann_dir)] = data
    for n in nodes:
        if n["id"] in by_id:
            n["annotations"] = by_id[n["id"]]


def vault_doctor(graph: dict | None = None, ann_dir=ANNOTATIONS_DIR,
                 runs="runs", plugins=PLUGINS_ROOT) -> list[str]:
    """Fail loud on any sidecar that would contradict the fold. Returns a list of
    error strings (empty == green). An annotation may ADD context but never
    overwrite derived truth: it is rejected if it (a) targets a node id not in the
    graph, (b) uses a key outside the fixed additive set, (c) has a dangling
    ``see_also`` target, or (d) is malformed JSON/shape."""
    if graph is None:
        graph = build_graph(runs, plugins, annotations=None)
    ids = {n["id"] for n in graph["nodes"]}
    ann_dir = Path(ann_dir)
    errors: list[str] = []
    if not ann_dir.is_dir():
        return errors
    for p in sorted(ann_dir.rglob("*.json")):
        nid = _annotation_id(p, ann_dir)
        try:
            ann = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{nid}: invalid JSON ({exc})")
            continue
        if nid not in ids:
            errors.append(f"{nid}: annotation targets unknown node")
            continue
        if not isinstance(ann, dict):
            errors.append(f"{nid}: annotation must be a JSON object")
            continue
        extra = sorted(set(ann) - ANNOTATION_KEYS)
        if extra:
            errors.append(f"{nid}: keys outside the additive set {extra} (derived fields are reserved)")
        if "note" in ann and not isinstance(ann["note"], str):
            errors.append(f"{nid}: note must be a string")
        if "tags" in ann and not (isinstance(ann["tags"], list) and all(isinstance(t, str) for t in ann["tags"])):
            errors.append(f"{nid}: tags must be a list of strings")
        if "see_also" in ann:
            sa = ann["see_also"]
            if not (isinstance(sa, list) and all(isinstance(t, str) for t in sa)):
                errors.append(f"{nid}: see_also must be a list of strings")
            else:
                for t in sa:
                    if t not in ids:
                        errors.append(f"{nid}: see_also target {t!r} is not a node")
    return errors


if __name__ == "__main__":  # self-check: fold the REAL runs/ and assert known truths
    import sys

    repo = Path(__file__).resolve().parent.parent
    g = build_graph(repo / "runs", PLUGINS_ROOT)
    ids = {n["id"] for n in g["nodes"]}
    E = {(e["rel"], e["src"], e["dst"]) for e in g["edges"]}
    STACK = "57162e40d2bd4a0d59973d8c51d19f7267b682ba582c7b5c84568b334f02d41d"
    ADC = "adc5578932681b6607737cdee40164c472e1bde277b0637a3b2c02623a3c4440"
    EB = "eb46481a88b93cf9db9e774734fdde063725557d83f1abffe3033cd33a45a40f"
    if STACK in ids:  # only when the sealed evidence is present in this checkout
        assert ("DESCENDS_FROM", ADC, STACK) in E
        assert ("DESCENDS_FROM", EB, ADC) in E
        assert ("CLAIMS", "plugins/skill_place", EB) in E
        assert ("REQUIRES", EB, "embodiment.ground_truth") in E
        assert ("REQUIRES", STACK, "percept.model") in E
        assert ("SUPERSEDES", "plugins/reasoner", "plugins/model_qwen") in E
        print(f"vault self-check OK: {len(g['nodes'])} nodes, {len(g['edges'])} edges")
    else:
        print("vault self-check skipped: sealed runs/ evidence not in this checkout")
    if vault_doctor(g):
        print("vault_doctor errors:", *vault_doctor(g), sep="\n  ", file=sys.stderr)
        sys.exit(1)
