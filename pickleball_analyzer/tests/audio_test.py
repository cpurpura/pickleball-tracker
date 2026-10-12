"""Paddle-pop detector test on a synthetic soundtrack (no real footage needed).

Mixes hard and soft paddle pops with things that must NOT count as hits: a voice, a shoe
squeak, a metal clang, low ball/foot thuds and room echo. Then muxes it into an AAC .mp4
the way a phone would, and checks the pops come back out at the right times.

    python tests/audio_test.py
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pb.audio import detect_onsets, ffmpeg_exe, video_onsets  # noqa: E402

OUT = ROOT / "tests" / "out"
SR = 44100
DUR = 20.0
POPS = [1.0, 2.1, 3.4, 5.0, 6.2, 7.3, 9.5, 10.6, 12.5, 15.0, 16.2, 17.7]
THUDS = [1.5, 5.6, 11.2]      # ball bounce / footstep: mostly low frequency
SQUEAK = (8.0, 0.25)          # shoe squeak: sustained tone
RING = 19.0                   # paddle clanging a metal pole: sudden, but rings on
VOICE = [(3.0, 5.0), (12.0, 14.0)]


def synth(rng):
    t = np.arange(int(DUR * SR)) / SR
    x = rng.normal(0, 0.002, len(t))
    for a, b in VOICE:
        m = (t >= a) & (t < b)
        x[m] += 0.05 * np.sin(2 * np.pi * 180 * t[m]) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t[m]))
    m = (t >= SQUEAK[0]) & (t < SQUEAK[0] + SQUEAK[1])
    x[m] += 0.05 * np.sin(2 * np.pi * 2800 * t[m])
    m = t >= RING
    x[m] += 0.3 * np.sin(2 * np.pi * 2300 * t[m]) * np.exp(-(t[m] - RING) / 0.3)
    k = np.arange(int(0.12 * SR)) / SR
    for i, p in enumerate(POPS):
        amp = 0.5 if i % 2 == 0 else 0.12   # drives vs. dinks (~12 dB quieter)
        burst = rng.normal(0, 1, len(k)) * (np.exp(-k / 0.003) + 0.05 * np.exp(-k / 0.025))  # pop + room echo
        s = int(p * SR)
        x[s:s + len(k)] += amp * burst
    lp = butter(4, 300, "lowpass", fs=SR, output="sos")
    for p in THUDS:
        s = int(p * SR)
        x[s:s + len(k)] += 0.3 * sosfilt(lp, rng.normal(0, 1, len(k)) * np.exp(-k / 0.02))
    return np.clip(x, -1, 1).astype(np.float32)


def check(on, label, tol):
    got = on[:, 0]
    missed = [p for p in POPS if np.min(np.abs(got - p)) > tol] if len(got) else POPS
    extra = [g for g in got if np.min(np.abs(np.array(POPS) - g)) > tol]
    err = [abs(g - POPS[np.argmin(np.abs(np.array(POPS) - g))]) * 1000 for g in got]
    print(f"{label}: {len(got)} pops, missed {missed}, extra {np.round(extra, 3).tolist()}, "
          f"timing error max {max(err, default=0):.1f} ms")
    assert not missed and not extra, f"{label}: detector regressed"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    a = cfg["audio"]
    x = synth(np.random.default_rng(0))

    check(detect_onsets(x, SR, a), "raw samples", 0.004)

    wav, mp4 = OUT / "pops.wav", OUT / "pops.mp4"
    wavfile.write(wav, SR, (x * 32767).astype(np.int16))
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=60",
                    "-i", str(wav), "-shortest", "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "128k", str(mp4)], check=True)
    check(video_onsets(mp4, a), "AAC .mp4", 0.008)
    print("OK")


if __name__ == "__main__":
    main()
