"""Shots file (JSON) read/write, plus the analyze orchestration."""
import json
import math
from pathlib import Path

from .classify import ShotClassifier
from .detect import load_tracks
from .events import detect_events
from .mechanics import compute_all


def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not (isinstance(v, float) and math.isnan(v))}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def save_shots(data, path):
    with open(path, "w") as f:
        json.dump(_clean(data), f, indent=1)


def load_shots(path):
    with open(path) as f:
        return json.load(f)


def players_from_cfg(cfg, near_name=None, far_name=None):
    p = cfg["players"]
    names = {"near": near_name or p["near"], "far": far_name or p["far"]}
    return {s: {"name": names[s], "hand": p.get("handedness", {}).get(names[s], "right")} for s in names}


def build_shots(tracks_path, cfg, players, model_path, out_path):
    tr = load_tracks(tracks_path)
    onsets = None
    if cfg.get("audio", {}).get("enabled"):
        from .audio import video_onsets
        meta = tr["meta"]
        onsets = video_onsets(meta["video"], cfg["audio"], meta["frames"] / meta["fps"])
    hits, bounces = detect_events(tr, cfg, players, onsets)
    compute_all(tr, hits, players)
    clf = ShotClassifier(model_path, cfg["classify"])
    clf.predict(hits)
    for h in hits:
        h["player"] = players[h["side"]]["name"]
        h.setdefault("type", None)       # your label (set in review)
        h.setdefault("deleted", False)
    data = {"video": tr["meta"]["video"], "tracks": str(tracks_path), "fps": tr["meta"]["fps"],
            "players": players, "classifier": clf.mode, "hits": hits, "bounces": bounces}
    save_shots(data, out_path)
    msg = f"  {len(hits)} hits, {len(bounces)} bounces detected (shot types via {clf.mode})"
    if onsets is not None:
        quiet = sum(1 for h in hits if h.get("audio") is False)
        msg += f"; {len(hits) - quiet} confirmed by sound, {quiet} with no pop (probably bounces)"
    print(f"{msg} -> {out_path}")
    return data


def rebuild_metrics(shots_path, cfg, model_path):
    """After review edits (moved/added hits), recompute metrics + auto types, keeping your labels."""
    data = load_shots(shots_path)
    tr = load_tracks(data["tracks"])
    players = data["players"]
    hits = [h for h in data["hits"] if not h.get("deleted")]
    hits.sort(key=lambda h: h["frame"])
    from .events import _assign_rallies, _attach_landings
    fps = data["fps"]
    _assign_rallies(hits, fps, cfg["events"]["rally_gap_s"])
    manual_volley = {id(h): h["volley"] for h in hits if h.get("volley_manual")}
    _attach_landings(hits, data["bounces"], tr, fps)
    for h in hits:
        if id(h) in manual_volley:
            h["volley"] = manual_volley[id(h)]
    compute_all(tr, hits, players)
    clf = ShotClassifier(model_path, cfg["classify"])
    clf.predict(hits)
    data["classifier"] = clf.mode
    for i, h in enumerate(hits):
        h["id"] = i
        h["t"] = round(h["frame"] / fps, 3)
        h["player"] = players[h["side"]]["name"]
    data["hits"] = hits
    save_shots(data, shots_path)
    return data
