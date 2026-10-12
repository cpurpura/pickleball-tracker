"""Pass 2: turn raw tracks into hits (who hit, when) and bounces (where it landed)."""
import numpy as np

from .audio import SPEED_OF_SOUND_FTS
from .court import NET_Y, COURT_W, COURT_L

SIDES = ("near", "far")
WRISTS = (9, 10)


def _smooth(traj, k=2):
    """Moving-average smoothing that respects NaN gaps."""
    out = traj.copy()
    n = len(traj)
    for i in range(n):
        if np.isnan(traj[i, 0]):
            continue
        a, b = max(0, i - k), min(n, i + k + 1)
        seg = traj[a:b]
        seg = seg[~np.isnan(seg[:, 0])]
        out[i] = seg.mean(axis=0)
    return out


def direction_change(traj, k):
    """Angle (deg) between incoming and outgoing ball velocity at each frame."""
    n = len(traj)
    ang = np.zeros(n)
    speed_in = np.zeros(n)
    speed_out = np.zeros(n)
    for i in range(k, n - k):
        a, b, c = traj[i - k], traj[i], traj[i + k]
        if np.isnan(a[0]) or np.isnan(b[0]) or np.isnan(c[0]):
            continue
        v1, v2 = b - a, c - b
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        speed_in[i], speed_out[i] = n1 / k, n2 / k
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        ang[i] = np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1, 1)))
    return ang, speed_in, speed_out


def _local_peaks(sig, thresh, min_sep):
    idx = []
    i, n = 0, len(sig)
    while i < n:
        if sig[i] >= thresh:
            j = i
            while j + 1 < n and sig[j + 1] >= thresh:
                j += 1
            seg = sig[i:j + 1]
            idx.append(i + int(np.argmax(seg)))
            i = j + 1
        else:
            i += 1
    # enforce separation, keeping the stronger peak
    out = []
    for p in idx:
        if out and p - out[-1] < min_sep:
            if sig[p] > sig[out[-1]]:
                out[-1] = p
        else:
            out.append(p)
    return out


def _side_dist(tr, s, f, pt):
    """Distance from ball to side s's paddle area at frame f, in body-heights (inf if unknown)."""
    if not (0 <= f < len(tr[f"kp_{s}"])) or np.isnan(pt[0]):
        return np.inf
    kp, box = tr[f"kp_{s}"][f], tr[f"box_{s}"][f]
    if np.isnan(box[0]):
        return np.inf
    bh = max(box[3] - box[1], 1.0)
    best = np.inf
    for wi in WRISTS:
        if not np.isnan(kp[wi, 0]) and kp[wi, 2] >= 0.2:
            best = min(best, np.hypot(*(kp[wi, :2] - pt)) / bh)
    # fall back to box distance only if the wrists weren't found -- and ignore the
    # bottom of the box so a bounce at the player's feet isn't called a hit
    if best == np.inf and pt[1] <= box[3] - 0.15 * bh:
        cx = np.clip(pt[0], box[0], box[2])
        cy = np.clip(pt[1], box[1], box[3])
        best = min(best, np.hypot(pt[0] - cx, pt[1] - cy) / bh + 0.3)
    return best


def _nearest_player(tr, f, pt, search=3):
    """Return (side, normalized distance) of the player closest to the ball at frame f."""
    best = (None, np.inf)
    for s in SIDES:
        for df in range(search + 1):
            d = min(_side_dist(tr, s, f - df, pt), _side_dist(tr, s, f + df, pt))
            if d < np.inf:
                if d < best[1]:
                    best = (s, d)
                break
    return best


def _refine_contact(tr, ball, s, f, win):
    """Move a hit to the frame where the ball is closest to that player's paddle hand."""
    best_f, best_d = f, _side_dist(tr, s, f, ball[f])
    for ff in range(max(0, f - win), min(len(ball), f + win + 1)):
        d = _side_dist(tr, s, ff, ball[ff])
        if d < best_d - 1e-9:
            best_f, best_d = ff, d
    return best_f, best_d


def wrist_speed(tr, side, paddle_wrist, fps):
    kp = tr[f"kp_{side}"]
    box = tr[f"box_{side}"]
    bh = np.maximum(box[:, 3] - box[:, 1], 1.0)
    w = kp[:, paddle_wrist, :2]
    v = np.full(len(w), np.nan)
    v[1:] = np.hypot(*(np.diff(w, axis=0)).T) / bh[1:] * fps  # body-heights per second
    return v


