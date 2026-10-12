"""Tee mode: solo practice hitting a ball off a batting tee into a net or a rebound board.

There's no court and no partner. Film face-on (the camera faces your chest, so the hitting
line runs across the frame). That view makes 2D pose far more accurate than the court
view. For each rep this measures:
  - contact point: how far in front of your front foot and hips, and the height relative
    to your body (0 = hips, 1 = shoulders)
  - body: knee bend, hip drop, elbow angle, torso lean
  - footwork: how long your feet were set before contact, how far you moved to the ball,
    stance width and whether your head was over your feet
  - paddle-hand speed at contact
  - ball: launch speed and angle from the video, and the average speed to a rebound board
    from the time between the paddle pop and the board pop

How a rep is found: a ball sits still on the tee, then vanishes at the same moment as a
paddle pop. Balls lying on the floor sit still too, but they don't vanish with a pop.
Distances in inches use the resting ball as a ruler (it is 2.9 in across).
"""
import json
from pathlib import Path

import cv2
import numpy as np

from .audio import SPEED_OF_SOUND_FTS, video_onsets
from .ball import BallDetector
from .draw import draw_pose
from .mechanics import (L_AN, L_EL, L_HIP, L_KN, L_SH, L_WR, R_AN, R_EL, R_HIP, R_KN, R_SH, R_WR,
                        _angle, _ok, standing_hip_height)
from .video import open_video

BALL_DIAMETER_IN = 2.9      # USA Pickleball spec: 2.87-2.97 in
IN_PER_S_PER_MPH = 17.6
FTS_TO_MPH = 0.681818
N_KP = 17
NOSE, L_EAR, R_EAR = 0, 3, 4
ANKLE_HEIGHT_FT = 0.29      # ankle joint above the floor (~3.5 in)


# ---------------------------------------------------------------- pass 1: scan the video

def _round_blobs(mask, min_area, max_area, min_circ):
    """Ball-sized, round, ball-colored blobs: (x, y, radius) in pixels."""
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if not min_area <= a <= max_area:
            continue
        per = cv2.arcLength(c, True) or 1.0
        if 4 * np.pi * a / (per * per) < min_circ:
            continue
        (x, y), r = cv2.minEnclosingCircle(c)  # robust to the tee cup hiding the bottom
        out.append((x, y, r))
    return out


class _RestTracker:
    """Follows ball-colored blobs that sit still and records each period a ball rested."""

    def __init__(self, fps, min_rest_s):
        self.gone = max(2, int(round(0.05 * fps)))  # brief occlusions (paddle, hand) are bridged
        self.min_len = min_rest_s * fps
        self.active, self.done = [], []  # done rows: [first, last, x, y, r]

    def update(self, f, blobs):
        for x, y, r in blobs:
            best, bd = None, np.inf
            for c in self.active:
                d = np.hypot(x - c["x"], y - c["y"])
                if d < max(0.5 * c["r"], 3.0) and d < bd:
                    best, bd = c, d
            if best is None:
                self.active.append({"first": f, "last": f, "x": x, "y": y, "x0": x, "y0": y, "r": r, "n": 1})
            elif best["last"] != f:
                best["last"], best["n"] = f, best["n"] + 1
                best["x"] += 0.2 * (x - best["x"])
                best["y"] += 0.2 * (y - best["y"])
                best["r"] += 0.2 * (r - best["r"])
        keep = []
        for c in self.active:
            if f - c["last"] > self.gone:
                self._close(c, c["last"])
            else:
                keep.append(c)
        self.active = keep

    def _close(self, c, last):
        span = c["last"] - c["first"] + 1
        still = np.hypot(c["x"] - c["x0"], c["y"] - c["y0"]) <= 0.5 * c["r"]
        if span >= self.min_len and c["n"] >= 0.7 * span and still:
            self.done.append([c["first"], last, c["x"], c["y"], c["r"]])

    def finish(self, n):
        for c in self.active:
            self._close(c, n)  # last = n means "still there when the video ended"


