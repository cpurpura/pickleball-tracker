"""Hit detection from the soundtrack.

A paddle striking a pickleball makes a short, sharp "pop": it starts within a millisecond
or two, is broadband, and dies away fast. Once the low frequencies are cut (voices,
footsteps, HVAC hum) it stands out clearly, and it times contact far more precisely than
the ball tracker can. events.fuse_audio uses these pops to snap video hits to the exact
contact frame, add hits the ball tracker missed, and flag video "hits" with no pop as
likely bounces.

Audio is decoded with ffmpeg: the system copy if it's on PATH, otherwise the one bundled
with the imageio-ffmpeg package.
"""
import shutil
import subprocess

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, find_peaks, sosfiltfilt

SPEED_OF_SOUND_FTS = 1125.0
ATTACK_S = 0.010   # a pop reaches its peak within this long
DECAY_S = 0.040    # ...and has dropped off by this long after the peak


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def load_audio(video_path, sr, max_seconds=None):
    """Mono float32 samples of the video's soundtrack, or None if there's no audio."""
    exe = ffmpeg_exe()
    if exe is None:
        print("  (audio) ffmpeg not found - run `pip install imageio-ffmpeg`. Using video only.")
        return None
    cmd = [exe, "-v", "error", "-i", str(video_path), "-vn", "-ac", "1", "-ar", str(sr)]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.3f}"]
    cmd += ["-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not res.stdout:
        return None
    return np.frombuffer(res.stdout, np.float32)


def detect_onsets(x, sr, a, debug=False):
    """Find paddle pops in mono samples x.

    Returns an array (N, 2) of [time_s, strength_db], where strength is relative to the
    session's loud pops (0 = as loud as a typical hard hit, -20 = 20 dB quieter).
    With debug=True also returns a dict with the envelope, for tune-audio's plot.
    """
    empty = np.zeros((0, 2))
    if x is None or len(x) < sr * 0.2:
        return (empty, None) if debug else empty
    sos = butter(4, a["highpass_hz"], "highpass", fs=sr, output="sos")
    y = sosfiltfilt(sos, x.astype(np.float64))
    hop = max(1, int(round(sr * a["hop_s"])))
    hs = hop / sr
    n = len(y) // hop
    db = 10 * np.log10((y[: n * hop] ** 2).reshape(n, hop).mean(axis=1) + 1e-12)

    # background level: rolling median, computed on a coarse grid for speed
    step = 10
    size = max(3, int(a["floor_window_s"] / (hs * step)) | 1)
    bg = np.repeat(median_filter(db[::step], size=size, mode="nearest"), step)[:n]
    snr = db - bg

    att = max(1, int(round(ATTACK_S / hs)))
    dec = max(1, int(round(DECAY_S / hs)))
    peaks, _ = find_peaks(snr, height=a["min_snr_db"], distance=max(1, int(a["min_gap_s"] / hs)))
    times, levels = [], []
    for p in peaks:
        pre = db[max(0, p - 3 * att):max(1, p - att)]
        post = db[p + dec:p + 2 * dec]
        if len(pre) == 0 or len(post) == 0:
            continue
        if db[p] - np.median(pre) < a["min_rise_db"]:
            continue  # didn't start suddenly
        if db[p] - np.median(post) < a["min_decay_db"]:
            continue  # sustained (squeak, voice, music), not a pop
        # onset = start of the attack, not the peak
        i = p
        while i > p - att and i > 0 and db[i - 1] > db[p] - 10:
            i -= 1
        times.append(i * hs)
        levels.append(db[p])
    if not times:
        out = empty
    else:
        levels = np.array(levels)
        rel = levels - np.percentile(levels, 90)
        keep = rel >= a["min_rel_db"]
        out = np.column_stack([np.array(times)[keep], rel[keep]])
    if debug:
        return out, {"t": np.arange(n) * hs, "snr": snr}
    return out


def video_onsets(video_path, a, max_seconds=None):
    """Pops in a video's soundtrack (see detect_onsets), or None if it has no audio."""
    x = load_audio(video_path, a["sample_rate"], max_seconds)
    if x is None:
        print("  (audio) no soundtrack found - using video only.")
        return None
    on = detect_onsets(x, a["sample_rate"], a)
    print(f"  (audio) {len(on)} paddle pops found in {len(x) / a['sample_rate']:.0f}s of sound")
    return on


def tune_audio(video_path, cfg, max_seconds=60.0, out_png=None):
    """Print the pops found and plot the envelope, to check the audio settings."""
    a = cfg["audio"]
    x = load_audio(video_path, a["sample_rate"], max_seconds)
    if x is None:
        print("No soundtrack found in this video.")
        return
    on, dbg = detect_onsets(x, a["sample_rate"], a, debug=True)
    print(f"{len(on)} pops in the first {len(x) / a['sample_rate']:.0f}s:")
    for t, rel in on:
        print(f"  {t:8.3f}s  {rel:6.1f} dB")
    if out_png and dbg is not None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(16, 4))
        ax.plot(dbg["t"], dbg["snr"], lw=0.5, color="#555")
        ax.axhline(a["min_snr_db"], color="#c33", lw=1, ls="--", label="min_snr_db")
        for t, _ in on:
            ax.axvline(t, color="#2a7", lw=0.8, alpha=0.7)
        ax.set_xlabel("seconds")
        ax.set_ylabel("dB above background")
        ax.set_ylim(-10, max(40, float(np.max(dbg["snr"])) + 5))
        ax.legend(loc="upper right")
        fig.tight_layout()
        fig.savefig(out_png, dpi=110)
        print(f"Plot -> {out_png}  (green lines = detected pops)")
