"""Setup check for tee mode: run a short test clip and get a pass / warn / fail checklist.

Record ~30-60 s of your real setup with a few normal hits, then:
    python pbshot.py check-setup clip.mp4
Each check says what's wrong and how to fix it, and the HTML page shows a frame from
your clip with what the app found drawn on it.
"""
import base64
import html
from pathlib import Path

import cv2
import numpy as np

from . import tee as T
from .audio import detect_onsets, load_audio
from .draw import draw_pose
from .mechanics import L_AN, R_AN, _ok
from .report import CSS
from .video import open_video

ICON = {"pass": "✓", "warn": "!", "fail": "✗", "skip": "–"}
TERM = {"pass": "ok", "warn": "!!", "fail": "XX", "skip": "--"}  # Windows consoles can't print the icons
CLS = {"pass": "good", "warn": "warn", "fail": "bad", "skip": "info"}


class Checks:
    def __init__(self):
        self.items = []

    def add(self, status, title, detail, fix=""):
        self.items.append({"status": status, "title": title, "detail": detail, "fix": fix if status != "pass" else ""})

    @property
    def verdict(self):
        s = {i["status"] for i in self.items}
        return "fail" if "fail" in s else "warn" if "warn" in s else "pass"


def _video_checks(video, cfg, ck, max_seconds):
    """Frame rate, steadiness of the frame rate, resolution, orientation, light, ball-colored clutter."""
    cap = open_video(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    lo, hi = np.array(cfg["ball"]["hsv_lower"], np.uint8), np.array(cfg["ball"]["hsv_upper"], np.uint8)
    stamps, bright, clutter = [], [], []
    n = int(min(cap.get(cv2.CAP_PROP_FRAME_COUNT), (max_seconds or 1e9) * max(fps, 1)))
    for f in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        stamps.append(cap.get(cv2.CAP_PROP_POS_MSEC))
        if f % 30 == 0:
            bright.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
            clutter.append((cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), lo, hi) > 0).mean())
    cap.release()

    if fps >= 50:
        ck.add("pass", "Frame rate", f"{fps:.0f} fps")
    elif fps >= 29:
        ck.add("warn", "Frame rate", f"{fps:.0f} fps: contact timing is coarser and launch speed/angle will often be missing.",
               "Record at 60 fps (phone camera settings). Slow-motion modes usually drop or stretch the sound, so use normal video.")
    else:
        ck.add("fail", "Frame rate", f"{fps:.0f} fps is too low to follow the ball.", "Record at 60 fps.")

    d = np.diff([s for s in stamps if s > 0])
    if len(d) > 30 and np.median(d) > 0:
        jitter = np.mean(np.abs(d - np.median(d)) > 0.5 * np.median(d))
        if jitter > 0.05:
            ck.add("warn", "Steady frame rate", f"{jitter:.0%} of frames arrive at uneven intervals (variable frame rate).",
                   "Add light (cameras slow down in the dark), or use a phone instead of a webcam.")
        else:
            ck.add("pass", "Steady frame rate", "frames are evenly spaced")

    if w < h:
        ck.add("fail", "Orientation", f"portrait ({w}x{h})", "Turn the phone sideways (landscape).")
    elif h < 700:
        ck.add("warn", "Resolution", f"{w}x{h}: small", "Record at 1080p.")
    else:
        ck.add("pass", "Resolution", f"{w}x{h}, landscape")

    b = float(np.median(bright)) if bright else 0
    if b < 50:
        ck.add("warn", "Lighting", f"dark (average brightness {b:.0f}/255): more blur and a less steady frame rate.",
               "Turn on more lights, or point a work light at the hitting area (not into the camera).")
    else:
        ck.add("pass", "Lighting", f"average brightness {b:.0f}/255")

    c = float(np.median(clutter)) if clutter else 0
    if c > 0.02:
        ck.add("warn", "Ball-colored background", f"{c:.1%} of the picture matches the ball's color.",
               "Move ball-colored things out of view, or run `tune-ball` to tighten the color range for your ball and lights.")
    else:
        ck.add("pass", "Ball-colored background", f"{c:.2%} of the picture matches the ball's color")
    return fps, (w, h), len(stamps)


