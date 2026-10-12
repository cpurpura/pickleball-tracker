"""Overlay drawing shared by the review tool and the annotated-video renderer."""
import cv2
import numpy as np

SKELETON = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16)]
SIDE_COLOR = {"near": (255, 180, 0), "far": (0, 140, 255)}  # BGR


def draw_pose(img, kp, color, min_conf=0.3):
    if kp is None or np.isnan(kp[:, 0]).all():
        return
    for a, b in SKELETON:
        if kp[a, 2] >= min_conf and kp[b, 2] >= min_conf and not np.isnan(kp[a, 0]) and not np.isnan(kp[b, 0]):
            cv2.line(img, tuple(kp[a, :2].astype(int)), tuple(kp[b, :2].astype(int)), color, 3, cv2.LINE_AA)
    for i in range(5, 17):
        if kp[i, 2] >= min_conf and not np.isnan(kp[i, 0]):
            cv2.circle(img, tuple(kp[i, :2].astype(int)), 4, (255, 255, 255), -1)


def draw_ball_trail(img, ball, f, length=15):
    pts = [(i, ball[i]) for i in range(max(0, f - length), f + 1) if not np.isnan(ball[i, 0])]
    for j in range(1, len(pts)):
        if pts[j][0] - pts[j - 1][0] <= 3:
            a = (j / len(pts))
            cv2.line(img, tuple(pts[j - 1][1].astype(int)), tuple(pts[j][1].astype(int)),
                     (0, int(255 * a), 255), 2, cv2.LINE_AA)
    if 0 <= f < len(ball) and not np.isnan(ball[f, 0]):
        cv2.circle(img, tuple(ball[f].astype(int)), 9, (0, 255, 255), 2, cv2.LINE_AA)


def overlay_frame(img, tr, f, show_court=True):
    if show_court:
        faint = img.copy()
        tr["court"].draw_lines(faint, (255, 255, 255), 1)
        cv2.addWeighted(faint, 0.4, img, 0.6, 0, img)
    for s in ("near", "far"):
        if f < len(tr[f"kp_{s}"]):
            draw_pose(img, tr[f"kp_{s}"][f], SIDE_COLOR[s])
    draw_ball_trail(img, tr["ball"], min(f, len(tr["ball"]) - 1))
    return img


def text_panel(img, lines, origin=(10, 10), scale=0.6, width=None):
    lh = int(28 * scale / 0.6)
    w = width or max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] for t, _ in lines) + 20
    x, y = origin
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + lh * len(lines) + 10), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    for i, (t, c) in enumerate(lines):
        cv2.putText(img, t, (x + 10, y + lh * (i + 1)), cv2.FONT_HERSHEY_SIMPLEX, scale, c, 1, cv2.LINE_AA)
    return img
