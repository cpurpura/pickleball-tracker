# Pickleball Shot Analyzer

Breaks down your drilling videos shot by shot, for **both players**:

- **Body mechanics at contact:** contact height, knee angle, hip drop (how low you get), elbow angle, stance width, torso lean, reach, forehand/backhand, and whether your paddle was up when your partner hit.
- **Ball placement:** where each shot landed on the court, where you hit it from, how many dinks and drops landed in the kitchen, and how deep your returns went.
- **Shot types:** serve, return, dink, drop, drive, lob, reset, speedup, overhead. These start as rule-based guesses. You confirm them in a quick review tool, and after about 30 tagged shots it trains a model on your labels.
- **Output:** an HTML report (with trends across sessions) and, if you want it, an annotated video.

## 1. Filming setup

This setup makes the biggest difference to accuracy.

| | |
|---|---|
| **Position** | Behind one baseline, centered on the court |
| **Height** | As high as you can get: 6–10 ft (tripod with an extension pole, a fence or wall mount, or bleachers). Higher is better for landing spots. |
| **Framing** | Landscape, with **all 4 court corners in frame** and some room above the far baseline for lobs |
| **Settings** | **60 fps**, 1080p. Lock exposure/focus if your phone allows it. Avoid shooting into windows or bright lights. |
| **Don't move the camera** | Calibration assumes the camera stays still for the whole video |

The near player (closest to the camera) gets the most accurate body mechanics. **Switch sides halfway through the drill** so you both get close-up footage.

## 2. Install (Windows, Mac or Linux)