def check_setup(video, cfg, sess_dir, out_html, max_seconds=60.0):
    t, a = cfg["tee"], cfg["audio"]
    sess_dir = Path(sess_dir)
    sess_dir.mkdir(exist_ok=True)
    ck = Checks()
    print("Checking the video ...")
    fps, (w, h), n = _video_checks(video, cfg, ck, max_seconds)

    print("Checking the sound ...")
    x = load_audio(video, a["sample_rate"], max_seconds)
    pops = None
    if x is None:
        ck.add("fail", "Sound", "the clip has no soundtrack, so reps can't be confirmed and board speed is unavailable.",
               "Record normal video with sound (not slow-mo or time-lapse) and make sure the mic isn't muted.")
    else:
        pops = detect_onsets(x, a["sample_rate"], a)
        clip = float(np.mean(np.abs(x) > 0.99))
        if clip > 1e-4:
            ck.add("warn", "Sound level", "the sound is clipping (too loud), which can merge or hide pops.",
                   "Move the phone a little farther from the board, or turn off any audio boost.")
        if len(pops) == 0:
            ck.add("fail", "Paddle pops", "no pops found in the sound.",
                   "Hit a few balls in the clip. On a laptop, turn off mic noise suppression / voice focus. "
                   "If hits are soft, lower audio.min_snr_db in config.yaml.")
        else:
            ck.add("pass", "Paddle pops", f"{len(pops)} pops found")

    print("Looking for the ball on the tee ...")
    scan_path = sess_dir / f"{Path(video).stem}.setup_scan.npz"
    T.scan(video, cfg, scan_path, max_seconds)
    sc = T.load_scan(scan_path)
    reps, no_pop = T.find_reps(sc, pops, cfg)
    for rep in reps:
        T.fit_launch(sc, rep, cfg)
    resting = len(sc["rests"])
    if not reps:
        if resting == 0:
            ck.add("fail", "Ball on the tee", "no ball sitting still was found.",
                   "Make sure the ball on the tee is in view and not hidden by you, and run `tune-ball` if your ball "
                   "isn't yellow/optic green.")
        else:
            ck.add("fail", "Reps", f"a ball was found sitting still, but it never left with a pop ({no_pop} times it left silently).",
                   "Include a few real hits in the clip. If you did hit, the mic may be filtering the pop (see Paddle pops).")
    else:
        msg = f"{len(reps)} reps found"
        if no_pop:
            msg += f"; the ball left the tee {no_pop} time{'s' * (no_pop > 1)} without a pop (fine if you picked it up)"
        ck.add("pass", "Reps", msg)

        r = float(np.median([rep["ball_r_px"] for rep in reps]))
        if 2 * r < 14:
            ck.add("warn", "Ball size", f"the ball is only {2 * r:.0f} px across, so inch measurements get rough.",
                   "Move the camera closer (about 10-12 ft) or record at 1080p.")
        else:
            ck.add("pass", "Ball size", f"{2 * r:.0f} px across")

        launched = [rep for rep in reps if "launch_speed_mph" in rep]
        if len(launched) >= 0.5 * len(reps):
            ck.add("pass", "Ball tracked in flight", f"launch measured on {len(launched)}/{len(reps)} reps")
        else:
            ck.add("warn", "Ball tracked in flight", f"launch speed/angle measured on only {len(launched)}/{len(reps)} reps.",
                   "Set the camera at a right angle to the hitting line so the ball crosses the frame, leave a few feet "
                   "of open space past the tee, and use 60 fps with good light.")

        direction = {"right": 1, "left": -1}.get(t["target_side"]) or \
            int(np.sign(sum(rep.get("direction", 0) for rep in reps))) or None
        tee_x = float(np.median([rep["tee_xy"][0] for rep in reps]))
        ppi = 2 * r / T.BALL_DIAMETER_IN
        if direction:
            room_ft = ((w - tee_x) if direction > 0 else tee_x) / ppi / 12
            if room_ft < 2.5:
                ck.add("warn", "Room for the ball's flight", f"only {room_ft:.1f} ft of picture past the tee in the hitting direction.",
                       "Shift the camera so the tee sits nearer the side you hit away from.")
            else:
                ck.add("pass", "Room for the ball's flight", f"{room_ft:.1f} ft of picture past the tee")

        if pops is not None and t["target"] == "board":
            board = [rep for rep in reps if rep.get("board_speed_mph")]
            both = [rep["launch_speed_mph"] / rep["board_speed_mph"] for rep in board if "launch_speed_mph" in rep]
            ratio = float(np.median(both)) if len(both) >= 2 else 1.0
            if len(board) >= 0.5 * len(reps) and 0.8 <= ratio <= 1.25:
                ck.add("pass", "Board pops", f"board speed measured on {len(board)}/{len(reps)} reps"
                       + (", and it agrees with the speed seen on video" if len(both) >= 2 else ""))
            elif len(board) >= 0.5 * len(reps):
                D = t["board_distance_ft"]
                ck.add("warn", "Board pops", f"board speed is {1 / ratio:.1f}x the speed seen on video, so the "
                                             f"{D:g} ft board distance is probably wrong.",
                       f"Measure from the tee to the board's face along the hitting line. From this clip it looks "
                       f"like about {D * ratio:.0f} ft (pass --board-distance).")
            else:
                ck.add("warn", "Board pops", f"board speed measured on only {len(board)}/{len(reps)} reps.",
                       f"Check board_distance_ft ({t['board_distance_ft']} ft, tee to board face). If the ball hits the floor "
                       "first or the board sound is soft, use --target net, or lower audio.min_snr_db.")
    P = _pose_checks(video, cfg, ck, reps, (w, h))
    shot = _snapshot(video, reps, P, (w, h), sess_dir)
    _write_html(ck, video, shot, out_html)
    word = {"pass": "READY", "warn": "READY, with warnings", "fail": "NOT READY"}[ck.verdict]
    print(f"\nSetup check: {word}")
    for i in ck.items:
        print(f"  [{TERM[i['status']]}] {i['title']}: {i['detail']}")
        if i["fix"]:
            print(f"        -> {i['fix']}")
    print(f"\nDetails with a picture -> {out_html}")
    return ck


