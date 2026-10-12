"""Ball detection (color + motion) and offline trajectory linking.

Pickleballs are small, fast and blurry, so a general object detector misses them a lot.
Instead we look for blobs that are (a) the ball's color and (b) moving, then link
those candidates across frames into tracklets and keep the ones that behave like a ball.
Use `pbshot.py tune-ball` to dial in the color range for your ball + lighting.
"""
import cv2
import numpy as np


class BallDetector:
    def __init__(self, cfg, frame_shape, roi_polygon=None):
        b = cfg["ball"]
        self.lower = np.array(b["hsv_lower"], dtype=np.uint8)
        self.upper = np.array(b["hsv_upper"], dtype=np.uint8)
        s = (frame_shape[1] / 1920.0) ** 2  # areas in config are for 1080p
        self.min_area = b["min_area"] * s
        self.max_area = b["max_area"] * s
        self.min_circ = b["min_circularity"]
        self.motion_thresh = b["motion_threshold"]
        self.use_motion = b.get("use_motion", True)
        self.prev = []  # previous gray frames
        self.roi = None
        if roi_polygon is not None:
            self.roi = np.zeros(frame_shape[:2], np.uint8)
            cv2.fillPoly(self.roi, [np.asarray(roi_polygon, np.int32)], 255)

    def detect(self, frame):
        """Return list of (x, y, score) candidates for this frame."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lower, self.upper)
        self.color_mask = mask  # ball-colored pixels, moving or not (tee mode looks for resting balls)
        gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self.use_motion:
            if len(self.prev) >= 2:
                d = cv2.absdiff(gray, self.prev[-2])
                motion = cv2.threshold(d, self.motion_thresh, 255, cv2.THRESH_BINARY)[1]
                motion = cv2.dilate(motion, None, iterations=2)
                mask = cv2.bitwise_and(mask, motion)
            else:
                mask = np.zeros_like(mask)
            self.prev.append(gray)
            self.prev = self.prev[-2:]
        if self.roi is not None:
            mask = cv2.bitwise_and(mask, self.roi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.dilate(mask, None, iterations=1)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in cnts:
            a = cv2.contourArea(c)
            if not (self.min_area <= a <= self.max_area):
                continue
            per = cv2.arcLength(c, True) or 1.0
            circ = 4 * np.pi * a / (per * per)
            if circ < self.min_circ:
                continue
            m = cv2.moments(c)
            if m["m00"] == 0:
                continue
            out.append((m["m10"] / m["m00"], m["m01"] / m["m00"], float(circ)))
        return out


def link_trajectory(candidates, n_frames, frame_w, cfg):
    """Link per-frame candidates into one ball trajectory.

    candidates: list (len n_frames) of lists of (x, y, score)
    returns: array (n_frames, 2) with NaN where the ball isn't known.
    """
    b = cfg["ball"]
    gate = b["link_gate_frac"] * frame_w
    max_gap = b["max_link_gap"]
    min_len = b["min_tracklet_len"]
    min_travel = b["min_tracklet_travel_frac"] * frame_w

    tracklets = []  # each: {"f": [...], "p": [...]}
    active = []
    for f, cands in enumerate(candidates):
        active = [t for t in active if f - t["f"][-1] <= max_gap]
        used = set()
        # predict and greedily match (closest pairs first)
        pairs = []
        for ti, t in enumerate(active):
            p = np.array(t["p"][-1])
            if len(t["p"]) >= 2:
                v = (np.array(t["p"][-1]) - np.array(t["p"][-2])) / max(1, t["f"][-1] - t["f"][-2])
                p = p + v * (f - t["f"][-1])
            for ci, (x, y, _) in enumerate(cands):
                d = np.hypot(x - p[0], y - p[1])
                if d < gate * (f - t["f"][-1]):
                    pairs.append((d, ti, ci))
        pairs.sort()
        matched_t = set()
        for d, ti, ci in pairs:
            if ti in matched_t or ci in used:
                continue
            active[ti]["f"].append(f)
            active[ti]["p"].append(cands[ci][:2])
            matched_t.add(ti)
            used.add(ci)
        for ci, (x, y, _) in enumerate(cands):
            if ci not in used:
                t = {"f": [f], "p": [(x, y)]}
                tracklets.append(t)
                active.append(t)

    good = []
    for t in tracklets:
        if len(t["f"]) < min_len:
            continue
        p = np.array(t["p"])
        travel = np.sum(np.hypot(*np.diff(p, axis=0).T))
        if travel < min_travel:
            continue
        good.append(t)

    traj = np.full((n_frames, 2), np.nan)
    owner_len = np.zeros(n_frames)
    for t in sorted(good, key=lambda t: len(t["f"])):
        for f, p in zip(t["f"], t["p"]):
            if len(t["f"]) >= owner_len[f]:
                traj[f] = p
                owner_len[f] = len(t["f"])
    return fill_gaps(traj, b["interp_gap"])


def fill_gaps(traj, max_gap):
    """Linearly interpolate NaN gaps of up to max_gap frames."""
    traj = traj.copy()
    known = np.where(~np.isnan(traj[:, 0]))[0]
    for a, b in zip(known[:-1], known[1:]):
        if 1 < b - a <= max_gap + 1:
            for k in range(1, b - a):
                traj[a + k] = traj[a] + (traj[b] - traj[a]) * k / (b - a)
    return traj


def tune_ball_color(video_path, cfg, at_seconds=5.0):
    """Trackbar tool to find the HSV range for your ball. Prints values to paste into config.yaml."""
    from .video import open_video
    cap = open_video(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(at_seconds * fps))
    lo, hi = cfg["ball"]["hsv_lower"], cfg["ball"]["hsv_upper"]
    win = "Ball color tuner (space = pause/play, , . = step, q = done)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    names = ["H lo", "S lo", "V lo", "H hi", "S hi", "V hi"]
    for n, v, mx in zip(names, lo + hi, [179, 255, 255, 179, 255, 255]):
        cv2.createTrackbar(n, win, int(v), mx, lambda _: None)
    paused, frame = False, None
    while True:
        if not paused or frame is None:
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
        vals = [cv2.getTrackbarPos(n, win) for n in names]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(vals[:3]), np.array(vals[3:]))
        vis = frame.copy()
        vis[mask > 0] = (255, 0, 255)
        small = cv2.resize(np.hstack([vis, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)]), None, fx=0.5, fy=0.5)
        cv2.imshow(win, small)
        k = cv2.waitKey(1 if not paused else 30) & 0xFF
        if k == ord(" "):
            paused = not paused
        elif k == ord("."):
            ok, frame = cap.read()
        elif k == ord(","):
            pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, pos - 2))
            ok, frame = cap.read()
        elif k in (ord("q"), 27):
            break
    cap.release()
    cv2.destroyAllWindows()
    print("Paste into config.yaml under ball:")
    print(f"  hsv_lower: {vals[:3]}")
    print(f"  hsv_upper: {vals[3:]}")
