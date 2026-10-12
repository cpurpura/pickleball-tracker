"""End-to-end tee-mode test on a synthetic basement session (no real footage needed).

Draws a tee with a ball on it, hits it away at known speeds and angles, and mixes paddle
pops and board pops into the soundtrack. Things that must NOT count as reps: balls lying
on the floor, a person walking in front of one of them, and a ball picked up off the tee
without being hit. YOLO can't see a cartoon hitter, so a synthetic skeleton is injected
in place of the pose model.

    python tests/tee_test.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pb.tee as tee  # noqa: E402
from pb.audio import SPEED_OF_SOUND_FTS, ffmpeg_exe  # noqa: E402
from pb.tee_report import build_tee_report  # noqa: E402

OUT = ROOT / "tests" / "out"
W, H, FPS, SR = 1280, 720, 60, 44100
TX, TY, R, FLOOR = 520, 420, 14, 620
PPI = 2 * R / 2.9
CAM_FT, BOARD_FT = 12.0, 12.0
HAND_PX = 30  # paddle-hand travel per frame at contact
# (launch mph, launch angle deg, front foot inches behind the ball); None = ball picked up, not hit
REPS = [(30, 4, 6), (22, 10, 4), None, (35, 2, 8), (18, 15, -2), (27, 6, 5)]
FLOOR_BALLS = [(150, FLOOR - 10), (1150, FLOOR - 12)]
WALKER = (300, 322)  # frames when a person blocks the first floor ball


def schedule():
    out = []
    for i, r in enumerate(REPS):
        place = 40 + i * 150
        out.append({"place": place, "contact": place + 90, "rep": r})
    return out


def ball_pos(ev, f):
    """Where this rep's ball is at frame f (None = not in view)."""
    if f < ev["place"]:
        return None
    if f < ev["contact"]:
        return (TX, TY)
    if ev["rep"] is None:
        return None  # picked up
    mph, ang, _ = ev["rep"]
    v = mph * 17.6 * PPI / FPS
    k = f - ev["contact"] + 0.5  # true contact is half a frame before frame `contact`
    x, y = TX + v * np.cos(np.radians(ang)) * k, TY - v * np.sin(np.radians(ang)) * k
    return (x, y) if x < W + R else None


def draw_video(path, evs, n):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    bg = np.full((H, W, 3), (95, 90, 85), np.uint8)
    bg[FLOOR:] = (70, 70, 75)
    cv2.line(bg, (TX, TY + R), (TX, FLOOR), (40, 40, 40), 8)
    cv2.rectangle(bg, (TX - 40, FLOOR - 6), (TX + 40, FLOOR + 4), (40, 40, 40), -1)
    rng = np.random.default_rng(0)
    for f in range(n):
        img = bg.copy()
        for x, y in FLOOR_BALLS:
            cv2.circle(img, (x, y), 12, (40, 230, 230), -1, cv2.LINE_AA)
        for ev in evs:
            p = ball_pos(ev, f)
            if p is not None and (ev is evs[-1] or f < evs[evs.index(ev) + 1]["place"] or p != (TX, TY)):
                cv2.circle(img, (int(p[0]), int(p[1])), R, (40, 230, 230), -1, cv2.LINE_AA)
        if WALKER[0] <= f < WALKER[1]:
            cv2.rectangle(img, (100, 300), (220, 715), (30, 30, 30), -1)
        noise = rng.integers(-5, 6, img.shape, dtype=np.int16)
        vw.write(np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8))
    vw.release()


def soundtrack(evs, n, rng):
    x = rng.normal(0, 0.002, int(n / FPS * SR))
    k = np.arange(int(0.08 * SR)) / SR
    d_tee = CAM_FT / SPEED_OF_SOUND_FTS
    d_board = np.hypot(BOARD_FT, CAM_FT) / SPEED_OF_SOUND_FTS
    for ev in evs:
        if ev["rep"] is None:
            continue
        tc = (ev["contact"] - 0.5) / FPS
        tb = tc + BOARD_FT / (ev["rep"][0] / 0.681818)
        for t, amp in ((tc + d_tee, 0.5), (tb + d_board, 0.3)):
            s = int(t * SR)
            x[s:s + len(k)] += amp * rng.normal(0, 1, len(k)) * np.exp(-k / 0.003)
    return np.clip(x, -1, 1).astype(np.float32)