def _pose_checks(video, cfg, ck, reps, size):
    """Is the whole body in frame, and do the camera height/distance settings match the video?"""
    if not reps:
        return {}
    t = cfg["tee"]
    frames = {rep["frame"] for rep in reps[:12]} | {reps[0]["frame"] - 2}  # last one: the snapshot
    P = T.run_pose(video, frames, cfg, float(np.median([rep["tee_xy"][0] for rep in reps])))
    if not P:
        ck.add("skip", "Body in frame", "pose model unavailable, so body and footwork checks were skipped.",
               "Run `pip install -r requirements.txt`.")
        return P
    w, h = size
    seen, whole, edge, zs = 0, 0, 0, []
    for rep in reps[:12]:
        kp = T._pose_at(P, rep["frame"])
        if kp is None:
            continue
        seen += 1
        if _ok(kp, L_AN, R_AN) and (_ok(kp, 0) or _ok(kp, 5, 6)):
            whole += 1
        box = P[min(P, key=lambda f: abs(f - rep["frame"]))][1]
        if box[0] < 0.01 * w or box[2] > 0.99 * w or box[1] < 0.01 * h or box[3] > 0.99 * h:
            edge += 1
        ppi = 2 * rep["ball_r_px"] / T.BALL_DIAMETER_IN
        f_px = ppi * 12 * t["camera_distance_ft"]
        if _ok(kp, L_AN, R_AN):
            g = T._floor(kp[[L_AN, R_AN], :2], f_px, t["camera_height_ft"] - T.ANKLE_HEIGHT_FT, w / 2, h / 2)
            zs += list(g[~np.isnan(g[:, 1]), 1])
    n = len(reps[:12])
    if seen < 0.5 * n:
        ck.add("fail", "Hitter found", f"you were found at contact on only {seen}/{n} reps.",
               "Make sure your whole body is in the picture and you're the largest person in view.")
        return P
    if whole < 0.8 * seen or edge > 0.2 * seen:
        ck.add("warn", "Body in frame", f"head and both feet visible on {whole}/{seen} reps; cut off at the edge on {edge}.",
               "Zoom out or move the camera back so there's space above your head and below your feet.")
    else:
        ck.add("pass", "Body in frame", f"head and both feet visible on {whole}/{seen} reps")
    if zs:
        ratio = float(np.median(zs)) / t["camera_distance_ft"]
        if 0.75 <= ratio <= 1.33:
            ck.add("pass", "Camera height & distance", f"your feet measure about {np.median(zs):.0f} ft from the camera, "
                                                       f"matching the {t['camera_distance_ft']:g} ft setting")
        else:
            ck.add("warn", "Camera height & distance",
                   f"your feet measure about {np.median(zs):.0f} ft from the camera, but the settings say the tee is "
                   f"{t['camera_distance_ft']:g} ft away and the lens is {t['camera_height_ft']:g} ft up.",
                   "Measure the camera-to-tee distance and lens height, pass --camera-distance / --camera-height, "
                   "and keep the phone level (not tilted up or down). Footwork numbers depend on this.")
    else:
        ck.add("warn", "Camera height & distance", "couldn't place your feet on the floor.",
               "Keep the phone level, with the lens above ankle height (about 3 ft).")
    return P


