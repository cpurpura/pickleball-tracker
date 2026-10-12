#!/usr/bin/env python3
"""Pickleball shot breakdown from video.

Typical workflow for a new video:
    python pbshot.py calibrate  drill.mp4          # click the 4 court corners (once per camera setup)
    python pbshot.py tune-audio drill.mp4          # optional: check the paddle-pop detector
    python pbshot.py analyze    drill.mp4          # pose + ball tracking, hit detection, metrics
    python pbshot.py review     sessions/drill.shots.json   # confirm / fix / tag each shot
    python pbshot.py report                        # HTML report over all sessions
    python pbshot.py train                         # after ~30+ tagged shots: learn your shot types
Or just:  python pbshot.py all drill.mp4

Solo practice off a batting tee:
    python pbshot.py tee practice.mp4 --shot "forehand drive" --tee-height 32 --board-distance 12
"""
import argparse
import glob
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SESS = ROOT / "sessions"


def load_cfg(path):
    with open(path) as f:
        return yaml.safe_load(f)


def stem(video):
    return Path(video).stem


def calib_for(video, explicit=None):
    if explicit:
        return Path(explicit)
    per_video = SESS / f"{stem(video)}.calib.json"
    return per_video if per_video.exists() else ROOT / "calib.json"


def cmd_calibrate(a, cfg):
    from pb.calibrate import calibrate
    out = Path(a.out) if a.out else ROOT / "calib.json"
    calibrate(a.video, out, a.at)


def cmd_tune(a, cfg):
    from pb.ball import tune_ball_color
    tune_ball_color(a.video, cfg, a.at)


def cmd_tune_audio(a, cfg):
    from pb.audio import tune_audio
    png = str(SESS / f"{stem(a.video)}.audio.png") if a.plot else None
    if png:
        SESS.mkdir(exist_ok=True)
    tune_audio(a.video, cfg, a.max_seconds, png)


def cmd_analyze(a, cfg):
    from pb.detect import analyze_video
    from pb.session import build_shots, players_from_cfg
    SESS.mkdir(exist_ok=True)
    calib = calib_for(a.video, a.calib)
    if not calib.exists():
        print("No court calibration found - opening the calibration tool first.")
        from pb.calibrate import calibrate
        calibrate(a.video, calib)
        if not calib.exists():
            sys.exit("Calibration is required.")
    tracks = SESS / f"{stem(a.video)}.tracks.npz"
    if a.redo or not tracks.exists():
        print(f"[1/2] Tracking players + ball in {a.video} ...")
        analyze_video(a.video, calib, cfg, tracks, a.max_seconds)
    else:
        print(f"[1/2] Reusing {tracks.name} (use --redo to re-track)")
    print("[2/2] Detecting hits, bounces and measuring mechanics ...")
    players = players_from_cfg(cfg, a.near, a.far)
    shots = SESS / f"{stem(a.video)}.shots.json"
    if shots.exists() and not a.redo:
        from pb.session import load_shots
        if any(h.get("type") for h in load_shots(shots)["hits"]):
            print(f"  {shots.name} already has your labels - keeping it (use --redo to rebuild from scratch).")
            return shots
    build_shots(tracks, cfg, players, ROOT / cfg["model_path"], shots)
    return shots


def cmd_review(a, cfg):
    from pb.review import review
    from pb.session import rebuild_metrics
    review(a.shots, cfg, a.start, a.unlabeled)
    rebuild_metrics(a.shots, cfg, ROOT / cfg["model_path"])


def _shot_files(given):
    files = given or sorted(glob.glob(str(SESS / "*.shots.json")))
    if not files:
        sys.exit("No sessions found. Run `analyze` on a video first.")
    return files


def cmd_train(a, cfg):
    from pb.classify import train
    from pb.session import rebuild_metrics
    files = _shot_files(a.shots)
    if train(files, ROOT / cfg["model_path"], a.min_labels):
        for f in files:  # refresh auto labels with the new model
            rebuild_metrics(f, cfg, ROOT / cfg["model_path"])


def cmd_report(a, cfg):
    from pb.report import build_report
    build_report(_shot_files(a.shots), cfg, a.out)


def cmd_render(a, cfg):
    from pb.render import render
    out = a.out or str(Path(a.shots).with_suffix("").with_suffix("")) + ".annotated.mp4"
    render(a.shots, out, a.hits_only)


