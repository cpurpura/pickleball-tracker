"""HTML report for tee sessions: the latest session rep by rep, plus trends across sessions."""
import base64
import html
import json
from pathlib import Path

import numpy as np

from .report import CSS, _img, _med, _style_ax, matplotlib, plt

INK_1, INK_2 = "#2a78d6", "#eb6834"

COLS = [  # key, label, help, format
    ("ahead_of_front_foot_in", "Ahead of front foot (in)", "where the ball was vs. your front foot; + = in front", "{:+.1f}"),
    ("ahead_of_hips_in", "Ahead of hips (in)", "where the ball was vs. the middle of your hips", "{:+.1f}"),
    ("contact_height", "Contact ht", "ball height on your body: 0 = hips, 1 = shoulders", "{:.2f}"),
    ("hand_speed_mph", "Hand mph", "paddle-hand speed just before contact", "{:.1f}"),
    ("launch_speed_mph", "Launch mph", "ball speed off the paddle (video)", "{:.1f}"),
    ("launch_angle_deg", "Launch °", "ball direction off the paddle; + = rising", "{:+.1f}"),
    ("board_speed_mph", "To board mph", "average speed from tee to board (sound)", "{:.1f}"),
    ("knee_angle", "Knee °", "180 = straight leg", "{:.0f}"),
    ("hip_drop_pct", "Hip drop %", "how much lower your hips are vs. standing tall", "{:.0f}"),
    ("elbow_angle", "Elbow °", "paddle arm at contact", "{:.0f}"),
    ("feet_set_s", "Feet set (s)", "how long your feet had been planted before contact; 0 = still moving", "{:.2f}"),
    ("moved_ft", "Moved (ft)", "how far you traveled to the ball in the 1.5 s before contact", "{:.1f}"),
    ("stance_in", "Stance (in)", "distance between your ankles at contact", "{:.0f}"),
    ("stagger_in", "Stagger (in)", "how far one foot was ahead of the other along the hitting line", "{:.0f}"),
    ("head_over_base_in", "Head over feet (in)", "head vs. the middle of your feet; + = toward the target", "{:+.1f}"),
]


def val(rep, k):
    return rep.get("metrics", {}).get(k, rep.get(k))


def _fmt(v, f):
    return "" if v is None else f.format(v)


def _spread(vals):
    v = [x for x in vals if x is not None]
    return float(np.std(v)) if len(v) >= 3 else None


def summary_table(reps):
    rows = []
    for k, label, tip, f in COLS:
        vals = [val(r, k) for r in reps]
        n = sum(v is not None for v in vals)
        if not n:
            continue
        sp = _spread(vals)
        rows.append(f"<tr><td title='{html.escape(tip)}'>{label}</td><td><b>{_fmt(_med(vals), f)}</b></td>"
                    f"<td>{'' if sp is None else f'± {sp:.1f}'}</td><td class=muted>{n}/{len(reps)}</td></tr>")
    return ("<table><tr><th>Measure</th><th>Median</th><th>Spread (1 SD)</th><th>Reps measured</th></tr>"
            + "".join(rows) + "</table><p class=muted>Smaller spread = more repeatable. Hover a measure for what it means.</p>")