def _snapshot(video, reps, P, size, sess_dir):
    """A frame with what the app found: tee ball, flight direction, skeleton, the camera's level line."""
    w, h = size
    f = reps[0]["frame"] - 2 if reps else 0
    cap = open_video(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, f))
    ok, img = cap.read()
    cap.release()
    if not ok:
        return None
    cv2.line(img, (0, h // 2), (w, h // 2), (200, 200, 200), 1, cv2.LINE_AA)
    cv2.putText(img, "camera level line", (10, h // 2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
    for rep in reps:
        bx, by = (int(v) for v in rep["tee_xy"])
        cv2.circle(img, (bx, by), int(rep["ball_r_px"]) + 4, (0, 220, 255), 2, cv2.LINE_AA)
        if "launch_angle_deg" in rep:
            ang = np.radians(rep["launch_angle_deg"])
            L = 0.25 * w
            end = (int(bx + rep["direction"] * L * np.cos(ang)), int(by - L * np.sin(ang)))
            cv2.arrowedLine(img, (bx, by), end, (80, 200, 80), 2, cv2.LINE_AA, tipLength=0.08)
    if f in P:
        draw_pose(img, P[f][0], (255, 180, 0))
    path = sess_dir / f"{Path(video).stem}.setup.jpg"
    scale = min(1.0, 1280 / w)
    cv2.imwrite(str(path), cv2.resize(img, (int(w * scale), int(h * scale))), [cv2.IMWRITE_JPEG_QUALITY, 82])
    return path


def _write_html(ck, video, shot, out_html):
    word = {"pass": "Ready to record", "warn": "Ready, with a few things to improve", "fail": "Not ready yet"}[ck.verdict]
    rows = "".join(
        f"<li class={CLS[i['status']]}><span class=ic>{ICON[i['status']]}</span><div><b>{html.escape(i['title'])}</b>"
        f" <span class=muted>{html.escape(i['detail'])}</span>"
        + (f"<div class=fix>{html.escape(i['fix'])}</div>" if i["fix"] else "") + "</div></li>"
        for i in sorted(ck.items, key=lambda i: ["fail", "warn", "skip", "pass"].index(i["status"])))
    pic = ""
    if shot and Path(shot).exists():
        pic = (f"<section><h2>What the app sees</h2><img src='data:image/jpeg;base64,{base64.b64encode(Path(shot).read_bytes()).decode()}'>"
               "<p class=muted>Yellow circle = ball on the tee · green arrow = measured flight · blue = your skeleton · "
               "grey line = the camera's level line (your feet should be well below it)</p></section>")
    css = CSS + ".bad .ic{background:#c4314b}.fix{margin-top:2px}.fix:before{content:'Fix: ';font-weight:600}"
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Setup Check</title><style>{css}</style></head><body><main>"
           f"<h1>Setup check</h1><p class=muted>{html.escape(Path(video).name)}</p>"
           f"<section><div class={CLS[ck.verdict]}><h2><span class=ic>{ICON[ck.verdict]}</span> {word}</h2></div>"
           f"<ul class=flags>{rows}</ul></section>{pic}</main></body></html>")
    Path(out_html).write_text(doc, encoding="utf-8")
