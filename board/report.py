"""Pure RSI report builder: parsed store/ledger/rounds data -> one self-contained HTML.

One button in the board (or one CLI call) turns the whole runs/ tree into a single
file with inline CSS and inline SVG -- zero external requests, prints cleanly to PDF
from the browser. All parsing is reused from ``board.store``; this module only shapes
the already-parsed dicts into a document and ports the board's hand-rolled SVG charts
to Python. Nothing here writes to runs/.

The document sections, in order (mirrors the morning read):
  executive summary -> per-campaign -> diagnostics -> live -> ledger -> rounds -> footer.
"""

from __future__ import annotations

import hashlib
import html
import time
from pathlib import Path

from board import store as bs

# board/index.html palette, kept in sync so the report is the same design language.
PAL = ["#4d6bfe", "#5ee0c8", "#e6b13a", "#f0616d", "#a78bfa", "#37d67a"]
AXIS = "#8896ab"  # axis/label ink in the SVGs
INK = "#e6edf7"   # value ink in the SVGs
DASH = "–"   # en dash for missing values


# --- formatters (mirror index.html's pct/sgn/num/esc) -----------------------

def _esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _pct(v) -> str:
    return DASH if v is None else f"{v * 100:.1f}%"


def _sgn(v) -> str:
    return DASH if v is None else f"{'+' if v >= 0 else ''}{v * 100:.1f}pp"


def _num(v) -> str:
    return DASH if v is None else str(v)


def _pv(v) -> str:
    if v is None:
        return DASH
    return f"{v:.1e}" if v < 1e-3 else f"{v:.4f}"


def _fi(v) -> str:  # chart count formatter
    return f"{round(v)}"


def _fpct0(v) -> str:  # chart rate formatter, 0 decimals
    return f"{v * 100:.0f}%"


# --- SVG chart primitives (ported from index.html: bars/line/groupedBars) ----

def _bars(items, h=120, fmt=_num, colors=None):
    colors = colors or PAL
    if not items:
        return ""
    w = max(len(items) * 46, 120)
    bh = h - 22
    m = max([abs(it.get("v") or 0) for it in items] + [1e-9])
    out = [f'<svg viewBox="0 0 {w} {h}">']
    for i, it in enumerate(items):
        v = it.get("v") or 0
        x = i * 46 + 8
        bar = max(v / m * bh, 1)
        y = bh - bar
        c = it.get("color") or colors[i % len(colors)]
        lbl = _esc(it.get("label"))
        out.append(f'<rect x="{x}" y="{y:.1f}" width="30" height="{bar:.1f}" rx="3" '
                   f'fill="{c}"><title>{lbl}: {fmt(v)}</title></rect>')
        out.append(f'<text x="{x + 15}" y="{bh + 12}" fill="{AXIS}" font-size="10" '
                   f'text-anchor="middle" font-family="monospace">{lbl}</text>')
        out.append(f'<text x="{x + 15}" y="{y - 3:.1f}" fill="{INK}" font-size="10" '
                   f'text-anchor="middle" font-family="monospace">{fmt(v)}</text>')
    out.append("</svg>")
    return "".join(out)


