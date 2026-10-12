"""End-to-end smoke test on a synthetic rally (no real footage needed).

Renders a perspective court + a yellow ball flying between two players, runs the real
ball tracker, injects simple synthetic skeletons (YOLO can't see cartoon players),
then runs hit/bounce detection, mechanics, the report and the annotated video.

    python tests/synthetic_test.py
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pb.court import Court, NET_Y  # noqa: E402
from pb.detect import analyze_video, load_tracks, SIDES  # noqa: E402
from pb.events import detect_events, fuse_audio, _sound_delay  # noqa: E402
from pb.session import build_shots, players_from_cfg, load_shots  # noqa: E402
from pb.report import build_report  # noqa: E402
from pb.render import render  # noqa: E402

OUT = ROOT / "tests" / "out"
W, H, FPS = 1280, 720, 60
CORNERS = [(240, 690), (1040, 690), (790, 250), (490, 250)]
court = Court(CORNERS)

# (hitter side, hit xy, contact height ft, landing xy, flight seconds, true type)
SHOTS = [
    ("near", (13, -1), 2.0, (6, 36), 1.10, "serve"),
    ("far", (7, 45), 2.5, (12, 6), 1.05, "return"),
    ("near", (12, 3), 1.8, (9, 25), 1.25, "drop"),
    ("far", (9, 29.5), 1.2, (11, 18), 0.95, "dink"),
    ("near", (11, 15.5), 1.2, (8, 26), 0.95, "dink"),
    ("far", (8, 29.5), 1.3, (12, 18.5), 0.95, "dink"),
    ("near", (12, 15.5), 1.4, (5, 40), 0.55, "speedup"),
]


def px_per_ft(y):
    a, b = court.to_image([(0, y), (20, y)])
    return np.hypot(*(b - a)) / 20.0


def project(x, y, z):
    g = court.to_image([(x, y)])[0]
    return g - np.array([0, z * px_per_ft(y)])


def simulate():
    """Return per-frame ball (x,y,z) in court feet, hit frames, player positions."""
    pos, hits, t = [], [], 0
    for i, (side, hxy, hz, lxy, dur, typ) in enumerate(SHOTS):
        # hit -> landing parabola
        n = int(dur * FPS)
        apex = 4.0 if typ not in ("speedup",) else 1.5
        hits.append({"frame": len(pos), "side": side, "type": typ, "xy": hxy})
        for k in range(n):
            s = k / n
            x = hxy[0] + (lxy[0] - hxy[0]) * s
            y = hxy[1] + (lxy[1] - hxy[1]) * s
            z = hz * (1 - s) + 4 * apex * s * (1 - s)
            pos.append((x, y, z))
        # landing -> next hitter (bounce), or roll away at the end
        nxt = SHOTS[i + 1][1] if i + 1 < len(SHOTS) else (lxy[0], lxy[1] + (8 if lxy[1] > NET_Y else -8))
        nz = SHOTS[i + 1][2] if i + 1 < len(SHOTS) else 0.5
        m = int(0.35 * FPS)
        for k in range(m):
            s = k / m
            x = lxy[0] + (nxt[0] - lxy[0]) * s
            y = lxy[1] + (nxt[1] - lxy[1]) * s
            z = nz * s + 4 * 1.2 * s * (1 - s)
            pos.append((x, y, z))
    for _ in range(FPS):
        pos.append((np.nan, np.nan, np.nan))
    return np.array(pos), hits


def player_track(hits, n):
    track = {s: np.zeros((n, 2)) for s in SIDES}
    for s in SIDES:
        key = [(h["frame"], h["xy"]) for h in hits if h["side"] == s]
        fr = [k[0] for k in key]
        for d in range(2):
            track[s][:, d] = np.interp(np.arange(n), fr, [k[1][d] for k in key])
    return track


def skeleton(side, feet_xy, wrist_target=None):
    """COCO-17 keypoints for a simple upright player standing at feet_xy (court ft)."""
    s = px_per_ft(feet_xy[1])
    base = court.to_image([feet_xy])[0]
    sign = 1 if side == "near" else -1  # player's right is image-right for the near player
    # (dx ft to player's right, height ft) -- slightly crouched ready stance
    pts = {5: (-0.7, 4.6), 6: (0.7, 4.6), 7: (-0.9, 3.6), 8: (0.9, 3.6), 9: (-0.9, 2.8), 10: (1.0, 3.2),
           11: (-0.45, 2.8), 12: (0.45, 2.8), 13: (-0.6, 1.5), 14: (0.6, 1.5), 15: (-0.7, 0), 16: (0.7, 0),
           0: (0, 5.3), 1: (-0.1, 5.4), 2: (0.1, 5.4), 3: (-0.3, 5.3), 4: (0.3, 5.3)}
    kp = np.zeros((17, 3), np.float32)
    for i, (dx, z) in pts.items():
        kp[i] = (base[0] + sign * dx * s, base[1] - z * s, 0.9)
    if wrist_target is not None:
        kp[10, :2] = wrist_target
        kp[8, :2] = (kp[6, :2] + wrist_target) / 2
    box = np.array([kp[:, 0].min() - 10, kp[:, 1].min() - 10, kp[:, 0].max() + 10, kp[:, 1].max() + 5])
    return kp, box


def draw_video(path, ball, ptrack):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    bg = np.full((H, W, 3), (70, 70, 70), np.uint8)
    cv2.fillPoly(bg, [np.array(CORNERS, np.int32)], (60, 110, 60))
    court.draw_lines(bg, (235, 235, 235), 2)
    rng = np.random.default_rng(0)
    for f, p in enumerate(ball):
        img = bg.copy()
        for s, col in (("far", (200, 80, 40)), ("near", (40, 60, 200))):
            kp, _ = skeleton(s, ptrack[s][f])
            for a, b in ((5, 11), (6, 12), (11, 15), (12, 16), (5, 6), (6, 10), (5, 9)):
                cv2.line(img, tuple(kp[a, :2].astype(int)), tuple(kp[b, :2].astype(int)), col, 4)
        if not np.isnan(p[0]):
            c = project(*p)
            r = max(3, int(0.25 * px_per_ft(p[1])))
            cv2.circle(img, tuple(c.astype(int)), r, (40, 230, 230), -1, cv2.LINE_AA)
        noise = rng.integers(-6, 7, img.shape, dtype=np.int16)
        vw.write(np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8))
    vw.release()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["pose"]["enabled"] = False
    cfg["audio"]["enabled"] = False  # the synthetic video is silent; check_audio feeds pops directly
    ball, truth = simulate()
    ptrack = player_track(truth, len(ball))
    video = OUT / "synthetic.mp4"
    draw_video(video, ball, ptrack)
    calib = OUT / "calib.json"
    court.save(calib)

    tracks = OUT / "synthetic.tracks.npz"
    analyze_video(video, calib, cfg, tracks)

    # inject synthetic skeletons: paddle wrist follows the ball near each contact
    d = dict(np.load(tracks))
    n = len(d["ball"])
    for s in SIDES:
        kps, boxes, feet = np.zeros((n, 17, 3), np.float32), np.zeros((n, 4), np.float32), np.zeros((n, 2), np.float32)
        for f in range(n):
            wt = None
            for h in truth:
                if h["side"] == s and abs(f - h["frame"]) <= 3 and not np.isnan(ball[f, 0]):
                    wt = project(*ball[f]) + np.array([0, 6])
            kps[f], boxes[f] = skeleton(s, ptrack[s][f], wt)
            feet[f] = ptrack[s][f]
        d[f"kp_{s}"], d[f"box_{s}"], d[f"feet_{s}"] = kps, boxes, feet
    np.savez_compressed(tracks, **d)

    players = players_from_cfg(cfg)
    shots_path = OUT / "synthetic.shots.json"
    data = build_shots(tracks, cfg, players, OUT / "no_model.joblib", shots_path)

    tol = int(0.1 * FPS)
    found = 0
    print("\n truth               detected")
    for t in truth:
        m = [h for h in data["hits"] if abs(h["frame"] - t["frame"]) <= tol and h["side"] == t["side"]]
        found += bool(m)
        got = f"{m[0]['type_auto']:8s} land={m[0].get('landing_xy')}" if m else "MISSED"
        print(f" {t['frame']:4d} {t['side']:4s} {t['type']:8s} -> {got}")
    extra = len(data["hits"]) - found
    print(f"\nHits found {found}/{len(truth)}, extra detections: {extra}")

    build_report([shots_path], cfg, OUT / "report.html")
    render(shots_path, OUT / "synthetic.annotated.mp4")
    assert found >= len(truth) - 1, "hit detection regressed"
    check_audio(tracks, cfg, players, truth)
    print("OK")


def check_audio(tracks, cfg, players, truth):
    """Feed the pops a microphone at the camera would hear and check they're fused in."""
    tr = load_tracks(tracks)
    cam = cfg["audio"]["camera_behind_baseline_ft"]
    pops = [(t["frame"] / FPS + _sound_delay(tr, t["side"], t["frame"], cam), 0.0) for t in truth]
    pops.append((len(tr["ball"]) / FPS - 0.5, -10.0))  # decoy: nobody near the ball, nobody swinging
    onsets = np.array(pops)

    def score(hits, label):
        exact = sum(any(h["side"] == t["side"] and abs(h["frame"] - t["frame"]) <= 1 and h.get("audio")
                        for h in hits) for t in truth)
        extra = sum(1 for h in hits if h.get("audio")) - exact
        quiet = sum(1 for h in hits if h.get("audio") is False)
        print(f"{label}: {exact}/{len(truth)} hits within 1 frame, {extra} extra, {quiet} flagged as no-pop")
        assert exact == len(truth) and extra == 0, f"{label} regressed"

    hits, _ = detect_events(tr, cfg, players, onsets)
    print()
    score(hits, "Video + audio")
    # audio alone must recover hits the ball tracker missed entirely
    ball = tr["ball"]
    score(fuse_audio([], onsets, tr, ball, FPS, cfg, players), "Audio only   ")


if __name__ == "__main__":
    main()
