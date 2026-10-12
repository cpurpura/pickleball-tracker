"""Write an annotated MP4: skeletons, ball trail, and a label card at every hit."""
import cv2

from .detect import load_tracks
from .draw import overlay_frame, text_panel, SIDE_COLOR
from .report import shot_type
from .session import load_shots
from .video import open_video


def render(shots_path, out_path, hits_only=False, pad_s=1.0):
    data = load_shots(shots_path)
    tr = load_tracks(data["tracks"])
    fps = data["fps"]
    hits = [h for h in data["hits"] if not h.get("deleted")]
    cap = open_video(data["video"])
    w, h = tr["meta"]["width"], tr["meta"]["height"]
    out = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    keep = None
    if hits_only:
        pad = int(pad_s * fps)
        keep = set()
        for x in hits:
            keep.update(range(x["frame"] - pad, x["frame"] + pad))
    hold = int(0.8 * fps)
    f = 0
    while True:
        ok, frame = cap.read()
        if not ok or f >= tr["meta"]["frames"]:
            break
        if keep is None or f in keep:
            img = overlay_frame(frame, tr, f)
            recent = [x for x in hits if 0 <= f - x["frame"] < hold]
            if recent:
                x = recent[-1]
                m = x.get("metrics", {})
                text_panel(img, [
                    (f"{x['player']}: {shot_type(x).upper()}{' (volley)' if x.get('volley') else ''}", SIDE_COLOR[x["side"]]),
                    (f"{m.get('wing', '')}  contact ht {m.get('contact_height', '?')}  hip drop {m.get('hip_drop_pct', '?')}%", (230, 230, 230)),
                ], origin=(20, 20), scale=max(0.6, w / 1920))
            out.write(img)
        f += 1
    cap.release()
    out.release()
    print(f"Annotated video -> {out_path}")