def detect_events(tr, cfg, players, onsets=None):
    fps = tr["meta"]["fps"]
    ev = cfg["events"]
    court = tr["court"]
    ball = _smooth(tr["ball"], 1)
    k = max(2, int(round(ev["velocity_window_s"] * fps)))
    ang, sp_in, sp_out = direction_change(ball, k)
    ang_wide, _, _ = direction_change(ball, 2 * k)  # a real bounce/hit is sharp at both scales
    peaks = _local_peaks(ang, ev["min_turn_deg"], int(ev["min_event_gap_s"] * fps))

    win = max(2, int(ev["refine_s"] * fps))
    cands, bounce_frames = [], []
    known = ~np.isnan(ball[:, 0])
    # a trajectory that starts right at a player (e.g. a serve) is also a hit candidate
    starts = [f for f in range(len(ball)) if known[f] and (f == 0 or not known[f - 1])]
    # the ball passing right by a paddle hand while changing direction (catches far-side
    # hits, where the ball barely changes direction on screen)
    close = []
    for s in SIDES:
        d = np.array([_side_dist(tr, s, f, ball[f]) for f in range(len(ball))])
        for f in range(1, len(d) - 1):
            if d[f] <= ev["hit_max_wrist_dist"] and d[f] <= d[f - 1] and d[f] < d[f + 1]:
                lo, hi = max(0, f - win), min(len(ang), f + win + 1)
                if ang[lo:hi].max() >= ev["min_turn_deg"] / 2:
                    close.append(f)
    for f, is_start in [(p, False) for p in peaks] + [(s, True) for s in starts + close]:
        side, d = _nearest_player(tr, f, ball[f])
        if side is not None and d <= ev["hit_search_dist"]:
            rf, rd = _refine_contact(tr, ball, side, f, win)
            if rd <= ev["hit_max_wrist_dist"]:
                lo, hi = max(0, rf - win), min(len(ang), rf + win + 1)
                cands.append({"frame": int(rf), "side": side, "source": "ball", "conf": float(1 - rd),
                              "score": float(ang[lo:hi].max() / 180.0 - rd), "peak": int(f)})
                continue
        if not is_start and ang_wide[max(0, f - 1):f + 2].max() >= ev["min_turn_deg"]:
            bounce_frames.append(f)
    cands.sort(key=lambda h: h["frame"])

    # A bounce in front of a player often looks like a hit in 2D (the ball overlaps the
    # player on screen). When the same player has several candidates in quick succession,
    # keep the sharpest direction change nearest the paddle hand; the rest were bounces
    # or the ball passing by.
    hits = []
    for c in cands:
        if hits and hits[-1]["side"] == c["side"]:
            gap = (c["frame"] - hits[-1]["frame"]) / fps
            if gap < ev["same_side_merge_s"]:
                if c["score"] > hits[-1]["score"]:
                    p = hits[-1]["peak"]
                    if gap > 3 / fps and p in peaks and ang_wide[max(0, p - 1):p + 2].max() >= ev["min_turn_deg"]:
                        bounce_frames.append(p)
                    hits[-1] = c
                continue
        hits.append(c)

    if onsets is not None and len(onsets):
        # The paddle pops replace the pose-only fallback below: they also catch hits the
        # ball tracker lost, with exact timing and far fewer false alarms.
        hits = fuse_audio(hits, onsets, tr, ball, fps, cfg, players)
    else:
        # Pose-only hits where the ball tracker lost the ball (fast drives, occlusion).
        known = ~np.isnan(tr["ball"][:, 0])
        pwin = int(0.25 * fps)
        for s in SIDES:
            ws = np.nan_to_num(wrist_speed(tr, s, _paddle_wrist(players, s), fps))
            for f in _local_peaks(ws, ev["pose_hit_wrist_speed"], int(0.5 * fps)):
                lo, hi = max(0, f - pwin), min(len(known), f + pwin)
                if known[lo:hi].mean() > 0.5:
                    continue  # ball was visible; trust the ball-based detector
                if any(abs(h["frame"] - f) < pwin for h in hits):
                    continue
                hits.append({"frame": int(f), "side": s, "source": "pose", "conf": 0.3})
    hits.sort(key=lambda h: h["frame"])

    bounces = []
    for f in sorted(set(bounce_frames)):
        if any(abs(f - h["frame"]) <= win for h in hits):
            continue
        cxy = court.to_court([ball[f]])[0]
        if -3 <= cxy[0] <= 23 and -3 <= cxy[1] <= 47:
            bounces.append({"frame": int(f), "court_xy": [float(cxy[0]), float(cxy[1])]})

    _assign_rallies(hits, fps, ev["rally_gap_s"])
    _attach_landings(hits, bounces, tr, fps)
    for i, h in enumerate(hits):
        h.pop("peak", None)
        h.pop("score", None)
        h["id"] = i
        h["t"] = round(h["frame"] / fps, 3)
    return hits, bounces


def _paddle_wrist(players, side):
    return 10 if players[side]["hand"] == "right" else 9