def flags(reps, cfg):
    c = cfg["tee"]["coaching"]
    out = []
    ahead = [val(r, "ahead_of_front_foot_in") for r in reps if val(r, "ahead_of_front_foot_in") is not None]
    if ahead:
        late = sum(a < c["min_ahead_of_front_foot_in"] for a in ahead) / len(ahead)
        cls = "warn" if late > 0.25 else "good"
        out.append((cls, f"Ball was behind your front foot on {late:.0%} of reps "
                         f"(median {np.median(ahead):+.1f} in). Contact out in front gives you control and pace."))
        sd = _spread(ahead)
        if sd is not None:
            cls = "warn" if sd > c["max_contact_spread_in"] else "good"
            out.append((cls, f"Contact point varies by ± {sd:.1f} in from rep to rep "
                             f"(aim for under {c['max_contact_spread_in']} in)."))
    feet = [val(r, "feet_set_s") for r in reps if val(r, "feet_set_s") is not None]
    if feet:
        moving = sum(f < c["min_feet_set_s"] for f in feet) / len(feet)
        out.append(("warn" if moving > 0.25 else "good",
                    f"Feet still moving at contact on {moving:.0%} of reps (median set {np.median(feet):.2f} s before contact)."))
    head = [val(r, "head_over_base_in") for r in reps if val(r, "head_over_base_in") is not None]
    if head:
        reach = sum(h > c["max_head_forward_in"] for h in head) / len(head)
        if reach > 0.25:
            out.append(("warn", f"Head was more than {c['max_head_forward_in']} in past the middle of your feet on "
                                f"{reach:.0%} of reps: you may be reaching instead of moving your feet."))
    pops = [r for r in reps if r.get("audio")]
    board = [r for r in pops if r.get("board_speed_mph")]
    if pops and cfg["tee"]["target"] == "board" and len(board) < 0.5 * len(pops):
        out.append(("info", f"Board speed was measured on only {len(board)}/{len(pops)} reps. Check board_distance_ft, "
                            "or the board's pop may be too quiet (see tune-audio)."))
    if not out:
        return ""
    icon = {"good": "✓", "warn": "!", "info": "i"}
    return "<ul class=flags>" + "".join(f"<li class={k}><span class=ic>{icon[k]}</span>{html.escape(t)}</li>"
                                         for k, t in out) + "</ul>"


def chart_reps(reps):
    series = [(k, l) for k, l in (("launch_speed_mph", "Launch mph"), ("board_speed_mph", "To board mph"),
                                  ("hand_speed_mph", "Hand mph")) if any(val(r, k) is not None for r in reps)]
    ahead = any(val(r, "ahead_of_front_foot_in") is not None for r in reps)
    if not series and not ahead:
        return ""
    n = int(bool(series)) + int(ahead)
    fig, axes = plt.subplots(n, 1, figsize=(8, 2.4 * n), sharex=True, squeeze=False)
    x = np.arange(1, len(reps) + 1)
    i = 0
    if series:
        ax = axes[i][0]
        for (k, l), col in zip(series, (INK_1, INK_2, "#7a5af5")):
            y = [np.nan if val(r, k) is None else val(r, k) for r in reps]
            ax.plot(x, y, "o-", ms=4, lw=1.5, color=col, label=l)
        ax.set_ylabel("mph", fontsize=9)
        ax.legend(frameon=False, fontsize=8, loc="lower left")
        _style_ax(ax)
        i += 1
    if ahead:
        ax = axes[i][0]
        y = [np.nan if val(r, "ahead_of_front_foot_in") is None else val(r, "ahead_of_front_foot_in") for r in reps]
        ax.axhline(0, color="#999", lw=1)
        ax.plot(x, y, "o-", ms=4, lw=1.5, color=INK_1)
        ax.set_ylabel("in front of\nfront foot (in)", fontsize=9)
        _style_ax(ax)
    axes[-1][0].set_xlabel("rep", fontsize=9)
    axes[-1][0].xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    return _img(fig)


def thumbs(reps, base):
    cells = []
    for i, r in enumerate(reps):
        p = base / r["thumb"] if r.get("thumb") else None
        if not p or not p.exists():
            continue
        b64 = base64.b64encode(p.read_bytes()).decode()
        cap = " · ".join(s for s in (
            _fmt(val(r, "ahead_of_front_foot_in"), "{:+.1f} in"),
            _fmt(val(r, "launch_speed_mph") or val(r, "board_speed_mph"), "{:.0f} mph"),
            _fmt(val(r, "launch_angle_deg"), "{:+.0f}°")) if s)
        cells.append(f"<figure><img src='data:image/jpeg;base64,{b64}' loading=lazy>"
                     f"<figcaption>#{i + 1} {html.escape(cap)}</figcaption></figure>")
    if not cells:
        return ""
    return ("<details open><summary>Contact frame of every rep (yellow = ball, green = front foot)</summary>"
            f"<div class=thumbs>{''.join(cells)}</div></details>")