def scan(video, cfg, out_path, max_seconds=None):
    """One pass over the video: resting balls and moving-ball candidates."""
    t = cfg["tee"]
    cap = open_video(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_seconds:
        n = min(n, int(max_seconds * fps))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    s = (w / 1920.0) ** 2
    bcfg = {**cfg, "ball": {**cfg["ball"], "min_area": t["ball_min_area"], "max_area": t["ball_max_area"]}}
    det = BallDetector(bcfg, (h, w))
    rest = _RestTracker(fps, t["rest_s"])
    moving = []
    for f in range(n):
        ok, frame = cap.read()
        if not ok:
            n = f
            break
        moving += [(f, x, y) for x, y, _ in det.detect(frame)]
        rest.update(f, _round_blobs(det.color_mask, t["ball_min_area"] * s, t["ball_max_area"] * s,
                                    t["min_circularity"]))
        if f % 300 == 0:
            print(f"\r  frame {f}/{n}   ", end="", flush=True)
    cap.release()
    rest.finish(n)
    print()
    meta = {"video": str(video), "fps": fps, "frames": n, "width": w, "height": h}
    np.savez_compressed(out_path, meta=json.dumps(meta), rests=np.array(rest.done, float).reshape(-1, 5),
                        moving=np.array(moving, float).reshape(-1, 3))
    return out_path


def load_scan(path):
    d = np.load(path, allow_pickle=False)
    return {"meta": json.loads(str(d["meta"])), "rests": d["rests"], "moving": d["moving"]}


# ---------------------------------------------------------------- pass 2: find the reps

def find_reps(sc, pops, cfg):
    """A rep = a resting ball that vanishes together with a paddle pop.

    pops: audio onsets (N, 2) [time_s, strength_db], or None if there's no soundtrack (then
    every vanish counts, except ones where the ball reappears right away).
    """
    t, a = cfg["tee"], cfg["audio"]
    fps, n = sc["meta"]["fps"], sc["meta"]["frames"]
    rests = sc["rests"]
    cam = t["camera_distance_ft"]
    t_pop = pops[:, 0] - a["offset_s"] if pops is not None else np.zeros(0)
    hit_t = t_pop - cam / SPEED_OF_SOUND_FTS   # contact times, if the pop came from the tee
    reps, no_pop = [], 0
    for first, last, x, y, r in rests:
        if last >= n - 1:
            continue  # still on the tee when the video ended
        vanish = int(last) + 1
        hidden = any(f2 > last and f2 - last <= t["reappear_s"] * fps and np.hypot(x2 - x, y2 - y) < 1.5 * r
                     for f2, _, x2, y2, _ in rests)
        if hidden:
            continue  # someone walked in front of it
        rep = {"frame": vanish, "t": vanish / fps, "tee_xy": [float(x), float(y)], "ball_r_px": float(r),
               "rest_s": round((last - first + 1) / fps, 2), "audio": False}
        if pops is not None:
            d = np.abs(hit_t - vanish / fps)
            j = int(np.argmin(d)) if len(d) else -1
            if j < 0 or d[j] > a["match_s"]:
                no_pop += 1
                continue  # picked up or knocked off, not hit
            rep.update(frame=int(round(hit_t[j] * fps)), t=float(hit_t[j]), audio=True,
                       audio_db=round(float(pops[j, 1]), 1), _pop=j)
        reps.append(rep)
    reps.sort(key=lambda r: r["t"])

    if pops is not None and t["target"] == "board" and t.get("board_distance_ft"):
        D = float(t["board_distance_ft"])
        board_t = t_pop - np.hypot(D, cam) / SPEED_OF_SOUND_FTS  # when the ball hit the board
        lo, hi = t["speed_range_fts"]
        for i, rep in enumerate(reps):
            if not rep["audio"]:
                continue
            nxt = reps[i + 1]["t"] if i + 1 < len(reps) else np.inf
            ok = (board_t > rep["t"] + D / hi) & (board_t < rep["t"] + D / lo) & (board_t < nxt)
            ok[rep["_pop"]] = False
            k = np.where(ok)[0]
            if len(k):
                rep["board_speed_mph"] = round(D / (board_t[k[0]] - rep["t"]) * FTS_TO_MPH, 1)
    for rep in reps:
        rep.pop("_pop", None)
    return reps, no_pop


def fit_launch(sc, rep, cfg):
    """Ball speed and angle off the paddle, from the first few frames of flight."""
    t = cfg["tee"]
    fps = sc["meta"]["fps"]
    c, (x0, y0), r = rep["frame"], rep["tee_xy"], rep["ball_r_px"]
    ppi = 2 * r / BALL_DIAMETER_IN
    vmax = t["speed_range_fts"][1] * 12 * ppi / fps  # px per frame
    mv = sc["moving"]
    mv = mv[(mv[:, 0] >= c - 1) & (mv[:, 0] <= c + t["launch_window_s"] * fps)]
    pts = []
    for f in np.unique(mv[:, 0]):
        cand = mv[mv[:, 0] == f][:, 1:]
        if len(pts) < 2:
            d = np.hypot(cand[:, 0] - x0, cand[:, 1] - y0)
            ok = (d > r) & (d <= (f - c + 2) * vmax)
            if len(pts) == 1:
                ok &= d > np.hypot(pts[0][1] - x0, pts[0][2] - y0)  # moving away from the tee
            if ok.any():
                k = np.where(ok)[0][np.argmin(d[ok])]
                pts.append((f, *cand[k]))
            continue
        (f1, xa, ya), (f2, xb, yb) = pts[-2], pts[-1]
        v = np.array([xb - xa, yb - ya]) / (f2 - f1)
        p = np.array([xb, yb]) + v * (f - f2)
        d = np.hypot(cand[:, 0] - p[0], cand[:, 1] - p[1])
        k = int(np.argmin(d))
        if d[k] <= max(2 * r, 0.3 * np.linalg.norm(v) * (f - f2)):
            pts.append((f, *cand[k]))
    if len(pts) < 3:
        return
    p = np.array(pts)
    vx, bx = np.polyfit(p[:, 0], p[:, 1], 1)
    vy, by = np.polyfit(p[:, 0], p[:, 2], 1)
    # traced back to the moment of contact, the flight must start at the tee (allowing a
    # couple of frames of timing error), or we followed something else
    if np.hypot(bx + vx * c - x0, by + vy * c - y0) > 3 * r + 2 * np.hypot(vx, vy):
        return
    speed = np.hypot(vx, vy) * fps / ppi / IN_PER_S_PER_MPH
    rep["launch_speed_mph"] = round(float(speed), 1)
    rep["launch_angle_deg"] = round(float(np.degrees(np.arctan2(-vy, abs(vx)))), 1)
    rep["direction"] = 1 if vx > 0 else -1


# ---------------------------------------------------------------- pass 3: pose + metrics

def _pick_player(result, tee_x):
    """The hitter: the tallest person in frame, preferring the one closest to the tee."""
    if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
        return None
    boxes = result.boxes.xyxy.cpu().numpy()
    kxy = result.keypoints.xy.cpu().numpy()
    kc = result.keypoints.conf
    kc = kc.cpu().numpy() if kc is not None else np.ones(kxy.shape[:2])
    score = (boxes[:, 3] - boxes[:, 1]) - 0.5 * np.abs((boxes[:, 0] + boxes[:, 2]) / 2 - tee_x)
    i = int(np.argmax(score))
    return np.concatenate([kxy[i], kc[i][:, None]], axis=1).astype(np.float32), boxes[i].astype(np.float32)


def run_pose(video, frames, cfg, tee_x):
    """YOLO pose on the given frames: {frame: (keypoints (17, 3), box (4,))} for the hitter."""
    try:
        from ultralytics import YOLO
    except ImportError:
        print("  (pose) ultralytics isn't installed - body measurements skipped. "
              "Run `pip install -r requirements.txt`.")
        return {}
    p = cfg["pose"]
    model = YOLO(p["model"])
    want = sorted(frames)
    out, f, i = {}, 0, 0
    cap = open_video(video)
    while i < len(want):
        if f < want[i]:
            if not cap.grab():
                break
            f += 1
            continue
        ok, frame = cap.read()
        if not ok:
            break
        res = model.predict(frame, verbose=False, conf=p["conf"], classes=[0], imgsz=p.get("imgsz", 960))[0]
        sel = _pick_player(res, tee_x)
        if sel is not None:
            out[f] = sel
        f, i = f + 1, i + 1
        if i % 200 == 0:
            print(f"\r  pose {i}/{len(want)} frames   ", end="", flush=True)
    cap.release()
    print()
    return out


def pose_cached(video, frames, cfg, tee_x, cache_path):
    """run_pose, reusing frames already done in an earlier run."""
    have = {}
    if Path(cache_path).exists():
        d = np.load(cache_path)
        have = {int(f): (k, b) for f, k, b in zip(d["frames"], d["kp"], d["box"])}
        done = set(int(f) for f in d["done"])
    else:
        done = set()
    todo = set(frames) - done
    if todo:
        print(f"  measuring body pose on {len(todo)} frames ...")
        have.update(run_pose(video, todo, cfg, tee_x))
        done |= todo
        fr = sorted(have)
        np.savez_compressed(cache_path, frames=np.array(fr, int), done=np.array(sorted(done), int),
                            kp=np.array([have[f][0] for f in fr]).reshape(-1, N_KP, 3),
                            box=np.array([have[f][1] for f in fr]).reshape(-1, 4))
    return have


def _pose_at(P, f, search=3):
    for df in range(search + 1):
        for ff in (f - df, f + df):
            if ff in P:
                return P[ff][0]
    return None


def front_foot_x(kp, direction):
    ankles = [kp[i, 0] for i in (L_AN, R_AN) if _ok(kp, i)]
    return max(ankles, key=lambda x: direction * x) if ankles else None


def measure_rep(rep, P, standing, hand, fps, direction):
    m = {}
    kp = _pose_at(P, rep["frame"])
    if kp is None:
        return m
    wr, el, sh = (R_WR, R_EL, R_SH) if hand == "right" else (L_WR, L_EL, L_SH)
    bx, by = rep["tee_xy"]
    ppi = 2 * rep["ball_r_px"] / BALL_DIAMETER_IN
    if _ok(kp, L_SH, R_SH, L_HIP, R_HIP):
        sh_mid, hip_mid = kp[[L_SH, R_SH], :2].mean(0), kp[[L_HIP, R_HIP], :2].mean(0)
        torso = np.linalg.norm(sh_mid - hip_mid)
        if hip_mid[1] - sh_mid[1] > 1:
            m["contact_height"] = round(float((hip_mid[1] - by) / (hip_mid[1] - sh_mid[1])), 2)
        m["torso_lean_deg"] = round(float(np.degrees(np.arctan2(abs(sh_mid[0] - hip_mid[0]), hip_mid[1] - sh_mid[1]))), 0)
        if direction:
            m["ahead_of_hips_in"] = round(float(direction * (bx - hip_mid[0]) / ppi), 1)
        if _ok(kp, L_AN, R_AN) and not np.isnan(standing) and torso > 1:
            hip_h = (np.mean(kp[[L_AN, R_AN], 1]) - hip_mid[1]) / torso
            m["hip_drop_pct"] = round(float((1 - hip_h / standing) * 100), 0)
    if direction:
        ff = front_foot_x(kp, direction)
        if ff is not None:
            m["ahead_of_front_foot_in"] = round(float(direction * (bx - ff) / ppi), 1)
    if _ok(kp, sh, el, wr):
        m["elbow_angle"] = round(_angle(kp[sh], kp[el], kp[wr]), 0)
    knees = [_angle(kp[h], kp[k], kp[a]) for h, k, a in ((L_HIP, L_KN, L_AN), (R_HIP, R_KN, R_AN)) if _ok(kp, h, k, a)]
    if knees:
        m["knee_angle"] = round(float(np.mean(knees)), 0)
    # paddle-hand speed: wrist travel over the frames just before contact
    c = rep["frame"]
    pts = [(f, P[f][0][wr]) for f in range(c - 3, c + 1) if f in P and P[f][0][wr, 2] >= 0.3]
    if len(pts) >= 2:
        (fa, a), (fb, b) = pts[0], pts[-1]
        v = np.hypot(*(b[:2] - a[:2])) / (fb - fa) * fps / ppi / IN_PER_S_PER_MPH
        m["hand_speed_mph"] = round(float(v), 1)
    return m


def _series(P, frames, idx):
    """Image position of the mean of keypoints idx at each frame (NaN where not seen)."""
    out = np.full((len(frames), 2), np.nan)
    for i, f in enumerate(frames):
        if f in P and _ok(P[f][0], *idx):
            out[i] = P[f][0][list(idx), :2].mean(axis=0)
    return out


def _smooth(a, k=2):
    """Moving average over 2k+1 frames, skipping gaps (pose keypoints jitter by a pixel or two)."""
    out = np.full_like(a, np.nan)
    for i in range(len(a)):
        seg = a[max(0, i - k):i + k + 1]
        seg = seg[~np.isnan(seg[:, 0])]
        if len(seg):
            out[i] = seg.mean(axis=0)
    return out


def _floor(xy, f_px, h, cx, cy):
    """Floor position (x across the frame, z away from the camera; feet) of ankles at image xy.

    A level camera h ft above the ankles sees an ankle z ft away f_px * h / z pixels below the
    middle of the frame. NaN for anything at or above the middle (can't be on the floor).
    """
    xy = np.atleast_2d(xy)
    dy = xy[:, 1] - cy
    z = np.full(len(xy), np.nan)
    ok = dy > 1
    z[ok] = f_px * h / dy[ok]
    return np.stack([(xy[:, 0] - cx) * z / f_px, z], axis=1)


def footwork(rep, P, fps, direction, t, size):
    """Feet set, distance moved, stance, contact vs. front foot and balance, from the pose.

    Camera model: a level camera camera_distance_ft from the tee and camera_height_ft off
    the floor; the resting ball gives pixels per foot at the tee, hence the focal length.
    Each foot is placed on the floor from how low it is in the frame, so stepping toward
    or away from the camera counts as much as stepping across it.
    """
    c = rep["frame"]
    if _pose_at(P, c) is None:
        return {}
    m = {}
    pre, lag = int(t["pose_window_s"][0] * fps), max(1, int(round(0.05 * fps)))
    frames = list(range(c - pre, c + lag + 1))  # index pre = contact
    ppi = 2 * rep["ball_r_px"] / BALL_DIAMETER_IN
    zc = t["camera_distance_ft"]
    f_px = ppi * 12 * zc
    cx, cy = size[0] / 2, size[1] / 2
    h = t["camera_height_ft"] - ANKLE_HEIGHT_FT
    feet = [_floor(_smooth(_series(P, frames, (i,))), f_px, h, cx, cy) for i in (L_AN, R_AN)]

    # Feet set: time since the last frame either foot was moving
    last_move, seen = -1, False
    for g in feet:
        sp = np.full(len(frames), np.nan)
        sp[lag:len(frames) - lag] = np.hypot(*(g[2 * lag:] - g[:-2 * lag]).T) / (2 * lag / fps)  # ft/s
        ok = ~np.isnan(sp[:pre + 1])
        if ok.sum() >= 0.5 * (pre + 1):
            seen = True
            moving = np.where(ok & (sp[:pre + 1] > t["feet_moving_speed"]))[0]
            if len(moving):
                last_move = max(last_move, int(moving[-1]))
    if seen:
        m["feet_set_s"] = round((pre - last_move) / fps if last_move >= 0 else pre / fps, 2)

    # Distance moved: furthest your hips were from where they were at contact. Distance from
    # the camera comes from your torso's size on screen (unaffected by lifting a foot).
    hip = _smooth(_series(P, frames, (L_HIP, R_HIP)))
    torso = np.hypot(*(_smooth(_series(P, frames, (L_SH, R_SH))) - hip).T)
    (zl, zr) = feet[0][pre, 1], feet[1][pre, 1]
    zbody = np.nanmean([zl, zr]) if not (np.isnan(zl) and np.isnan(zr)) else zc
    if not np.isnan(torso[pre]):
        z = zbody * torso[pre] / torso
        x = (hip[:, 0] - cx) * z / f_px
        d = np.hypot(x - x[pre], z - z[pre])[:pre + 1]
        if (~np.isnan(d)).sum() >= 0.5 * (pre + 1):
            m["moved_ft"] = round(float(np.nanmax(d)), 1)

    # At contact: stance, stagger, ball vs. front foot, head vs. the middle of the feet
    gl, gr = feet[0][pre], feet[1][pre]
    if not (np.isnan(gl).any() or np.isnan(gr).any()):
        m["stance_in"] = round(float(np.hypot(*(gl - gr)) * 12), 0)
        m["stagger_in"] = round(float(abs(gl[0] - gr[0]) * 12), 0)
        if direction:
            ball_x = (rep["tee_xy"][0] - cx) * zc / f_px
            front = max(gl[0], gr[0], key=lambda v: direction * v)
            m["ahead_of_front_foot_in"] = round(float(direction * (ball_x - front) * 12), 1)
            kp = _pose_at(P, c)
            head = next((kp[list(i), 0].mean() for i in ((NOSE,), (L_EAR, R_EAR), (L_SH, R_SH)) if _ok(kp, *i)), None)
            if head is not None:
                head_x = (head - cx) * (gl[1] + gr[1]) / 2 / f_px
                m["head_over_base_in"] = round(float(direction * (head_x - (gl[0] + gr[0]) / 2) * 12), 1)
    return m


def save_thumb(video, rep, P, direction, out_path):
    """Contact frame with the skeleton, the ball, and lines at the ball and the front foot."""
    cap = open_video(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, rep["frame"])
    ok, img = cap.read()
    cap.release()
    if not ok:
        return None
    kp = _pose_at(P, rep["frame"])
    bx, by = (int(v) for v in rep["tee_xy"])
    r = int(rep["ball_r_px"])
    xs, ys = [bx - 3 * r, bx + 3 * r], [by - 3 * r, by + 3 * r]
    if kp is not None:
        draw_pose(img, kp, (255, 180, 0))
        good = kp[kp[:, 2] >= 0.3]
        xs += list(good[:, 0])
        ys += list(good[:, 1])
        ff = front_foot_x(kp, direction) if direction else None
        if ff is not None:
            cv2.line(img, (int(ff), 0), (int(ff), img.shape[0]), (80, 200, 80), 2, cv2.LINE_AA)
    cv2.line(img, (bx, 0), (bx, img.shape[0]), (0, 220, 255), 1, cv2.LINE_AA)
    cv2.circle(img, (bx, by), r + 3, (0, 220, 255), 2, cv2.LINE_AA)
    pad = 0.12 * (max(ys) - min(ys))
    x0, x1 = int(max(0, min(xs) - pad)), int(min(img.shape[1], max(xs) + pad))
    y0, y1 = int(max(0, min(ys) - pad)), int(min(img.shape[0], max(ys) + pad))
    crop = img[y0:y1, x0:x1]
    crop = cv2.resize(crop, (int(crop.shape[1] * 360 / crop.shape[0]), 360))
    cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out_path


# ---------------------------------------------------------------- orchestration

def analyze_tee(video, cfg, sess_dir, player, max_seconds=None, redo=False):
    t = cfg["tee"]
    sess_dir = Path(sess_dir)
    sess_dir.mkdir(exist_ok=True)
    stem = Path(video).stem
    scan_path = sess_dir / f"{stem}.tee_scan.npz"
    if redo or not scan_path.exists():
        print(f"[1/3] Scanning {video} for the ball on the tee ...")
        scan(video, cfg, scan_path, max_seconds)
    else:
        print(f"[1/3] Reusing {scan_path.name} (use --redo to rescan)")
    sc = load_scan(scan_path)
    fps, n = sc["meta"]["fps"], sc["meta"]["frames"]

    print("[2/3] Finding reps ...")
    pops = video_onsets(video, cfg["audio"], n / fps) if cfg["audio"].get("enabled") else None
    reps, no_pop = find_reps(sc, pops, cfg)
    for rep in reps:
        fit_launch(sc, rep, cfg)
    # which way you hit across the frame: from the settings, else each rep's own flight,
    # else the way most reps went
    side = {"right": 1, "left": -1}.get(t["target_side"])
    usual = int(np.sign(sum(r.get("direction", 0) for r in reps))) or None
    msg = f"  {len(reps)} reps"
    if pops is None:
        msg += " (no soundtrack: every ball that left the tee counts)"
    elif no_pop:
        msg += f"; {no_pop} times the ball left the tee with no pop (picked up or knocked off) were skipped"
    print(msg)

    print("[3/3] Measuring each rep ...")
    pre, post = (int(s * fps) for s in t["pose_window_s"])
    frames = {f for r in reps for f in range(max(0, r["frame"] - pre), min(n, r["frame"] + post + 1))}
    frames |= set(range(0, n, max(1, int(0.5 * fps))))  # spread over the video, for your standing height
    tee_x = float(np.median([r["tee_xy"][0] for r in reps])) if reps else sc["meta"]["width"] / 2
    P = pose_cached(video, frames, cfg, tee_x, sess_dir / f"{stem}.tee_pose.npz") if reps and cfg["pose"].get("enabled", True) else {}
    kps = np.array([P[f][0] for f in sorted(P)]).reshape(-1, N_KP, 3)
    standing = standing_hip_height({"kp_player": kps}, "player")
    thumbs = sess_dir / f"{stem}.tee"
    thumbs.mkdir(exist_ok=True)
    for i, rep in enumerate(reps):
        direction = side or rep.get("direction") or usual
        rep["id"] = i
        rep["t"] = round(rep["t"], 3)
        rep["metrics"] = measure_rep(rep, P, standing, player["hand"], fps, direction)
        rep["metrics"].update(footwork(rep, P, fps, direction, t, (sc["meta"]["width"], sc["meta"]["height"])))
        if save_thumb(video, rep, P, direction, thumbs / f"rep_{i:03d}.jpg"):
            rep["thumb"] = f"{thumbs.name}/rep_{i:03d}.jpg"

    data = {"mode": "tee", "video": str(video), "fps": fps, "player": player, "shot": t["shot"],
            "tee_height_in": t.get("tee_height_in"), "target": t["target"],
            "board_distance_ft": t.get("board_distance_ft") if t["target"] == "board" else None,
            "audio": pops is not None, "reps": reps}
    out = sess_dir / f"{stem}.tee.json"
    with open(out, "w") as f:
        json.dump(data, f, indent=1)
    print(f"  saved -> {out}")
    return out
