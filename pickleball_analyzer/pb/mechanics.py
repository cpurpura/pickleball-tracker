"""Body-mechanics metrics measured at each contact point (2D pose, so treat as trends).

COCO keypoints: 5/6 shoulders, 7/8 elbows, 9/10 wrists, 11/12 hips, 13/14 knees, 15/16 ankles
(left/right from the PLAYER's point of view).
"""
import numpy as np

from .court import NET_Y, KITCHEN_DEPTH, COURT_W

L_SH, R_SH, L_EL, R_EL, L_WR, R_WR, L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16
MIN_CONF = 0.3


def _angle(a, b, c):
    v1, v2 = a[:2] - b[:2], c[:2] - b[:2]
    n = np.linalg.norm(v1) * np.linalg.norm(v2)
    if n < 1e-6:
        return np.nan
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1, 1))))


def _ok(kp, *idx):
    return all(not np.isnan(kp[i, 0]) and kp[i, 2] >= MIN_CONF for i in idx)


def _pose_near(tr, side, f, search=4):
    kps = tr[f"kp_{side}"]
    for df in range(search + 1):
        for ff in (f - df, f + df):
            if 0 <= ff < len(kps) and not np.isnan(kps[ff][:, 0]).all():
                return kps[ff], ff
    return None, None


def standing_hip_height(tr, side):
    """Typical upright hip-to-ankle height in torso units (90th percentile over the video)."""
    kps = tr[f"kp_{side}"]
    vals = []
    for kp in kps[:: max(1, len(kps) // 2000)]:
        if _ok(kp, L_SH, R_SH, L_HIP, R_HIP, L_AN, R_AN):
            torso = np.mean(kp[[L_HIP, R_HIP], 1]) - np.mean(kp[[L_SH, R_SH], 1])
            if torso > 1:
                vals.append((np.mean(kp[[L_AN, R_AN], 1]) - np.mean(kp[[L_HIP, R_HIP], 1])) / torso)
    return float(np.percentile(vals, 90)) if len(vals) > 10 else np.nan


def paddle_height(kp, wrist):
    """0 = hip level, 1 = shoulder level, <0 below hips, >1 above shoulders."""
    if not _ok(kp, wrist, L_SH, R_SH, L_HIP, R_HIP):
        return np.nan
    hip_y = np.mean(kp[[L_HIP, R_HIP], 1])
    sh_y = np.mean(kp[[L_SH, R_SH], 1])
    if hip_y - sh_y < 1:
        return np.nan
    return float((hip_y - kp[wrist, 1]) / (hip_y - sh_y))


def measure(tr, hit, players, standing):
    side = hit["side"]
    hand = players[side]["hand"]
    wr, el, sh = (R_WR, R_EL, R_SH) if hand == "right" else (L_WR, L_EL, L_SH)
    m = {}
    kp, f = _pose_near(tr, side, hit["frame"])
    feet = tr[f"feet_{side}"][hit["frame"]]
    if not np.isnan(feet[0]):
        m["court_x"], m["court_y"] = float(feet[0]), float(feet[1])
        dn = abs(feet[1] - NET_Y)
        m["dist_from_net_ft"] = round(float(dn), 1)
        m["ft_behind_kitchen"] = round(float(dn - KITCHEN_DEPTH), 1)
        m["lateral_from_center_ft"] = round(float(feet[0] - COURT_W / 2), 1)
    if kp is None:
        return m

    m["contact_height"] = round(paddle_height(kp, wr), 2)
    if _ok(kp, sh, el, wr):
        m["elbow_angle"] = round(_angle(kp[sh], kp[el], kp[wr]), 0)
    knees = [_angle(kp[h], kp[k], kp[a]) for h, k, a in ((L_HIP, L_KN, L_AN), (R_HIP, R_KN, R_AN)) if _ok(kp, h, k, a)]
    if knees:
        m["knee_angle"] = round(float(np.mean(knees)), 0)
    if _ok(kp, L_SH, R_SH, L_HIP, R_HIP):
        sh_mid, hip_mid = kp[[L_SH, R_SH], :2].mean(0), kp[[L_HIP, R_HIP], :2].mean(0)
        torso_vec = sh_mid - hip_mid
        torso = np.linalg.norm(torso_vec)
        m["torso_lean_deg"] = round(float(np.degrees(np.arctan2(abs(torso_vec[0]), -torso_vec[1]))), 0)
        if _ok(kp, L_AN, R_AN):
            hip_h = (np.mean(kp[[L_AN, R_AN], 1]) - hip_mid[1]) / max(torso, 1)
            if not np.isnan(standing):
                m["hip_drop_pct"] = round(float((1 - hip_h / standing) * 100), 0)
            sw = abs(kp[R_SH, 0] - kp[L_SH, 0])
            if sw > 1:
                m["stance_width"] = round(float(abs(kp[R_AN, 0] - kp[L_AN, 0]) / sw), 2)
        if _ok(kp, wr):
            m["reach"] = round(float(np.linalg.norm(kp[wr, :2] - hip_mid) / max(torso, 1)), 2)
            # Forehand vs backhand: near player faces away from camera (their right = image right),
            # far player faces the camera (their right = image left).
            dx = kp[wr, 0] - hip_mid[0]
            right_side = dx > 0 if side == "near" else dx < 0
            m["wing"] = "forehand" if right_side == (hand == "right") else "backhand"
    return m


def readiness(tr, hits, players):
    """Paddle height at the moment the OPPONENT struck the ball (were you ready?)."""
    for i, h in enumerate(hits):
        prev = hits[i - 1] if i > 0 and hits[i - 1]["rally"] == h["rally"] else None
        if prev is None or prev["side"] == h["side"]:
            continue
        wr = R_WR if players[h["side"]]["hand"] == "right" else L_WR
        kp, _ = _pose_near(tr, h["side"], prev["frame"])
        if kp is not None:
            v = paddle_height(kp, wr)
            if not np.isnan(v):
                h["metrics"]["ready_paddle_height"] = round(v, 2)


def compute_all(tr, hits, players):
    standing = {s: standing_hip_height(tr, s) for s in ("near", "far")}
    fps = tr["meta"]["fps"]
    for h in hits:
        h["metrics"] = measure(tr, h, players, standing[h["side"]])
        # rough ball speed: hitter's feet -> landing spot (or -> opponent) over flight time
        start = (h["metrics"].get("court_x"), h["metrics"].get("court_y"))
        if start[0] is not None and h.get("landing_xy") and h.get("landing_frame"):
            d = np.hypot(h["landing_xy"][0] - start[0], h["landing_xy"][1] - start[1])
            dt = (h["landing_frame"] - h["frame"]) / fps
            if dt > 0:
                h["metrics"]["speed_fts"] = round(float(d / dt), 1)
                h["metrics"]["flight_s"] = round(dt, 2)
        elif start[0] is not None and h.get("next_hit_frame"):
            opp = "far" if h["side"] == "near" else "near"
            of = tr[f"feet_{opp}"][h["next_hit_frame"]]
            dt = (h["next_hit_frame"] - h["frame"]) / fps
            if not np.isnan(of[0]) and dt > 0:
                h["metrics"]["speed_fts"] = round(float(np.hypot(of[0] - start[0], of[1] - start[1]) / dt), 1)
                h["metrics"]["flight_s"] = round(dt, 2)
        if h.get("landing_xy"):
            dn = abs(h["landing_xy"][1] - NET_Y)
            h["metrics"]["landing_from_net_ft"] = round(float(dn), 1)
            h["metrics"]["landed_in_kitchen"] = bool(dn <= KITCHEN_DEPTH)
    readiness(tr, hits, players)
