"""Footwork metrics test: a 3D hitter projected through a pinhole camera, with pose jitter.

The camera sees the hitter in profile (hitting line across the frame), so moving sideways
along the kitchen line means moving toward the camera, and a square stance puts the feet
one behind the other along the camera's line of sight.

    python tests/footwork_test.py
"""
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pb.tee import BALL_DIAMETER_IN, footwork  # noqa: E402

FPS, W, H = 60, 1280, 720
F_PX = 1400.0          # focal length in pixels
CAM_H, CAM_Z = 3.0, 12.0
C = 300                # contact frame


def project(x, y, z):
    """World feet (x across, y up, z away from camera) -> image pixels."""
    return W / 2 + F_PX * x / z, H / 2 + F_PX * (CAM_H - y) / z


def hitter(z_mid, width_in, stagger_in, head_fwd_in, rng):
    """COCO keypoints for a player at depth z_mid, facing +x (the target)."""
    w, s, hf = width_in / 12, stagger_in / 12, head_fwd_in / 12
    bx = -2.0  # body is 2 ft behind the ball along the hitting line
    pts = {15: (bx - s / 2, 0.29, z_mid + w / 2), 16: (bx + s / 2, 0.29, z_mid - w / 2),
           13: (bx - s / 2, 1.5, z_mid + w / 2), 14: (bx + s / 2, 1.5, z_mid - w / 2),
           11: (bx, 2.6, z_mid + 0.45), 12: (bx, 2.6, z_mid - 0.45),
           5: (bx + hf / 2, 4.2, z_mid + 0.7), 6: (bx + hf / 2, 4.2, z_mid - 0.7),
           0: (bx + hf, 5.0, z_mid), 3: (bx + hf, 5.0, z_mid + 0.25), 4: (bx + hf, 5.0, z_mid - 0.25)}
    kp = np.zeros((17, 3), np.float32)
    for i, p in pts.items():
        kp[i] = (*project(*p), 0.9)
    kp[:, :2] += rng.normal(0, 1.0, (17, 2))  # pose jitter
    kp[[1, 2, 7, 8, 9, 10], 2] = 0.0          # unused joints
    return kp, np.zeros(4, np.float32)


def session(stop_frame, width_in=20, stagger_in=0, head_fwd_in=2, start_z=16.0, seed=0):
    """Shuffle from start_z to CAM_Z between frames C-70 and stop_frame, then hold."""
    rng = np.random.default_rng(seed)
    P = {}
    for f in range(C - 100, C + 16):
        k = np.clip((f - (C - 70)) / (stop_frame - (C - 70)), 0, 1)
        P[f] = hitter(start_z + (CAM_Z - start_z) * k, width_in, stagger_in, head_fwd_in, rng)
    return P


def main():
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    t = {**cfg["tee"], "camera_distance_ft": CAM_Z, "camera_height_ft": CAM_H}
    r_px = F_PX / CAM_Z / 12 * BALL_DIAMETER_IN / 2   # resting ball radius at the tee
    rep = {"frame": C, "ball_r_px": r_px, "tee_xy": list(project(0, 2.5, CAM_Z))}
    cases = [  # (label, session, expected)
        ("shuffle in, set 0.4 s", session(C - 24),
         dict(feet_set_s=0.40, moved_ft=4.0, stance_in=20, stagger_in=0, head_over_base_in=2, ahead_of_front_foot_in=24)),
        ("still moving at contact", session(C + 10), dict(feet_set_s=0.0)),
        ("stand still, staggered", session(C - 200, width_in=16, stagger_in=6, head_fwd_in=-1, start_z=CAM_Z),
         dict(feet_set_s=1.5, moved_ft=0.0, stance_in=17, stagger_in=6, head_over_base_in=-1, ahead_of_front_foot_in=21)),
    ]
    tol = dict(feet_set_s=0.07, moved_ft=0.4, stance_in=2.5, stagger_in=1.5, head_over_base_in=1.0, ahead_of_front_foot_in=1.0)
    ok = True
    for label, P, want in cases:
        m = footwork(rep, P, FPS, 1, t, (W, H))
        bad = {k: (m.get(k), v) for k, v in want.items() if m.get(k) is None or abs(m[k] - v) > tol[k]}
        print(f"{label:26s} {m}" + (f"   MISMATCH (got, want): {bad}" if bad else ""))
        ok &= not bad
    assert ok, "footwork metrics regressed"
    print("OK")


if __name__ == "__main__":
    main()