def _sound_delay(tr, side, f, cam_behind_ft):
    """Seconds for the pop to reach a camera centered behind the near baseline."""
    feet = tr[f"feet_{side}"]
    p = feet[f] if 0 <= f < len(feet) else np.array([np.nan, np.nan])
    if np.isnan(p[0]):
        p = (COURT_W / 2, 0.0 if side == "near" else COURT_L)
    return float(np.hypot(p[0] - COURT_W / 2, p[1] + cam_behind_ft)) / SPEED_OF_SOUND_FTS


def fuse_audio(hits, onsets, tr, ball, fps, cfg, players):
    """Combine video hits with paddle pops from the soundtrack (see pb/audio.py).

    1. A video hit with a pop close by is confirmed and moved to the pop's frame.
    2. A video hit with no pop is probably a bounce: its confidence is lowered (or it is
       dropped, with audio.drop_unmatched).
    3. A pop with no video hit becomes a hit if one player's paddle hand is at the ball
       or swinging hard at that moment; otherwise it was a bounce, an echo or noise.
    """
    a, ev = cfg["audio"], cfg["events"]
    n = len(ball)
    cam = a["camera_behind_baseline_ft"]
    t_pop = onsets[:, 0] - a["offset_s"]
    used = np.zeros(len(t_pop), bool)

    for h in hits:
        tc = t_pop - _sound_delay(tr, h["side"], h["frame"], cam)  # contact times if h's player hit
        d = np.where(used, np.inf, np.abs(tc - h["frame"] / fps))
        j = int(np.argmin(d))
        if d[j] <= a["match_s"]:
            used[j] = True
            h["audio"], h["audio_db"] = True, round(float(onsets[j, 1]), 1)
            if a["snap_to_audio"]:
                h["frame"] = int(np.clip(round(tc[j] * fps), 0, n - 1))
        else:
            h["audio"] = False
            h["conf"] = round(h["conf"] * a["unmatched_conf_scale"], 3)
    if a["drop_unmatched"]:
        hits = [h for h in hits if h["audio"]]

    win = max(1, int(a["match_s"] * fps))
    gap = int(ev["min_event_gap_s"] * fps)
    swing = ev["pose_hit_wrist_speed"] * a["swing_speed_frac"]
    ws = {s: np.nan_to_num(wrist_speed(tr, s, _paddle_wrist(players, s), fps)) for s in SIDES}
    for j in np.where(~used)[0]:
        best = None
        for s in SIDES:
            f = int(round((t_pop[j] - _sound_delay(tr, s, int(t_pop[j] * fps), cam)) * fps))
            if not 0 <= f < n or any(abs(h["frame"] - f) <= gap for h in hits):
                continue
            lo, hi = max(0, f - win), min(n, f + win + 1)
            dist = min(_side_dist(tr, s, ff, ball[ff]) for ff in range(lo, hi))
            if dist <= ev["hit_max_wrist_dist"]:
                score = 1 - dist
            elif ws[s][lo:hi].max(initial=0) >= swing:
                score = 0.3  # ball not seen, but this player swung
            else:
                continue
            if best is None or score > best[0]:
                best = (score, s, f)
        if best:
            hits.append({"frame": best[2], "side": best[1], "source": "audio", "conf": round(float(best[0]), 3),
                         "audio": True, "audio_db": round(float(onsets[j, 1]), 1)})
    return hits


def _assign_rallies(hits, fps, gap_s):
    rally, idx = 0, 0
    for i, h in enumerate(hits):
        if i > 0 and (h["frame"] - hits[i - 1]["frame"]) / fps > gap_s:
            rally += 1
            idx = 0
        h["rally"], h["shot_in_rally"] = rally, idx
        idx += 1


def _attach_landings(hits, bounces, tr, fps):
    for i, h in enumerate(hits):
        nxt = hits[i + 1] if i + 1 < len(hits) and hits[i + 1]["rally"] == h["rally"] else None
        end = nxt["frame"] if nxt else h["frame"] + int(3 * fps)
        opp_side = "far" if h["side"] == "near" else "near"
        land = None
        for b in bounces:
            if h["frame"] < b["frame"] < end:
                on_opp = (b["court_xy"][1] >= NET_Y) == (opp_side == "far")
                if on_opp:
                    land = b
                    break
        h["landing_frame"] = land["frame"] if land else None
        h["landing_xy"] = land["court_xy"] if land else None
        h["next_hit_frame"] = nxt["frame"] if nxt else None
        # Was THIS shot a volley? i.e. no bounce on the hitter's side since the previous hit.
        prev = hits[i - 1] if i > 0 and hits[i - 1]["rally"] == h["rally"] else None
        if prev is None:
            h["volley"] = False
        else:
            bounced = any(prev["frame"] < b["frame"] < h["frame"] and
                          ((b["court_xy"][1] >= NET_Y) == (h["side"] == "far")) for b in bounces)
            h["volley"] = not bounced
