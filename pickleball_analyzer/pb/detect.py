"""Pass 1: run pose estimation (both players) + ball detection over a video.

Output is a compact .npz "tracks" file that every later step reads, so the slow part
only runs once per video.
"""
import json
import time

import cv2
import numpy as np

from .ball import BallDetector, link_trajectory
from .court import Court, NET_Y, COURT_W, COURT_L
from .video import open_video

SIDES = ("near", "far")
N_KP = 17  # COCO keypoints


def ball_roi(court, w, h):
    c = court.image_corners  # nearL, nearR, farR, farL
    dx, up, down = 0.08 * w, 0.35 * h, 0.10 * h
    poly = np.array([
        [c[0][0] - dx, c[0][1] + down], [c[1][0] + dx, c[1][1] + down],
        [c[2][0] + dx, c[2][1] - up], [c[3][0] - dx, c[3][1] - up],
    ])
    poly[:, 0] = poly[:, 0].clip(0, w - 1)
    poly[:, 1] = poly[:, 1].clip(0, h - 1)
    return poly


def _feet_point(kp, box):
    la, ra = kp[15], kp[16]  # ankles
    good = [p for p in (la, ra) if p[2] > 0.3]
    if good:
        return np.mean([p[:2] for p in good], axis=0)
    return np.array([(box[0] + box[2]) / 2, box[3]])


def select_players(result, court, prev_feet):
    """Pick one person per side of the net that's standing on/near this court."""
    chosen = {}
    if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
        return chosen
    boxes = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()
    kxy = result.keypoints.xy.cpu().numpy()
    kc = result.keypoints.conf
    kc = kc.cpu().numpy() if kc is not None else np.ones(kxy.shape[:2])
    for i in range(len(boxes)):
        kp = np.concatenate([kxy[i], kc[i][:, None]], axis=1)
        feet = _feet_point(kp, boxes[i])
        cxy = court.to_court([feet])[0]
        # players may stand behind the baseline or just outside the sidelines
        if not (-6 <= cxy[0] <= COURT_W + 6 and -14 <= cxy[1] <= COURT_L + 14):
            continue
        side = "near" if cxy[1] < NET_Y else "far"
        # prefer the confident person closest to where this side's player was last frame
        score = confs[i]
        if prev_feet.get(side) is not None:
            score -= 0.02 * np.hypot(*(cxy - prev_feet[side]))
        if side not in chosen or score > chosen[side][0]:
            chosen[side] = (score, kp, boxes[i], cxy)
    return {s: v[1:] for s, v in chosen.items()}


def _interp_nan(arr):
    """Linear interpolation along axis 0 over NaNs (for pose-every-N)."""
    flat = arr.reshape(arr.shape[0], -1)
    idx = np.arange(flat.shape[0])
    for j in range(flat.shape[1]):
        col = flat[:, j]
        good = ~np.isnan(col)
        if good.sum() >= 2:
            # only fill interior gaps shorter than 10 frames
            gi = np.where(good)[0]
            filled = np.interp(idx, gi, col[gi])
            for a, b in zip(gi[:-1], gi[1:]):
                if 1 < b - a <= 10:
                    col[a + 1:b] = filled[a + 1:b]
    return flat.reshape(arr.shape)


def analyze_video(video_path, calib_path, cfg, out_path, max_seconds=None):
    court = Court.load(calib_path)
    cap = open_video(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_seconds:
        n = min(n, int(max_seconds * fps))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    pose_cfg = cfg["pose"]
    model = None
    if pose_cfg.get("enabled", True):
        from ultralytics import YOLO
        model = YOLO(pose_cfg["model"])
    every = max(1, int(pose_cfg.get("every_n_frames", 1)))

    ball_det = BallDetector(cfg, (h, w), ball_roi(court, w, h))
    cands = []
    kps = {s: np.full((n, N_KP, 3), np.nan, np.float32) for s in SIDES}
    boxes = {s: np.full((n, 4), np.nan, np.float32) for s in SIDES}
    feet = {s: np.full((n, 2), np.nan, np.float32) for s in SIDES}
    prev_feet = {}

    t0 = time.time()
    for f in range(n):
        ok, frame = cap.read()
        if not ok:
            n = f
            break
        cands.append(ball_det.detect(frame))
        if model is not None and f % every == 0:
            res = model.predict(frame, verbose=False, conf=pose_cfg["conf"], classes=[0],
                                imgsz=pose_cfg.get("imgsz", 960))[0]
            for side, (kp, box, cxy) in select_players(res, court, prev_feet).items():
                kps[side][f], boxes[side][f], feet[side][f] = kp, box, cxy
                prev_feet[side] = cxy
        if f % 100 == 0:
            el = time.time() - t0
            rate = (f + 1) / el if el > 0 else 0
            print(f"\r  frame {f}/{n}  ({rate:.1f} fps, ~{(n - f) / max(rate, 1e-6):.0f}s left)   ", end="", flush=True)
    cap.release()
    print()

    for s in SIDES:
        kps[s], boxes[s], feet[s] = kps[s][:n], boxes[s][:n], feet[s][:n]
        if every > 1:
            kps[s], boxes[s], feet[s] = _interp_nan(kps[s]), _interp_nan(boxes[s]), _interp_nan(feet[s])

    ball = link_trajectory(cands[:n], n, w, cfg)
    meta = {"video": str(video_path), "fps": fps, "frames": n, "width": w, "height": h,
            "calib": court.image_corners.tolist()}
    np.savez_compressed(
        out_path, meta=json.dumps(meta), ball=ball,
        **{f"kp_{s}": kps[s] for s in SIDES},
        **{f"box_{s}": boxes[s] for s in SIDES},
        **{f"feet_{s}": feet[s] for s in SIDES},
    )
    found = np.mean(~np.isnan(ball[:, 0])) * 100
    pose_found = {s: np.mean(~np.isnan(feet[s][:, 0])) * 100 for s in SIDES}
    print(f"  ball visible in {found:.0f}% of frames; players found near={pose_found['near']:.0f}% far={pose_found['far']:.0f}%")
    return out_path


def load_tracks(path):
    d = np.load(path, allow_pickle=False)
    t = {"meta": json.loads(str(d["meta"])), "ball": d["ball"]}
    for s in SIDES:
        t[f"kp_{s}"], t[f"box_{s}"], t[f"feet_{s}"] = d[f"kp_{s}"], d[f"box_{s}"], d[f"feet_{s}"]
    t["court"] = Court(t["meta"]["calib"])
    return t
