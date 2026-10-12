"""Review/tag tool: loop each detected hit in slow motion, confirm or fix it.

Your labels are what the shot-type model learns from, so a few sessions of tagging
makes the automatic labels much better.
"""
import time

import cv2
import numpy as np

from .classify import SHOT_TYPES
from .detect import load_tracks
from .draw import overlay_frame, text_panel, SIDE_COLOR
from .session import load_shots, save_shots
from .video import FrameCache

KEYS_HELP = [
    "1-9,0 label type (auto-advance) | Enter accept auto label",
    "n next  b back  | space pause  , . step frame  | j l jump -/+1s",
    "c set contact = this frame  | a add hit here  | x delete/restore",
    "v toggle volley  | p swap player  | s save  | q save+quit",
]


def review(shots_path, cfg, start_at=0, only_unlabeled=False):
    data = load_shots(shots_path)
    tr = load_tracks(data["tracks"])
    fps = data["fps"]
    cache = FrameCache(data["video"])
    n_frames = tr["meta"]["frames"]
    hits = data["hits"]
    rc = cfg["review"]
    half = int(rc["window_s"] * fps)
    slow = rc["slowmo"]
    maxw = rc["max_display_width"]

    def next_index(i, step=1):
        j = i + step
        while only_unlabeled and 0 <= j < len(hits) and hits[j].get("type"):
            j += step
        return max(0, min(len(hits) - 1, j)) if hits else 0

    i = min(start_at, max(0, len(hits) - 1))
    if only_unlabeled and hits and hits[i].get("type"):
        i = next_index(i)
    focus = hits[i]["frame"] if hits else 0
    cur = max(0, focus - half)
    paused = False
    dirty = False
    win = "Pickleball review"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    last = time.time()

    while True:
        frame = cache.get(cur)
        if frame is None:
            cur = max(0, focus - half)
            continue
        img = overlay_frame(frame.copy(), tr, cur)
        h = hits[i] if hits else None
        lines = []
        if h is not None:
            is_contact = abs(cur - h["frame"]) <= 1
            if is_contact:
                cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (0, 255, 0), 12)
            m = h.get("metrics", {})
            col = SIDE_COLOR[h["side"]]
            status = "DELETED" if h.get("deleted") else ""
            lines += [
                (f"Hit {i + 1}/{len(hits)}  t={h['frame'] / fps:6.2f}s  rally {h.get('rally', '-')}  {status}", (255, 255, 255)),
                (f"Player: {h['player']} ({h['side']})   source: {h.get('source')}"
                 + {True: "  +pop", False: "  NO POP (bounce?)"}.get(h.get("audio"), ""), col),
                (f"Auto: {h.get('type_auto')}" + (f" ({h['type_conf']:.0%})" if h.get("type_conf") else "")
                 + f"   YOUR LABEL: {h.get('type') or '-'}   volley: {'yes' if h.get('volley') else 'no'}", (0, 255, 255)),
                (f"wing {m.get('wing', '?')} | contact ht {m.get('contact_height', '?')} | knee {m.get('knee_angle', '?')} deg"
                 f" | hip drop {m.get('hip_drop_pct', '?')}%", (220, 220, 220)),
                (f"from net {m.get('dist_from_net_ft', '?')} ft | speed {m.get('speed_fts', '?')} ft/s"
                 f" | landed {m.get('landing_from_net_ft', '?')} ft from net", (220, 220, 220)),
                (f"frame {cur}  {'[CONTACT]' if is_contact else ''}  {'PAUSED' if paused else f'{slow}x'}", (180, 180, 180)),
            ]
        else:
            lines.append(("No hits detected. Use j/l to scrub and 'a' to add hits.", (255, 255, 255)))
        lines += [("", (0, 0, 0))] + [(k, (160, 200, 160)) for k in KEYS_HELP]
        scale = img.shape[1] / 1920 * 0.75
        text_panel(img, [("  ".join(SHOT_TYPES_KEYS), (255, 255, 255))] + lines, scale=max(scale, 0.45))
        if img.shape[1] > maxw:
            img = cv2.resize(img, (maxw, int(img.shape[0] * maxw / img.shape[1])))
        cv2.imshow(win, img)

        wait = max(1, int(1000 / (fps * slow) - (time.time() - last) * 1000))
        k = cv2.waitKey(wait if not paused else 30) & 0xFF
        last = time.time()

        if k == 255:
            if not paused:
                cur += 1
                if cur > min(focus + half, n_frames - 1):
                    cur = max(0, focus - half)
            continue
        if k == ord("q") or k == 27:
            break
        if k == ord(" "):
            paused = not paused
        elif k == ord("."):
            paused, cur = True, min(cur + 1, n_frames - 1)
        elif k == ord(","):
            paused, cur = True, max(cur - 1, 0)
        elif k == ord("j"):
            focus = max(0, focus - int(fps)); cur = max(0, focus - half)
        elif k == ord("l"):
            focus = min(n_frames - 1, focus + int(fps)); cur = max(0, focus - half)
        elif k == ord("s"):
            save_shots(data, shots_path); dirty = False
            print("saved")
        elif k == ord("a"):
            side = _guess_side(tr, cur)
            new = {"frame": int(cur), "side": side, "source": "manual", "conf": 1.0,
                   "player": data["players"][side]["name"], "type": None, "deleted": False,
                   "metrics": {}, "volley": False}
            hits.append(new)
            hits.sort(key=lambda x: x["frame"])
            i = hits.index(new); focus = cur; dirty = True
        elif h is not None:
            if k in (ord("n"),):
                i = next_index(i); focus = hits[i]["frame"]; cur = max(0, focus - half)
            elif k == ord("b"):
                i = next_index(i, -1); focus = hits[i]["frame"]; cur = max(0, focus - half)
            elif k == ord("c"):
                h["frame"] = int(cur); focus = cur; dirty = True
            elif k == ord("x"):
                h["deleted"] = not h.get("deleted"); dirty = True
            elif k == ord("v"):
                h["volley"] = not h.get("volley"); h["volley_manual"] = True; dirty = True
            elif k == ord("p"):
                h["side"] = "far" if h["side"] == "near" else "near"
                h["player"] = data["players"][h["side"]]["name"]; dirty = True
            elif k in (13, 10) and h.get("type_auto"):
                h["type"] = h["type_auto"]; dirty = True
                i = next_index(i); focus = hits[i]["frame"]; cur = max(0, focus - half)
            elif chr(k) in TYPE_KEYS:
                h["type"] = TYPE_KEYS[chr(k)]; dirty = True
                i = next_index(i); focus = hits[i]["frame"]; cur = max(0, focus - half)

    save_shots(data, shots_path)
    cache.release()
    cv2.destroyAllWindows()
    labelled = sum(1 for h in hits if h.get("type") and not h.get("deleted"))
    print(f"Saved. {labelled}/{len(hits)} hits labelled.")
    return dirty or True


TYPE_KEYS = {str((n + 1) % 10): t for n, t in enumerate(SHOT_TYPES)}
SHOT_TYPES_KEYS = [f"{k}={t}" for k, t in TYPE_KEYS.items()]


def _guess_side(tr, f):
    b = tr["ball"][f]
    best, bd = "near", np.inf
    for s in ("near", "far"):
        box = tr[f"box_{s}"][f]
        if np.isnan(box[0]):
            continue
        c = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])
        d = np.hypot(*(c - b)) if not np.isnan(b[0]) else (0 if s == "near" else 1)
        if d < bd:
            best, bd = s, d
    return best
