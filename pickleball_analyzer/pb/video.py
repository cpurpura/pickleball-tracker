"""Small video I/O helpers."""
import cv2


def open_video(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {path}")
    return cap


def video_info(path):
    cap = open_video(path)
    info = {
        "fps": cap.get(cv2.CAP_PROP_FPS) or 30.0,
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    cap.release()
    return info


def read_frame_at(path, seconds=0.0):
    cap = open_video(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(seconds * fps))
    ok, frame = cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError("Could not read a frame from the video")
    return frame


class FrameCache:
    """Random access to frames with a small cache (for the review UI)."""

    def __init__(self, path, size=240):
        self.cap = open_video(path)
        self.size = size
        self.cache = {}
        self.pos = -1

    def get(self, idx):
        if idx in self.cache:
            return self.cache[idx]
        if idx != self.pos + 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self.cap.read()
        self.pos = idx
        if not ok:
            return None
        if len(self.cache) > self.size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[idx] = frame
        return frame

    def release(self):
        self.cap.release()
