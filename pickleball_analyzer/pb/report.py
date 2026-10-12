"""HTML report: shot mix, placement, body mechanics, coaching flags, and trends across sessions."""
import base64
import html
import io
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker  # noqa: F401
import numpy as np

from .classify import SHOT_TYPES
from .court import draw_court_mpl, NET_Y
from .session import load_shots

PLAYER_COLORS = ["#2a78d6", "#eb6834"]  # categorical slots 1-2
INK, MUTED = "#1f2328", "#6b7280"

MECH_COLS = [
    ("contact_height", "Contact ht", "0 = hip, 1 = shoulder"),
    ("knee_angle", "Knee °", "180 = straight leg (camera behind you understates bend)"),
    ("hip_drop_pct", "Hip drop %", "how much lower your hips are vs. standing tall"),
    ("elbow_angle", "Elbow °", "paddle arm at contact"),
    ("stance_width", "Stance", "feet width ÷ shoulder width"),
    ("ready_paddle_height", "Ready ht", "paddle height when opponent hit"),
    ("dist_from_net_ft", "From net ft", "where you hit from"),
    ("speed_fts", "Speed ft/s", "rough average ball speed"),
]


def shot_type(h):
    return h.get("type") or h.get("type_auto") or "other"


def _img(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return f'<img src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}">'


def _style_ax(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#d0d7de")
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="x", color="#eef0f2", lw=0.8)
    ax.set_axisbelow(True)


def chart_shot_mix(by_player, names):
    types = [t for t in SHOT_TYPES if any(Counter(shot_type(h) for h in by_player[n])[t] for n in names)]
    if not types:
        return ""
    fig, ax = plt.subplots(figsize=(7, 0.45 * len(types) + 1))
    y = np.arange(len(types))
    bh = 0.8 / len(names)
    for i, n in enumerate(names):
        c = Counter(shot_type(h) for h in by_player[n])
        vals = [c[t] for t in types]
        ax.barh(y + i * bh - 0.4 + bh / 2, vals, height=bh - 0.04, color=PLAYER_COLORS[i % 2], label=n)
        for yy, v in zip(y + i * bh - 0.4 + bh / 2, vals):
            if v:
                ax.text(v, yy, f" {v}", va="center", fontsize=8, color=MUTED)
    ax.set_yticks(y, types)
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.invert_yaxis()
    _style_ax(ax)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.set_xlabel("shots", color=MUTED, fontsize=9)
    return _img(fig)


def chart_placement(hits, color, title):
    """Small multiples: one top-down court per shot type, dots where the ball landed."""
    groups = defaultdict(list)
    for h in hits:
        if h.get("landing_xy"):
            groups[shot_type(h)].append(h)
    types = [t for t in SHOT_TYPES if groups.get(t)]
    if not types:
        return "<p class='muted'>No landing spots detected yet for this player.</p>"
    cols = min(len(types), 5)
    rows = int(np.ceil(len(types) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(2.3 * cols, 4.4 * rows), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for ax, t in zip(axes.flat, types):
        ax.axis("on")
        draw_court_mpl(ax)
        pts = np.array([h["landing_xy"] for h in groups[t]])
        # show every shot landing on the FAR half so all panels read the same way
        pts[:, 1] = np.where(pts[:, 1] < NET_Y, 2 * NET_Y - pts[:, 1], pts[:, 1])
        pts[:, 0] = np.where(np.array([h["side"] == "far" for h in groups[t]]), 20 - pts[:, 0], pts[:, 0])
        ax.scatter(pts[:, 0], pts[:, 1], s=36, c=color, edgecolors="white", linewidths=1.2, zorder=3)
        ax.set_ylim(NET_Y - 3, 48)
        ax.set_title(f"{t}  (n={len(pts)})", fontsize=10, color=INK)
    fig.suptitle(title, fontsize=11, color=INK, y=1.0)
    fig.tight_layout()
    return _img(fig)


def chart_hit_positions(hits, color):
    """Where on your side of the court you were when you hit, by shot type."""
    pts = [(h["metrics"]["court_x"], h["metrics"]["court_y"], shot_type(h)) for h in hits
           if h.get("metrics", {}).get("court_x") is not None]
    if not pts:
        return ""
    fig, ax = plt.subplots(figsize=(3.2, 4.2))
    draw_court_mpl(ax)
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    far = ys > NET_Y
    xs[far] = 20 - xs[far]; ys[far] = 2 * NET_Y - ys[far]  # normalise to near half
    ax.scatter(xs, ys, s=22, c=color, alpha=0.75, edgecolors="white", linewidths=0.8, zorder=3)
    ax.set_ylim(-6, NET_Y + 1)
    ax.set_title("Where you hit from", fontsize=10, color=INK)
    return _img(fig)


def _med(vals):
    v = [x for x in vals if x is not None and not (isinstance(x, float) and np.isnan(x))]
    return float(np.median(v)) if v else None


def mechanics_table(hits):
    by_type = defaultdict(list)
    for h in hits:
        by_type[shot_type(h)].append(h)
    head = "".join(f"<th title='{html.escape(d)}'>{l}</th>" for _, l, d in MECH_COLS)
    rows = []
    for t in SHOT_TYPES:
        hs = by_type.get(t)
        if not hs:
            continue
        fh = sum(1 for h in hs if h.get("metrics", {}).get("wing") == "forehand")
        bh = sum(1 for h in hs if h.get("metrics", {}).get("wing") == "backhand")
        cells = []
        for k, _, _ in MECH_COLS:
            m = _med([h.get("metrics", {}).get(k) for h in hs])
            fmt = "{:.2f}" if k in ("contact_height", "stance_width", "ready_paddle_height") else "{:.0f}"
            cells.append(f"<td>{'' if m is None else fmt.format(m)}</td>")
        rows.append(f"<tr><td><b>{t}</b></td><td>{len(hs)}</td><td>{fh}/{bh}</td>{''.join(cells)}</tr>")
    return (f"<table><tr><th>Shot</th><th>n</th><th>FH/BH</th>{head}</tr>{''.join(rows)}</table>"
            "<p class='muted'>Medians. Hover a column header for what it means.</p>")


def coaching_flags(hits, cfg):
    c = cfg["coaching"]
    flags = []
    by_type = defaultdict(list)
    for h in hits:
        by_type[shot_type(h)].append(h)

    def pct(xs, cond):
        xs = [x for x in xs if x is not None]
        return (100 * sum(1 for x in xs if cond(x)) / len(xs), len(xs)) if xs else (None, 0)

    dinks = by_type.get("dink", [])
    p, n = pct([h["metrics"].get("landed_in_kitchen") for h in dinks if h.get("landing_xy")], lambda x: x)
    if n >= 5:
        flags.append(("good" if p >= c["dink_in_kitchen_target_pct"] else "warn",
                      f"Dinks landing in the kitchen: {p:.0f}% (n={n}; target ≥{c['dink_in_kitchen_target_pct']}%)."))
    drops = by_type.get("drop", []) + by_type.get("reset", [])
    p, n = pct([h["metrics"].get("landing_from_net_ft") for h in drops], lambda x: x <= 10)
    if n >= 5:
        flags.append(("good" if p >= c["drop_in_kitchen_target_pct"] else "warn",
                      f"Drops/resets landing within 10 ft of the net: {p:.0f}% (n={n}; target ≥{c['drop_in_kitchen_target_pct']}%)."))
    low = [h["metrics"].get("hip_drop_pct") for h in dinks + drops]
    m = _med(low)
    if m is not None and len([x for x in low if x is not None]) >= 5 and m < c["min_hip_drop_pct_soft_game"]:
        flags.append(("warn", f"On dinks/drops your hips only drop about {m:.0f}% from standing height — "
                              "get lower with your legs instead of bending at the waist or reaching with the arm."))
    lean = _med([h["metrics"].get("torso_lean_deg") for h in dinks + drops])
    if lean is not None and lean > c["max_torso_lean_deg"]:
        flags.append(("warn", f"Median torso lean on soft shots is {lean:.0f}° — that often means bending at the waist."))
    ready = [h["metrics"].get("ready_paddle_height") for h in hits]
    p, n = pct(ready, lambda x: x < c["ready_min_paddle_height"])
    if n >= 5:
        flags.append(("warn" if p > 30 else "good",
                      f"Paddle below ready height when your opponent hit: {p:.0f}% of the time (n={n})."))
    trans = [h["metrics"].get("ft_behind_kitchen") for h in hits if shot_type(h) not in ("serve", "return")]
    p, n = pct(trans, lambda x: 3 <= x <= 11)
    if n >= 10:
        flags.append(("info", f"{p:.0f}% of rally shots hit from the transition zone (3–11 ft behind the kitchen line)."))
    returns = by_type.get("return", [])
    p, n = pct([h["metrics"].get("landing_from_net_ft") for h in returns], lambda x: x >= 15)
    if n >= 3:
        flags.append(("good" if p >= 60 else "warn", f"Deep returns (landing ≥15 ft from net): {p:.0f}% (n={n})."))
    reach = _med([h["metrics"].get("reach") for h in hits])
    if reach is not None and reach > c["max_reach"]:
        flags.append(("warn", "You're often reaching away from your body at contact — move your feet to the ball."))
    if not flags:
        return "<p class='muted'>Not enough tagged shots yet for coaching flags.</p>"
    icon = {"good": "✓", "warn": "!", "info": "i"}
    return "<ul class='flags'>" + "".join(
        f"<li class='{k}'><span class='ic'>{icon[k]}</span>{html.escape(t)}</li>" for k, t in flags) + "</ul>"


def trends_table(sessions, names):
    rows = []
    for path, data in sessions:
        for n in names:
            hs = [h for h in data["hits"] if not h.get("deleted") and h["player"] == n]
            if not hs:
                continue
            dinks = [h for h in hs if shot_type(h) == "dink" and h.get("landing_xy")]
            dk = f"{100 * sum(1 for h in dinks if h['metrics'].get('landed_in_kitchen')) / len(dinks):.0f}%" if dinks else ""
            hd = _med([h["metrics"].get("hip_drop_pct") for h in hs if shot_type(h) in ("dink", "drop", "reset")])
            rd = _med([h["metrics"].get("ready_paddle_height") for h in hs])
            rows.append(f"<tr><td>{html.escape(Path(path).stem.replace('.shots', ''))}</td><td>{html.escape(n)}</td>"
                        f"<td>{len(hs)}</td><td>{dk}</td><td>{'' if hd is None else f'{hd:.0f}%'}</td>"
                        f"<td>{'' if rd is None else f'{rd:.2f}'}</td></tr>")
    return ("<table><tr><th>Session</th><th>Player</th><th>Shots</th><th>Dinks in kitchen</th>"
            "<th>Hip drop (soft shots)</th><th>Ready ht</th></tr>" + "".join(rows) + "</table>")


def shot_list(hits):
    rows = []
    for h in hits:
        m = h.get("metrics", {})
        mm, ss = divmod(h["t"], 60)
        rows.append(f"<tr><td>{int(mm)}:{ss:05.2f}</td><td>{html.escape(h['player'])}</td><td>{shot_type(h)}"
                    f"{'' if h.get('type') else ' <span class=muted>(auto)</span>'}</td><td>{m.get('wing', '')}</td>"
                    f"<td>{'yes' if h.get('volley') else ''}</td><td>{m.get('dist_from_net_ft', '')}</td>"
                    f"<td>{m.get('landing_from_net_ft', '')}</td><td>{m.get('speed_fts', '')}</td></tr>")
    return ("<details><summary>All shots</summary><table><tr><th>Time</th><th>Player</th><th>Type</th><th>Wing</th>"
            "<th>Volley</th><th>From net</th><th>Landed from net</th><th>Speed</th></tr>" + "".join(rows) + "</table></details>")


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1f2328;--muted:#6b7280;--line:#e5e7eb;--good:#0a7d32;--warn:#b54708;--info:#2a78d6}
@media (prefers-color-scheme: dark){:root{--bg:#111418;--card:#1a1e24;--ink:#e6e8eb;--muted:#9aa3ad;--line:#2b313a;--good:#4ec27a;--warn:#f0a35e;--info:#6aa8f0}}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:24px;line-height:1.45}
main{max-width:1100px;margin:auto}h1{margin:0 0 4px}h2{margin-top:0}.muted{color:var(--muted);font-size:.9em}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0}
img{max-width:100%;border-radius:8px;background:#fff}.row{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start}
table{border-collapse:collapse;width:100%;font-size:.9em;margin:8px 0}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:600}ul.flags{list-style:none;padding:0}ul.flags li{padding:6px 0;display:flex;gap:10px}
.ic{display:inline-flex;width:20px;height:20px;border-radius:50%;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;flex:none}
.good .ic{background:var(--good)}.warn .ic{background:var(--warn)}.info .ic{background:var(--info)}
details summary{cursor:pointer;color:var(--muted)}.pill{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
"""


def build_report(shot_files, cfg, out_path):
    sessions = [(p, load_shots(p)) for p in shot_files]
    names = []
    for _, d in sessions:
        for s in ("near", "far"):
            n = d["players"][s]["name"]
            if n not in names:
                names.append(n)
    all_hits = [h for _, d in sessions for h in d["hits"] if not h.get("deleted")]
    by_player = {n: [h for h in all_hits if h["player"] == n] for n in names}
    labelled = sum(1 for h in all_hits if h.get("type"))

    parts = [f"<h1>Pickleball shot breakdown</h1><p class='muted'>{len(sessions)} session(s) · {len(all_hits)} shots · "
             f"{labelled} tagged by you · shot types on untagged shots are automatic guesses</p>"]
    parts.append(f"<section><h2>Shot mix</h2>{chart_shot_mix(by_player, names)}</section>")
    for i, n in enumerate(names):
        hs = by_player[n]
        if not hs:
            continue
        col = PLAYER_COLORS[i % 2]
        parts.append(
            f"<section><h2><span class='pill' style='background:{col}'></span>{html.escape(n)}</h2>"
            f"<h3>Coaching flags</h3>{coaching_flags(hs, cfg)}"
            f"<h3>Body mechanics at contact</h3>{mechanics_table(hs)}"
            f"<h3>Placement</h3><div class='row'>{chart_hit_positions(hs, col)}"
            f"<div style='flex:1;min-width:300px'>{chart_placement(hs, col, 'Where your shots landed (opponent side)')}</div></div>"
            f"</section>")
    if len(sessions) > 1:
        parts.append(f"<section><h2>Trends across sessions</h2>{trends_table(sessions, names)}</section>")
    parts.append(f"<section>{shot_list(sorted(all_hits, key=lambda h: (h['player'], h['t'])))}</section>")
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Pickleball Shot Report</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")
    Path(out_path).write_text(doc, encoding="utf-8")
    print(f"Report -> {out_path}")
    return out_path