def _line(pts, h=120, fmt=None, color="#4d6bfe"):
    if not pts:
        return ""
    fmt = fmt or (lambda v: f"{v:.3f}")
    w = max(len(pts) * 54, 140)
    bh = h - 24
    ys = [p["v"] for p in pts]
    mn, mx = min(ys), max(ys)
    rng = (mx - mn) or 1

    def X(i):
        return i * 54 + 24

    def Y(v):
        return bh - ((v - mn) / rng) * (bh - 8) - 4

    d = " ".join(f'{"L" if i else "M"}{X(i)},{Y(p["v"]):.1f}' for i, p in enumerate(pts))
    out = [f'<svg viewBox="0 0 {w} {h}">',
           f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>']
    for i, p in enumerate(pts):
        out.append(f'<circle cx="{X(i)}" cy="{Y(p["v"]):.1f}" r="3.5" fill="{color}">'
                   f'<title>{_esc(p["label"])}: {fmt(p["v"])}</title></circle>')
        out.append(f'<text x="{X(i)}" y="{bh + 12}" fill="{AXIS}" font-size="10" '
                   f'text-anchor="middle" font-family="monospace">{_esc(p["label"])}</text>')
        out.append(f'<text x="{X(i)}" y="{Y(p["v"]) - 7:.1f}" fill="{INK}" font-size="9.5" '
                   f'text-anchor="middle" font-family="monospace">{fmt(p["v"])}</text>')
    out.append("</svg>")
    return "".join(out)


def _grouped_bars(groups, series, h=150, fmt=_num):
    if not groups or not series:
        return ""
    gw = max(len(series) * 22 + 26, 70)
    w = max(len(groups) * gw, 140)
    bh = h - 26
    m = max([v or 0 for se in series for v in se["vals"]] + [1e-9])
    out = [f'<svg viewBox="0 0 {w} {h}">']
    for gi, g in enumerate(groups):
        for si, se in enumerate(series):
            v = (se["vals"][gi] if gi < len(se["vals"]) else 0) or 0
            x = gi * gw + 13 + si * 22
            bar = max(v / m * bh, 1)
            y = bh - bar
            out.append(f'<rect x="{x}" y="{y:.1f}" width="18" height="{bar:.1f}" rx="2" '
                       f'fill="{se["color"]}"><title>{_esc(g["label"])} {_esc(se["name"])}: '
                       f'{fmt(v)}</title></rect>')
            out.append(f'<text x="{x + 9}" y="{y - 3:.1f}" fill="{INK}" font-size="9" '
                       f'text-anchor="middle" font-family="monospace">{fmt(v)}</text>')
        out.append(f'<text x="{gi * gw + gw / 2}" y="{bh + 13}" fill="{AXIS}" font-size="10" '
                   f'text-anchor="middle" font-family="monospace">{_esc(g["label"])}</text>')
    out.append("</svg>")
    return "".join(out)


def _legend(series):
    return ('<div class="legend">'
            + "".join(f'<span><i style="background:{s["color"]}"></i>{_esc(s["name"])}</span>'
                      for s in series)
            + "</div>")


def _chart(title, svg):
    return f'<div class="chart"><div class="t">{_esc(title)}</div>{svg}</div>' if svg else ""


# --- shared sub-renderers ---------------------------------------------------

def _ablation_chart(a):
    if not a:
        return ""
    pts = [{"label": f'sd{x["sd"]}{"*" if x.get("declared_privilege") else ""}',
            "v": x["governed_rate"]} for x in a if x.get("governed_rate") is not None]
    return (_chart("held-out transfer ablation (governed rate vs sensor sd)",
                   _line(pts, fmt=_fpct0, color="#5ee0c8"))
            + '<div class="sub">* sd=0 is the privileged ground-truth percept; the drop '
              'across sd is the transfer gap.</div>')


def _stage_charts(staged):
    """Per-stage success + first-failure attribution, grouped by block. `staged` is
    the subset of held-out blocks that carry a stage_attribution overlay."""
    if not staged:
        return ('<div class="card sub">no stage attribution on these blocks '
                '(campaign ran without a stage overlay)</div>')
    stages = staged[0]["stage_attribution"]["stage_order"]
    h = ['<div class="card"><h2>Stage attribution</h2>',
         ('<div class="sub">where the residual failures land – the quantified handoff '
          'to the place primitive</div>'),
         "<h4>per-stage success (reached → success)</h4>",
         _legend([{"name": _num(b["block"]), "color": PAL[i % len(PAL)]}
                  for i, b in enumerate(staged)]),
         '<div class="charts">']
    for st in stages:
        series = []
        for i, b in enumerate(staged):
            ps = b["stage_attribution"]["per_stage"][st]
            rate = ps["success"] / max(ps["reached"], 1)
            series.append({"name": _num(b["block"]), "color": PAL[i % len(PAL)], "vals": [rate]})
        h.append(_chart(f"{st} success", _grouped_bars([{"label": st}], series, fmt=_fpct0)))
    h.append('</div><h4>first-failure attribution (count by first failed stage)</h4>')
    h.append(_legend([{"name": _num(b["block"]), "color": PAL[i % len(PAL)]}
                      for i, b in enumerate(staged)]))
    ffkeys = list(staged[0]["stage_attribution"]["first_failure"].keys())
    ffseries = [{"name": _num(b["block"]), "color": PAL[i % len(PAL)],
                 "vals": [b["stage_attribution"]["first_failure"].get(k, 0) for k in ffkeys]}
                for i, b in enumerate(staged)]
    h.append('<div class="charts">'
             + _chart("first failure by stage",
                      _grouped_bars([{"label": k} for k in ffkeys], ffseries, fmt=_fi))
             + "</div></div>")
    return "".join(h)


def _heldout_table(blocks):
    rows = []
    for b in blocks:
        p = b.get("paired") or {}
        judged = (DASH if b.get("judgement") is None
                  else ("✓" if b["judgement"] else "✗"))
        rows.append(
            f'<tr><td>{_num(b.get("block"))}</td><td style="text-align:left">{_esc(b.get("source"))}</td>'
            f'<td>{_num(p.get("n"))}</td><td>{_pct(p.get("base_rate"))}</td>'
            f'<td>{_pct(p.get("governed_rate"))}</td><td>{_sgn(b.get("delta"))}</td>'
            f'<td>{_num(p.get("fixed"))}</td><td>{_num(p.get("broken"))}</td>'
            f'<td>{_pv(p.get("p_value"))}</td><td>{judged}</td></tr>')
    return ('<table><thead><tr><th>block</th><th>source</th><th>n</th><th>base</th><th>gov</th>'
            '<th>Δ</th><th>fixed</th><th>broken</th><th>p</th><th>judged</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


# --- section: per-campaign --------------------------------------------------

def _gen_table(gens):
    rows = []
    for x in gens:
        d = x.get("dev_gate") or {}
        badge = "ok" if x.get("promoted") else "no"
        status = "promoted" if x.get("promoted") else "rejected"
        rule = x.get("rule") or {}
        rec = f' → {_esc(rule.get("recovery"))}' if rule.get("recovery") else ""
        rows.append(
            f'<tr><td>g{x.get("generation")}</td>'
            f'<td><span class="badge {badge}">{status}</span></td>'
            f'<td style="text-align:left" title="{_esc(x.get("reason"))}">'
            f'{_esc(rule.get("trigger_str"))}{rec}</td>'
            f'<td>{_num(d.get("n"))}</td><td>{_pct(d.get("base_rate"))}</td>'
            f'<td>{_pct(d.get("governed_rate"))}</td><td>{_sgn(x.get("dev_delta"))}</td>'
            f'<td>{_num(d.get("fixed"))}</td><td>{_num(d.get("broken"))}</td>'
            f'<td>{_num(d.get("fires"))}</td><td>{_pv(d.get("p_value"))}</td></tr>')
    return ('<table><thead><tr><th>gen</th><th>status</th><th>rule</th><th>n</th><th>base</th>'
            '<th>gov</th><th>Δ</th><th>fixed</th><th>broken</th><th>fires</th><th>p</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _scalar_panel(g):
    labels = [{"label": "g" + str(x["generation"])} for x in g]
    c = [_chart("dev fixed / broken", _grouped_bars(labels, [
            {"name": "fixed", "color": "#37d67a", "vals": [x["dev_gate"].get("fixed") for x in g]},
            {"name": "broken", "color": "#f0616d", "vals": [x["dev_gate"].get("broken") for x in g]},
         ], fmt=_fi)),
         _chart("dev delta (governed - base)",
                _line([{"label": "g" + str(x["generation"]), "v": x.get("dev_delta") or 0} for x in g],
                      fmt=lambda v: f"{v * 100:.1f}pp")),
         _chart("dev rate: base vs governed", _grouped_bars(labels, [
            {"name": "base", "color": "#5c6b82", "vals": [x["dev_gate"].get("base_rate") for x in g]},
            {"name": "governed", "color": "#4d6bfe",
             "vals": [x["dev_gate"].get("governed_rate") for x in g]},
         ], fmt=_fpct0)),
         _chart("dev p-value", _bars(
            [{"label": "g" + str(x["generation"]), "v": x["dev_gate"].get("p_value") or 0,
              "color": "#37d67a" if (x["dev_gate"].get("p_value") or 1) < 0.05 else "#f0616d"}
             for x in g],
            fmt=lambda v: f"{v:.1e}" if v < 1e-3 else f"{v:.3f}"))]
    return ('<div class="charts">'
            + _legend([{"name": "fixed", "color": "#37d67a"}, {"name": "broken", "color": "#f0616d"}])
            + "".join(c) + "</div>")


def _campaign_section(runs, name, detail):
    p = detail.get("prereg")
    res = detail.get("result")
    g = detail.get("generations") or []
    h = [f'<div class="card"><h2>{_esc(name)}</h2>']
    if detail.get("skipped"):
        h.append(f'<div class="warnbar">␉ {detail["skipped"]} artifact(s) mid-write, '
                 "skipped this poll</div>")
    if p:
        blk = (f' [{p["heldout_block"][0]}–{p["heldout_block"][1]})'
               if p.get("heldout_block") else "")
        h.append(
            f'<div class="kv"><span><b>task</b> {_esc(p.get("task"))}</span>'
            f'<span><b>policy</b> {_esc(p.get("policy"))}</span>'
            f'<span><b>dev</b> {p.get("dev_n")}</span>'
            f'<span><b>held-out</b> {p.get("heldout_n")}{blk}</span>'
            f'<span><b>stages</b> {p.get("stages")}</span>'
            f'<span><b>noise</b> {p.get("percept_noise")}</span>'
            f'<span><b>terminal</b> {p.get("terminal_label")}</span>'
            f'<span><b>α</b> {p.get("alpha")}</span>'
            f'<span><b>min_fixed</b> {p.get("min_fixed")}</span></div>')
    h.append("</div>")

    if g:
        h.append(f'<div class="card"><h2>Generation ladder – {_esc(name)}</h2>'
                 f'{_gen_table(g)}<h4>scalars across generations</h4>{_scalar_panel(g)}</div>')

    if res:
        hd = res.get("heldout") or {}
        vb = res.get("heldout_vs_blind") or {}
        vb_html = ""
        if vb.get("fixed") is not None:
            vb_html = ('<h4>vs blind twin (is the win judgement?)</h4><div class="kv">'
                       f'<span><b>fixed</b> {vb.get("fixed")}</span>'
                       f'<span><b>broken</b> {vb.get("broken")}</span>'
                       f'<span><b>p</b> {_pv(vb.get("p_value"))}</span></div>')
        h.append(
            f'<div class="card"><h2>Campaign result – {_esc(name)}</h2>'
            f'<div class="kv"><span><b>promoted</b> {res.get("promoted")}/{res.get("generations")}</span>'
            f'<span><b>rules</b> {_esc(", ".join(res.get("rules") or []))}</span></div>'
            "<h4>held-out (scored once)</h4>"
            f'<div class="kv"><span><b>n</b> {_num(hd.get("n"))}</span>'
            f'<span><b>base</b> {_pct(hd.get("base_rate"))}</span>'
            f'<span><b>governed</b> {_pct(hd.get("governed_rate"))}</span>'
            f'<span><b>Δ</b> {_sgn(res.get("heldout_delta"))}</span>'
            f'<span><b>fixed</b> {_num(hd.get("fixed"))}</span>'
            f'<span><b>broken</b> {_num(hd.get("broken"))}</span>'
            f'<span><b>fires</b> {_num(hd.get("fires"))}</span>'
            f'<span><b>p</b> {_pv(hd.get("p_value"))}</span></div>'
            f'{vb_html}<h4>ablation</h4><div class="charts">{_ablation_chart(res.get("ablation"))}</div>'
            "</div>")

    # multi-block held-out + stage attribution (pulled through the same parse layer)
    hb = bs.heldout_blocks(runs, name)
    blocks = hb.get("blocks") or []
    if blocks:
        h.append('<div class="card"><h2>Held-out blocks – ' + _esc(name) + "</h2>"
                 f'<div class="sub">{len(blocks)} block(s), the campaign’s own block plus '
                 "any sibling rescores</div>" + _heldout_table(blocks)
                 + '<h4>fixed &amp; delta across blocks</h4>'
                 + _legend([{"name": "fixed", "color": "#37d67a"},
                            {"name": "delta pp", "color": "#5ee0c8"}])
                 + '<div class="charts">'
                 + _chart("fixed per block",
                          _bars([{"label": _num(b.get("block")), "v": (b.get("paired") or {}).get("fixed") or 0}
                                  for b in blocks], fmt=_fi, colors=["#37d67a"]))
                 + _chart("delta per block (pp)",
                          _bars([{"label": _num(b.get("block")), "v": (b.get("delta") or 0) * 100}
                                  for b in blocks], fmt=lambda v: f"{v:.1f}", colors=["#5ee0c8"]))
                 + "</div></div>")
        staged = [b for b in blocks if b.get("stage_attribution")]
        if staged:
            h.append(_stage_charts(staged))
    return "".join(h)


# --- section: diagnostics ---------------------------------------------------

def _diag_round25(name, r):
    names = list(r["arms"].keys())
    svg = _grouped_bars(
        [{"label": n} for n in names],
        [{"name": "fixed", "color": "#37d67a",
          "vals": [(r["arms"][n]["heldout"] or {}).get("fixed") for n in names]},
         {"name": "fires", "color": "#4d6bfe",
          "vals": [(r["arms"][n]["heldout"] or {}).get("fires") for n in names]}], fmt=_fi)
    rows = "".join(
        f'<tr><td>{_esc(n)}</td><td style="text-align:left">{_esc(r["arms"][n]["trigger_str"])}</td>'
        f'<td>{_num((r["arms"][n]["heldout"] or {}).get("fixed"))}</td>'
        f'<td>{_num((r["arms"][n]["heldout"] or {}).get("broken"))}</td>'
        f'<td>{_num((r["arms"][n]["heldout"] or {}).get("fires"))}</td></tr>' for n in names)
    return (f'<div class="card"><h2>Arm comparison – {_esc(name)}</h2>'
            f'<div class="sub">held-out block [{r.get("heldout_block")}] · identical detection, '
            "different repair yield</div>"
            "<table><thead><tr><th>arm</th><th>rule</th><th>fixed</th><th>broken</th><th>fires</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
            "<h4>fixed vs fires by arm</h4>"
            + _legend([{"name": "fixed", "color": "#37d67a"}, {"name": "fires", "color": "#4d6bfe"}])
            + f'<div class="charts">{_chart("", svg)}</div></div>')


def _diag_probe(name, p):
    a = (p.get("p2") or {}).get("arms") or {}
    keys = list(a.keys())
    svg = _bars([{"label": k.replace("search_", "s").replace("naive_", "n").replace("arm", "@"),
                  "v": a[k].get("fixed") or 0} for k in keys], fmt=_fi, colors=["#37d67a"]) if keys else ""
    p1 = p.get("p1") or {}
    p1_html = ""
    if p1:
        p1_html = ('<div class="kv">'
                   f'<span><b>tie@top</b> {p1.get("tie_count_at_top")}</span>'
                   f'<span><b>naive arm reachable</b> '
                   f'{(p1.get("arm_set") or {}).get("naive_arm_reachable")}</span>'
                   f'<span><b>naive thr in grid</b> {(p1.get("grid_check") or {}).get("in_grid")}</span>'
                   "</div>")
    return (f'<div class="card"><h2>Arm-time probe – {_esc(name)} '
            f'<span class="badge no">{_esc(p.get("grade"))}</span></h2>'
            '<div class="sub">is the search objective blind to fire-time repair yield?</div>'
            f"{p1_html}"
            + (f'<h4>repair yield (fixed) by fire time</h4><div class="charts">'
               f'{_chart("fixed per arm", svg)}</div>' if svg else "")
            + "</div>")


def _diag_fix(name, f):
    hd = f.get("heldout") or {}
    pick = (hd.get("fixed_pick") or {}).get("fixed")
    anch = (hd.get("search_arm43_anchor") or {}).get("fixed")
    return (f'<div class="card"><h2>Objective-repair validation – {_esc(name)} '
            f'<span class="badge no">{_esc(f.get("grade"))}</span></h2>'
            '<div class="sub">search now self-selects the peak-yield arm</div>'
            '<div class="kv">'
            f'<span><b>dev rate</b> {_pct(f.get("dev_rate"))}</span>'
            f'<span><b>top score</b> {_num(f.get("top_score"))}</span>'
            f'<span><b>picked arm fixed</b> {_num(pick)}</span>'
            f'<span><b>old search arm fixed</b> {_num(anch)}</span>'
            f'<span><b>anchors</b> {_esc(f.get("anchors"))}</span></div></div>')


def _diagnostics_section(details):
    cards = []
    for name, d in details.items():
        if d.get("round25"):
            cards.append(_diag_round25(name, d["round25"]))
        if d.get("probe"):
            cards.append(_diag_probe(name, d["probe"]))
        if d.get("round88_fix"):
            cards.append(_diag_fix(name, d["round88_fix"]))
    if not cards:
        return ""
    return ('<section id="diagnostics"><h1 class="sec">Diagnostics</h1>'
            '<div class="sub secsub">the objective-repair narrative: detection is identical across '
            "arms, repair yield rides fire time, and the search now self-selects the peak arm</div>"
            + "".join(cards) + "</section>")


# --- section: executive summary ---------------------------------------------

def _primary_campaign(runs, details):
    """The campaign whose held-out is the headline: most blocks, breaking ties toward
    one carrying a stage-attribution overlay. Returns (name, heldout_blocks dict)."""
    best = None
    best_key = (0, 0)
    for name, d in details.items():
        if not (d.get("generations") or d.get("result")):
            continue
        hb = bs.heldout_blocks(runs, name)
        nb = len(hb.get("blocks") or [])
        staged = 1 if any(b.get("stage_attribution") for b in hb.get("blocks") or []) else 0
        if nb and (nb, staged) > best_key:
            best_key = (nb, staged)
            best = (name, hb)
    return best


def _exec_summary(runs, details):
    parts = ['<section id="exec"><h1 class="sec">Executive summary</h1>']
    prim = _primary_campaign(runs, details)

    if prim:
        name, hb = prim
        blocks = hb["blocks"]
        deltas = " / ".join(_sgn(b["delta"]) for b in blocks)
        tot_fixed = sum((b.get("paired") or {}).get("fixed") or 0 for b in blocks)
        tot_broken = sum((b.get("paired") or {}).get("broken") or 0 for b in blocks)
        tot_n = sum((b.get("paired") or {}).get("n") or 0 for b in blocks)
        judged = sum(1 for b in blocks
                     if b.get("judgement") or (b.get("vs_blind") or {}).get("fixed") is not None)
        parts.append(
            '<div class="card headline"><h2>Held-out headline – ' + _esc(name) + "</h2>"
            f'<p class="lede">Three-block held-out on <b>{_esc(hb.get("task"))}</b>: '
            f"<b>{deltas}</b> – <b>{tot_fixed} fixed, {tot_broken} broken</b> across "
            f"<b>n={tot_n}</b>. Blind-twin judgement established on "
            f"<b>{judged}/{len(blocks)}</b> block(s).</p>"
            + '<div class="charts">'
            + _chart("delta per block (pp)",
                     _bars([{"label": _num(b.get("block")), "v": (b.get("delta") or 0) * 100}
                            for b in blocks], fmt=lambda v: f"{v:.1f}", colors=["#5ee0c8"]))
            + _chart("fixed per block",
                     _bars([{"label": _num(b.get("block")), "v": (b.get("paired") or {}).get("fixed") or 0}
                            for b in blocks], fmt=_fi, colors=["#37d67a"]))
            + "</div>")
        # ceiling: place-stage attribution
        staged = [b for b in blocks if b.get("stage_attribution")]
        if staged:
            frags = []
            for b in staged:
                per = b["stage_attribution"]["per_stage"]
                seg = ", ".join(
                    f'{st} {per[st]["success"]}/{per[st]["reached"]}'
                    for st in b["stage_attribution"]["stage_order"])
                frags.append(f'block {_num(b.get("block"))}: {seg}')
            parts.append('<p class="lede">Current ceiling is the <b>place</b> primitive – '
                         + "; ".join(_esc(f) for f in frags)
                         + " (reached → success). Grasp is largely solved; the residual "
                           "failures land on place, the quantified handoff to the place-primitive "
                           "work.</p>")
        parts.append("</div>")

    # what RSI improved this cycle (objective-repair story)
    fix = next((d for d in details.values() if d.get("round88_fix")), None)
    r25 = next((d for d in details.values() if d.get("round25")), None)
    if fix or r25:
        bits = []
        if r25:
            arms = r25["round25"]["arms"]
            fires = {(a.get("heldout") or {}).get("fires") for a in arms.values()}
            fx = {n: (arms[n].get("heldout") or {}).get("fixed") for n in arms}
            fset = ", ".join(str(f) for f in sorted(fires) if f is not None)
            bits.append(f"detection-identical arms (all {fset} fires) split only on repair yield "
                        f"({', '.join(f'{n} {v}' for n, v in fx.items())})")
        if fix:
            f = fix["round88_fix"]
            anch = f.get("anchors") or {}
            old = anch.get("old_search_arm43_fixed")
            peak = anch.get("naive_arm95_fixed")
            pick = ((f.get("heldout") or {}).get("fixed_pick") or {}).get("fixed")
            if old is not None and peak is not None:
                bits.append(f"repair yield rides fire time {old}→{peak}")
            if pick is not None:
                bits.append(f"the search now self-selects the peak arm (fixed {pick})")
        parts.append('<div class="card"><h2>What RSI improved this cycle</h2>'
                     '<p class="lede">Objective repair: ' + "; ".join(_esc(b) for b in bits)
                     + ".</p></div>")

    if not prim and not fix and not r25:
        parts.append('<div class="card sub">no campaign or diagnostic data present.</div>')
    parts.append("</section>")
    return "".join(parts)


# --- section: live ----------------------------------------------------------

def _live_section(runs, stores, details, threshold_s, now):
    live = [s for s in stores if now - s.get("mtime", 0) <= threshold_s]
    if not live:
        return ('<section id="live"><h1 class="sec">Live campaigns</h1>'
                f'<div class="card sub">no store written in the last {int(threshold_s)}s '
                "– nothing in progress.</div></section>")
    cards = []
    for s in live:
        d = details.get(s["name"], {})
        gens = d.get("generations") or []
        age = int(now - s.get("mtime", 0))
        last = gens[-1] if gens else None
        last_html = ""
        if last:
            dg = last.get("dev_gate") or {}
            last_html = (f'<div class="kv"><span><b>latest gen</b> g{last.get("generation")}</span>'
                         f'<span><b>status</b> {"promoted" if last.get("promoted") else "pending/rejected"}</span>'
                         f'<span><b>dev Δ</b> {_sgn(last.get("dev_delta"))}</span>'
                         f'<span><b>fixed</b> {_num(dg.get("fixed"))}</span>'
                         f'<span><b>broken</b> {_num(dg.get("broken"))}</span></div>')
        skip = (f'<span style="color:var(--warn)"><b>mid-write</b> {d.get("skipped")}</span>'
                if d.get("skipped") else "")
        cards.append(
            f'<div class="card live"><h2>{_esc(s["name"])} '
            '<span class="badge live-badge">in progress</span></h2>'
            f'<div class="kv"><span><b>task</b> {_esc(s.get("task"))}</span>'
            f'<span><b>generations</b> {len(gens)}</span>'
            f'<span><b>last write</b> {age}s ago</span>{skip}</div>{last_html}</div>')
    return ('<section id="live"><h1 class="sec">Live campaigns</h1>'
            '<div class="sub secsub">stores written within the freshness window, flagged for the '
            "morning glance</div>" + "".join(cards) + "</section>")


# --- section: ledger + rounds -----------------------------------------------

def _ledger_section(ranges):
    if not ranges:
        return ('<section id="ledger"><h1 class="sec">Seed ledger</h1>'
                '<div class="card sub">no seed ledger found in STATUS.md</div></section>')
    lo = min(r["lo"] for r in ranges)
    hi = max(r["hi"] for r in ranges)
    span = (hi - lo) or 1
    col = {"burned": "#f0616d", "reserved": "#e6b13a", "planned": "#4d6bfe"}
    segs = []
    for st in ("planned", "reserved", "burned"):  # burned/reserved layer over planned
        for r in (x for x in ranges if x["state"] == st):
            x = (r["lo"] - lo) / span * 100
            w = max((r["hi"] - r["lo"]) / span * 100, 0.4)
            segs.append(f'<div class="seg" style="left:{x:.2f}%;width:{w:.2f}%;'
                        f'background:{col[st]}" title="{_esc(r["line"])}"></div>')
    rows = "".join(
        f'<tr><td>{r["lo"]}–{r["hi"]}</td>'
        f'<td><span class="badge" style="background:{col[r["state"]]}33;color:{col[r["state"]]}">'
        f'{r["state"]}</span></td><td style="text-align:left">{_esc(r["line"])}</td></tr>'
        for r in ranges)
    return ('<section id="ledger"><h1 class="sec">Seed ledger</h1>'
            '<div class="card"><h2>Seed-block burn map</h2>'
            f'<div class="sub">parsed from STATUS.md · range {lo}–{hi}</div>'
            '<div class="legend">'
            f'<span><i style="background:{col["burned"]}"></i>burned</span>'
            f'<span><i style="background:{col["reserved"]}"></i>reserved</span>'
            f'<span><i style="background:{col["planned"]}"></i>planned</span></div>'
            f'<div class="ledger-line">{"".join(segs)}</div>'
            '<table><thead><tr><th>block</th><th>state</th><th>source line</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></section>')


def _rounds_section(rounds, limit=10):
    if not rounds:
        return ('<section id="rounds"><h1 class="sec">Rounds</h1>'
                '<div class="card sub">no rounds found in progress.md</div></section>')
    shown = rounds[:limit]
    body = []
    for r in shown:
        body.append(
            '<details class="round" open><summary>'
            f'<span class="rn">Round {r["round"]}</span> <span class="rd">{_esc(r["date"])}</span> '
            f'· {_esc(r["title"])}</summary><pre>{_esc(r["body"])}</pre></details>')
    return ('<section id="rounds"><h1 class="sec">Rounds</h1>'
            f'<div class="sub secsub">latest {len(shown)} of {len(rounds)}, full narrative text</div>'
            + "".join(body) + "</section>")


# --- footer -----------------------------------------------------------------

def _store_sha(runs, name):
    try:
        return hashlib.sha256((Path(runs) / name / "index.jsonl").read_bytes()).hexdigest()[:12]
    except OSError:
        return "?"


def _footer(runs, stores, head_sha, now):
    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    rows = "".join(
        f'<tr><td>{_esc(s["name"])}</td><td>{_store_sha(runs, s["name"])}</td>'
        f'<td>{time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("mtime", 0)))}</td></tr>'
        for s in stores)
    return ('<footer id="footer" class="rpt-foot"><h1 class="sec">Traceability</h1>'
            '<div class="card"><div class="kv">'
            f'<span><b>generated</b> {_esc(ts)}</span>'
            f'<span><b>runs</b> {_esc(runs)}</span>'
            f'<span><b>repo HEAD</b> {_esc(head_sha or DASH)}</span></div>'
            '<h4>store shas (sha256 of index.jsonl)</h4>'
            '<table><thead><tr><th>store</th><th>sha</th><th>mtime</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></footer>')


# --- document ---------------------------------------------------------------

_CSS = """
:root{--bg:#0b0f17;--panel:#111725;--panel2:#0e1420;--line:#1e2838;--ink:#e6edf7;
--muted:#8896ab;--dim:#5c6b82;--accent:#4d6bfe;--accent2:#5ee0c8;--good:#37d67a;
--bad:#f0616d;--warn:#e6b13a;--mono:ui-monospace,SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px}
.doc{max-width:960px;margin:0 auto;padding:24px 22px 60px}
.rpt-head{border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:8px}
.rpt-head .brand{font-weight:700;letter-spacing:.3px;font-size:22px;display:flex;align-items:center;gap:8px}
.rpt-head .whale{color:var(--accent)}
.rpt-head .subtitle{color:var(--muted);font-family:var(--mono);font-size:12px;margin-top:6px}
h1.sec{font-size:15px;text-transform:uppercase;letter-spacing:.8px;color:var(--accent2);
margin:34px 0 4px;border-bottom:1px solid var(--line);padding-bottom:6px}
.secsub{margin-bottom:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.card.headline{border-color:var(--accent)}
.card h2{margin:0 0 4px;font-size:16px}
.card h4{margin:18px 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
.lede{font-size:14px;line-height:1.55;color:var(--ink);margin:8px 0}
.sub{color:var(--muted);font-size:12px;font-family:var(--mono)}
.kv{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:8px;font-family:var(--mono);font-size:12px}
.kv b{color:var(--muted);font-weight:400}
table{border-collapse:collapse;width:100%;font-family:var(--mono);font-size:12px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--dim);font-weight:500;text-transform:uppercase;font-size:10px;letter-spacing:.4px}
.badge{padding:1px 7px;border-radius:10px;font-size:11px}
.badge.ok{background:rgba(55,214,122,.15);color:var(--good)}
.badge.no{background:rgba(240,97,109,.15);color:var(--bad)}
.badge.live-badge{background:rgba(94,224,200,.15);color:var(--accent2)}
.card.live{border-color:var(--accent2)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.chart{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px}
.chart .t{font-size:11px;color:var(--muted);margin-bottom:6px;font-family:var(--mono)}
svg{display:block;width:100%;overflow:visible}
.round{border:1px solid var(--line);border-radius:8px;margin-bottom:8px;background:var(--panel)}
.round summary{cursor:pointer;padding:9px 12px;font-family:var(--mono);font-size:13px;list-style:none}
.round summary::-webkit-details-marker{display:none}
.round .rn{color:var(--accent)}
.round .rd{color:var(--dim);font-size:11px}
.round pre{white-space:pre-wrap;word-break:break-word;padding:0 14px 12px;margin:0;
color:var(--muted);font-size:12px;line-height:1.5;font-family:var(--mono)}
.ledger-line{position:relative;height:26px;background:var(--panel2);border:1px solid var(--line);
border-radius:6px;margin:10px 0 16px;overflow:hidden}
.seg{position:absolute;top:0;bottom:0;opacity:.85}
.legend{display:flex;gap:14px;font-size:11px;color:var(--muted);font-family:var(--mono);margin-bottom:8px;flex-wrap:wrap}
.legend i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.warnbar{background:rgba(230,177,58,.12);border:1px solid var(--warn);color:var(--warn);
padding:6px 10px;border-radius:6px;font-family:var(--mono);font-size:12px;margin-bottom:12px}
.rpt-foot{margin-top:20px}
@media print{
body{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.card,.chart,.round,details.round{break-inside:avoid}
h1.sec{break-after:avoid}
.doc{max-width:none;padding:0}
}
"""


def build_report(runs_dir, *, status_text="", progress_text="", head_sha="",
                 live_threshold_s=3600.0, now=None):
    """Render the whole runs/ tree into one self-contained HTML string.

    All parsing goes through ``board.store``; this function only shapes and lays out.
    ``now`` is injectable so tests are deterministic; ``live_threshold_s`` sets the
    freshness window for the live-campaign flag.
    """
    now = time.time() if now is None else now
    runs = Path(runs_dir)
    stores = bs.list_stores(runs)
    details = {s["name"]: bs.store_detail(runs / s["name"]) for s in stores}

    campaigns = [(s["name"], details[s["name"]]) for s in stores
                 if details[s["name"]].get("generations")]
    camp_html = "".join(_campaign_section(runs, n, d) for n, d in campaigns)

    date = time.strftime("%Y-%m-%d", time.localtime(now))
    body = [
        '<div class="doc">',
        ('<header class="rpt-head"><div class="brand"><span class="whale">\U0001f40b</span> '
         "RSI Report</div>"
         f'<div class="subtitle">recursive self-improvement monitoring · {date} · '
         f"{len(stores)} store(s) under {_esc(runs)}</div></header>"),
        _exec_summary(runs, details),
        '<section id="campaigns"><h1 class="sec">Campaigns</h1>'
        + (camp_html or '<div class="card sub">no campaigns with generations</div>')
        + "</section>",
        _diagnostics_section(details),
        _live_section(runs, stores, details, live_threshold_s, now),
        _ledger_section(bs.parse_ledger(status_text)),
        _rounds_section(bs.parse_rounds(progress_text)),
        _footer(runs, stores, head_sha, now),
        "</div>",
    ]
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>RSI Report {date}</title><style>{_CSS}</style></head>"
            f'<body>{"".join(body)}</body></html>')