def skeleton(f, evs):
    """Right-handed hitter facing the camera, hitting left to right."""
    ev = min(evs, key=lambda e: abs(e["contact"] - f))
    ahead = ev["rep"][2] if ev["rep"] else 5
    crouch = abs(f - ev["contact"]) <= 30 and ev["rep"] is not None
    front = TX - ahead * PPI
    back = front - 20 * PPI
    hip_y = 470 if crouch else 450
    hip_x = (front + back) / 2
    kp = np.zeros((17, 3), np.float32)
    pts = {15: (back, 612), 16: (front, 612), 13: (back + 5, 545), 14: (front - 5, 545),
           11: (hip_x - 25, hip_y), 12: (hip_x + 25, hip_y), 5: (hip_x - 40, 330), 6: (hip_x + 40, 330),
           7: (hip_x - 60, 400), 0: (hip_x, 280), 1: (hip_x - 5, 275), 2: (hip_x + 5, 275), 3: (hip_x - 12, 280),
           4: (hip_x + 12, 280), 9: (hip_x - 60, 460)}
    wx = TX + (f - (ev["contact"] - 0.5)) * HAND_PX if crouch else hip_x + 60
    pts[10] = (wx, TY if crouch else 450)
    pts[8] = ((pts[6][0] + wx) / 2, (330 + pts[10][1]) / 2 - 15)
    for i, (x, y) in pts.items():
        kp[i] = (x, y, 0.9)
    box = np.array([kp[:, 0].min() - 10, 260, kp[:, 0].max() + 10, 620], np.float32)
    return kp, box


def run(video, cfg, label):
    sess = OUT / f"tee_{label}"
    shutil.rmtree(sess, ignore_errors=True)
    tee.analyze_tee(video, cfg, sess, {"name": "Chad", "hand": "right"}, redo=True)
    import json
    return json.loads((sess / f"{video.stem}.tee.json").read_text()), sess


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["tee"].update(camera_distance_ft=CAM_FT, board_distance_ft=BOARD_FT, target="board", tee_height_in=32)
    evs = schedule()
    n = evs[-1]["contact"] + 90
    tee.run_pose = lambda video, frames, cfg, tee_x: {f: skeleton(f, evs) for f in frames}

    silent = OUT / "tee_silent.mp4"
    draw_video(silent, evs, n)
    wav, video = OUT / "tee.wav", OUT / "tee_session.mp4"
    wavfile.write(wav, SR, (soundtrack(evs, n, np.random.default_rng(1)) * 32767).astype(np.int16))
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(silent), "-i", str(wav), "-c:v", "copy",
                    "-c:a", "aac", "-shortest", str(video)], check=True)

    data, sess = run(video, cfg, "audio")
    hits = [e for e in evs if e["rep"]]
    print("\n truth (mph / deg / in)      measured launch | board | ahead | hand")
    ok = len(data["reps"]) == len(hits)
    hand = HAND_PX * FPS / PPI / 17.6
    for ev, rep in zip(hits, data["reps"]):
        mph, ang, ahead = ev["rep"]
        m = rep["metrics"]
        print(f" {mph:3d} {ang:+3d}° {ahead:+3d} in  frame {ev['contact']:4d}->{rep['frame']:4d}   "
              f"{rep.get('launch_speed_mph')} {rep.get('launch_angle_deg')} | {rep.get('board_speed_mph')} | "
              f"{m.get('ahead_of_front_foot_in')} | {m.get('hand_speed_mph')}  hip drop {m.get('hip_drop_pct')}%")
        ok &= abs(rep["frame"] - ev["contact"]) <= 1
        ok &= rep.get("launch_speed_mph") is not None and abs(rep["launch_speed_mph"] - mph) <= 0.1 * mph
        ok &= rep.get("launch_angle_deg") is not None and abs(rep["launch_angle_deg"] - ang) <= 3
        ok &= rep.get("board_speed_mph") is not None and abs(rep["board_speed_mph"] - mph) <= 0.05 * mph
        ok &= abs(m.get("ahead_of_front_foot_in", 99) - ahead) <= 1
        ok &= abs(m.get("hand_speed_mph", 99) - hand) <= 0.1 * hand
        ok &= m.get("hip_drop_pct", 0) > 0
    print(f"With sound: {len(data['reps'])} reps found ({len(hits)} real; floor balls, walker and pick-up ignored)")
    assert ok, "tee mode regressed"

    quiet, _ = run(silent, cfg, "silent")
    print(f"No sound:   {len(quiet['reps'])} reps (expect {len(hits) + 1}: the pick-up can't be told apart without a pop)")
    assert len(quiet["reps"]) == len(hits) + 1, "tee mode (no audio) regressed"

    build_tee_report(sorted(sess.glob("*.tee.json")), cfg, OUT / "tee_report.html")
    print("OK")


if __name__ == "__main__":
    main()
