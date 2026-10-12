"""Interactive court calibration: click the 4 outer court corners once per camera setup."""
import cv2
import numpy as np

from .court import Court, CORNER_NAMES
from .video import read_frame_at


def calibrate(video_path, out_path, at_seconds=1.0, max_width=1600):
    frame = read_frame_at(video_path, at_seconds)
    h, w = frame.shape[:2]
    scale = min(1.0, max_width / w)
    disp_base = cv2.resize(frame, (int(w * scale), int(h * scale)))
    pts = []
    win = "Calibrate court  (click corners | r = reset | Enter = save | q = quit)"

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append((x / scale, y / scale))

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        disp = disp_base.copy()
        for i, (x, y) in enumerate(pts):
            cv2.circle(disp, (int(x * scale), int(y * scale)), 6, (0, 0, 255), -1)
            cv2.putText(disp, str(i + 1), (int(x * scale) + 8, int(y * scale) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if len(pts) < 4:
            msg = f"Click {len(pts) + 1}/4: {CORNER_NAMES[len(pts)]}"
        else:
            court = Court(pts)
            overlay = court.draw_lines(frame.copy(), (0, 255, 255), 3)
            disp = cv2.resize(overlay, disp.shape[1::-1])
            msg = "Yellow lines should sit on the court lines. Enter = save, r = redo"
        cv2.rectangle(disp, (0, 0), (disp.shape[1], 40), (0, 0, 0), -1)
        cv2.putText(disp, msg, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.imshow(win, disp)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("r"):
            pts.clear()
        elif k in (13, 10) and len(pts) == 4:
            Court(pts).save(out_path)
            print(f"Saved calibration -> {out_path}")
            break
        elif k == ord("q") or k == 27:
            print("Calibration cancelled.")
            break
    cv2.destroyAllWindows()