def rep_table(reps):
    head = "".join(f"<th title='{html.escape(t)}'>{l}</th>" for _, l, t, _ in COLS)
    rows = [f"<tr><td>{i + 1}</td><td>{int(r['t'] // 60)}:{r['t'] % 60:05.2f}</td>"
            + "".join(f"<td>{_fmt(val(r, k), f)}</td>" for k, _, _, f in COLS) + "</tr>"
            for i, r in enumerate(reps)]
    return f"<details><summary>Every rep</summary><table><tr><th>#</th><th>Time</th>{head}</tr>{''.join(rows)}</table></details>"


def trends(sessions):
    rows = []
    for path, d in sessions:
        reps = d["reps"]
        cells = "".join(f"<td>{_fmt(_med([val(r, k) for r in reps]), f)}</td>" for k, _, _, f in COLS[:7])
        sd = _spread([val(r, "ahead_of_front_foot_in") for r in reps])
        rows.append(f"<tr><td>{html.escape(Path(path).name.replace('.tee.json', ''))}</td><td>{html.escape(d['shot'])}</td>"
                    f"<td>{_fmt(d.get('tee_height_in'), '{:g}')}</td><td>{len(reps)}</td>{cells}"
                    f"<td>{'' if sd is None else f'± {sd:.1f}'}</td></tr>")
    head = "".join(f"<th>{l}</th>" for _, l, _, _ in COLS[:7])
    return (f"<table><tr><th>Session</th><th>Shot</th><th>Tee (in)</th><th>Reps</th>{head}<th>Contact spread</th></tr>"
            + "".join(rows) + "</table><p class=muted>Medians per session, oldest first.</p>")


TEE_CSS = """
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:10px}
figure{margin:0}figure img{width:100%;aspect-ratio:3/4;object-fit:contain;background:#222;display:block}figcaption{font-size:.8em;color:var(--muted);padding-top:2px}
.scroll{overflow-x:auto}
"""


def build_tee_report(files, cfg, out_path):
    sessions = []
    for p in files:
        with open(p) as f:
            sessions.append((p, json.load(f)))
    sessions.sort(key=lambda s: Path(s[0]).stat().st_mtime)
    path, last = sessions[-1]
    reps = last["reps"]
    base = Path(path).parent
    setup = " · ".join(s for s in (
        html.escape(last["shot"]),
        f"tee {last['tee_height_in']:g} in" if last.get("tee_height_in") else "",
        f"into a board {last['board_distance_ft']:g} ft away" if last.get("board_distance_ft") else f"into a {last['target']}",
        "" if last.get("audio") else "no sound - reps found from video only") if s)
    parts = [f"<h1>Tee practice</h1><p class=muted>{len(sessions)} session(s) · latest: "
             f"{html.escape(Path(path).name.replace('.tee.json', ''))}</p>",
             f"<section><h2>Latest session</h2><p>{len(reps)} reps · {setup}</p>{flags(reps, cfg)}"
             f"<div class=scroll>{summary_table(reps)}</div></section>"]
    if reps:
        parts.append(f"<section><h2>Rep by rep</h2>{chart_reps(reps)}{thumbs(reps, base)}"
                     f"<div class=scroll>{rep_table(reps)}</div></section>")
    if len(sessions) > 1:
        parts.append(f"<section><h2>Trends across sessions</h2><div class=scroll>{trends(sessions)}</div></section>")
    parts.append("<p class=muted>Single camera and 2D pose: compare numbers between your own sessions; don't treat "
                 "them as exact measurements. Inch measurements use the 2.9 in ball as a ruler.</p>")
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Tee Practice Report</title><style>{CSS}{TEE_CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")
    Path(out_path).write_text(doc, encoding="utf-8")
    print(f"Report -> {out_path}")
    return out_path