```bash
cd pickleball_analyzer
python -m venv .venv
.venv\Scripts\activate          # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

If you have an NVIDIA GPU, install the CUDA build of PyTorch first (see pytorch.org) for about a 10× speedup. It also runs on CPU, just slower. Setting `pose.every_n_frames: 2` in `config.yaml` halves the processing time.

## 3. Set your names

In `config.yaml`, under `players:`, set who is on the near and far side and each player's handedness. You can override the sides for a single video with `--near Chad --far Partner`.

## 4. Workflow

```bash
python pbshot.py tune-ball drill.mp4        # one time: set the HSV range for your ball + gym lighting
python pbshot.py calibrate drill.mp4        # click 4 court corners (redo whenever the camera moves)
python pbshot.py tune-audio drill.mp4 --plot   # optional: check the paddle-pop detector
python pbshot.py analyze   drill.mp4        # tracking + hit/bounce detection + mechanics
python pbshot.py review    sessions/drill.shots.json
python pbshot.py report                     # builds report.html from every session
python pbshot.py render    sessions/drill.shots.json --hits-only   # optional annotated video
python pbshot.py train                      # once you have ~30+ tagged shots
```

To run analyze → review → report in one step: `python pbshot.py all drill.mp4`.

Tip: test on a short clip first with `analyze drill.mp4 --max-seconds 60`.

### Review tool keys

| Key | Action |
|---|---|
| `1`–`9`, `0` | Tag the shot type (serve, return, dink, drop, drive, lob, reset, speedup, overhead, other) and move to the next hit |
| `Enter` | Accept the auto label |
| `n` / `b` | Next / previous hit |
| `space`, `,` `.` | Pause, step one frame |
| `c` | Set contact to the current frame (if a hit's timing is off) |
| `a` | Add a missed hit at the current frame (use `j`/`l` to scrub ±1 s) |
| `x` | Delete a false hit (press again to restore) |
| `v` / `p` | Toggle volley / swap which player hit it |
| `s` / `q` | Save / save and quit |

Use `review ... --unlabeled` to skip shots you've already tagged.

## Tee mode (solo practice)

Hit balls off a batting tee into a rebound board (such as a Dink Master) or a net, and get rep-by-rep feedback on your contact point. No court, no partner and no calibration needed.

```bash
python pbshot.py tee practice.mp4 --shot "forehand drive" --tee-height 32 --board-distance 12
python pbshot.py tee-report          # rebuild tee_report.html from every tee session
```

**Filming:** put the phone on a tripod **facing your chest** (face-on), about **12 ft from the tee** with the lens at about tee height. The hitting line should run straight across the frame. Get your whole body, the tee and a few feet of the ball's flight in frame. Record ordinary **60 fps** video with sound. Slow-motion modes often drop or stretch the audio. Keep other ball-colored things away from the tee.

**Board or net?**
- **Board:** the pop of the ball hitting the board gives an accurate average ball speed, worked out from sound and the tee-to-board distance. The rebound also brings the ball back to you. Measure from the tee to the face of the board along the hitting line and pass it as `--board-distance`. About 12–15 ft works for drives.
- **Net:** quieter and safer for full-power drives. You still get launch speed and angle from the video, but not board speed. Use `--target net`.

**Tee height and shots (26–39 in tee):** waist-height drives and serves fit this range well. A legal serve is contacted below the navel, which for most adults means the lower end. Dinks and third-shot drops are usually hit below the knee, lower than the tee goes. Practice those against the board without the tee.

**What you get per rep:**
- how far the ball was in front of your front foot and hips (inches)
- contact height on your body (0 = hips, 1 = shoulders)
- paddle-hand speed
- launch speed and angle (from video), plus speed to the board (from sound)
- knee bend, hip drop, elbow angle, stance width

The report adds the spread of each measure (how repeatable you are), a contact-frame picture of every rep, and trends across sessions.

**How reps are found:** a ball that rests on the tee, then disappears at the same moment as a paddle pop. Balls lying on the floor, someone walking in front of the camera, or picking a ball off the tee without hitting it don't count. Without sound, every ball that leaves the tee counts. Inch measurements use the resting ball as a ruler, so treat them as within a few percent.

## How it works

1. **Court calibration:** the 4 corners you click give a homography, which maps image pixels to court feet. That's how bounces become landing spots and player positions become distances from the net.
2. **Players:** YOLO11 pose finds everyone in the frame. The tool keeps one person per side of the net who is standing on or near your court, so people on other courts are ignored.
3. **Ball:** blobs that are the ball's color *and* moving are linked across frames into a trajectory. Anything that doesn't move like a ball is thrown out.
4. **Hits vs. bounces:** a sharp change in the ball's direction near a player's wrist counts as a hit. Elsewhere it counts as a bounce. When several candidates cluster around one player, the one with the sharpest turn nearest the paddle hand wins. If the ball is lost, a fast wrist swing can still add a low-confidence "pose" hit.
5. **Sound:** the paddle hitting the ball makes a sharp "pop". The soundtrack is high-pass filtered (to drop voices, footsteps and hum), and pops are found by how suddenly they start and how quickly they fade. Each video hit is moved to the frame of its pop, and the speed of sound (about 1 ms per foot) is taken into account. A pop with no video hit becomes a hit if a player's paddle hand is at the ball or swinging. A video "hit" with no pop is probably a bounce, so its confidence is halved and the review tool shows **NO POP**. Recording with the phone's own mic is fine. Turn it off with `audio.enabled: false`.
6. **Mechanics** come from the skeleton at the contact frame. **Speed** is an estimate: distance ÷ flight time.

## Accuracy notes

- This uses one camera and 2D pose, so the numbers are best used for **trends and comparisons between sessions**, not as exact measurements. Knee angle in particular looks straighter from behind than it really is, so use **hip drop %** as your main "get low" metric.
- Expect some missed or extra hits on a new setup. The review pass fixes them and also trains the shot-type model.
- If the ball tracker keeps losing the ball, run `tune-ball` again. Most problems come from a ball color that is close to the court or wall color.
- If `tune-audio` misses quiet dinks, lower `audio.min_snr_db`. If it picks up shoe squeaks or the ball hitting the back wall, raise `min_rise_db` or `min_decay_db`. Once most hits show `+pop`, try `audio.drop_unmatched: true`.
- `tests/audio_test.py` checks the pop detector against voices, squeaks, clangs, thuds and echo, including a round trip through an AAC .mp4.
- `tests/synthetic_test.py` runs the whole pipeline on a generated rally as a smoke test.

## Files

```
pbshot.py            command-line entry point
config.yaml          all settings and thresholds
pb/                  court, video, ball, detect, events, mechanics, classify, review, report, render
sessions/            per-video tracks (.npz) + shots (.json, your labels live here)
models/              your trained shot-type model
```