def cmd_tee(a, cfg):
    from pb.tee import analyze_tee
    t = cfg["tee"]
    for key, v in (("shot", a.shot), ("tee_height_in", a.tee_height), ("target", a.target),
                   ("board_distance_ft", a.board_distance), ("camera_distance_ft", a.camera_distance)):
        if v is not None:
            t[key] = v
    name = a.player or cfg["players"]["near"]
    player = {"name": name, "hand": cfg["players"].get("handedness", {}).get(name, "right")}
    analyze_tee(a.video, cfg, SESS, player, a.max_seconds, a.redo)
    a.shots, a.out = None, str(ROOT / "tee_report.html")
    cmd_tee_report(a, cfg)


def cmd_tee_report(a, cfg):
    from pb.tee_report import build_tee_report
    files = a.shots or sorted(glob.glob(str(SESS / "*.tee.json")))
    if not files:
        sys.exit("No tee sessions found. Run `tee` on a video first.")
    build_tee_report(files, cfg, a.out)


def cmd_all(a, cfg):
    shots = cmd_analyze(a, cfg)
    a.shots, a.start, a.unlabeled = str(shots), 0, False
    cmd_review(a, cfg)
    a.shots, a.out = None, str(ROOT / "report.html")
    cmd_report(a, cfg)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("calibrate", help="click the 4 court corners")
    s.add_argument("video"); s.add_argument("--out"); s.add_argument("--at", type=float, default=1.0, help="seconds into video")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("tune-ball", help="find the HSV color range for your ball")
    s.add_argument("video"); s.add_argument("--at", type=float, default=5.0)
    s.set_defaults(fn=cmd_tune)

    s = sub.add_parser("tune-audio", help="list the paddle pops found in a video's sound")
    s.add_argument("video"); s.add_argument("--max-seconds", type=float, default=60.0)
    s.add_argument("--plot", action="store_true", help="also save sessions/<video>.audio.png")
    s.set_defaults(fn=cmd_tune_audio)

    for name, fn in (("analyze", cmd_analyze), ("all", cmd_all)):
        s = sub.add_parser(name, help="track + detect shots" if name == "analyze" else "analyze, review, report")
        s.add_argument("video")
        s.add_argument("--calib", help="calibration json (default: calib.json)")
        s.add_argument("--near", help="name of the player on the camera side")
        s.add_argument("--far", help="name of the player on the far side")
        s.add_argument("--max-seconds", type=float, help="only process the first N seconds (for testing)")
        s.add_argument("--redo", action="store_true", help="re-run tracking even if cached")
        s.set_defaults(fn=fn)

    s = sub.add_parser("tee", help="solo practice off a batting tee: find reps, measure, report")
    s.add_argument("video")
    s.add_argument("--shot", help='label for this session, e.g. "backhand drive" or "serve"')
    s.add_argument("--tee-height", type=float, help="tee height in inches")
    s.add_argument("--target", choices=["board", "net"])
    s.add_argument("--board-distance", type=float, help="feet from the tee to the rebound board")
    s.add_argument("--camera-distance", type=float, help="feet from the phone to the tee")
    s.add_argument("--player", help="whose session this is (for handedness)")
    s.add_argument("--max-seconds", type=float, help="only process the first N seconds (for testing)")
    s.add_argument("--redo", action="store_true", help="rescan the video even if cached")
    s.set_defaults(fn=cmd_tee)

    s = sub.add_parser("tee-report", help="rebuild tee_report.html from every tee session")
    s.add_argument("shots", nargs="*"); s.add_argument("--out", default=str(ROOT / "tee_report.html"))
    s.set_defaults(fn=cmd_tee_report)

    s = sub.add_parser("review", help="step through hits, fix and tag them")
    s.add_argument("shots"); s.add_argument("--start", type=int, default=0)
    s.add_argument("--unlabeled", action="store_true", help="skip hits you already tagged")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("train", help="learn shot types from your tags")
    s.add_argument("shots", nargs="*"); s.add_argument("--min-labels", type=int, default=30)
    s.set_defaults(fn=cmd_train)

    s = sub.add_parser("report", help="build the HTML report")
    s.add_argument("shots", nargs="*"); s.add_argument("--out", default=str(ROOT / "report.html"))
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("render", help="write an annotated video")
    s.add_argument("shots"); s.add_argument("--out"); s.add_argument("--hits-only", action="store_true")
    s.set_defaults(fn=cmd_render)

    a = p.parse_args()
    a.fn(a, load_cfg(a.config))


if __name__ == "__main__":
    main()
