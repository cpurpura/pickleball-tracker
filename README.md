# Pickleball Drill Tracker

A phone-first web app for running pickleball drill sessions on the court and saving the results to your PC.

**Live app:** https://cpurpura.github.io/pickleball-tracker/

- **Import drill plans** (JSON from Claude, or CSV) from your phone.
- **Build your own plans:** choose from every drill you've imported (library and plans), reorder them, and set minutes per drill. **Edit** opens any existing plan in the same builder.
- **Drill library:** keep individual drills outside of any plan. Add them by hand, import them from a file, or save them from a plan. Run one drill, or tick several to run as a custom session.
- **Run a session:** drill-by-drill view with a countdown timer (beeps/vibrates at zero), big ✓ Made / ✗ Miss counters, a 1–5 rating and notes for each drill. The screen stays awake during a session.
- **Partner logging:** add your drill partner to a session and switch between **You** and **[Partner]** to log each person's made/miss, rating and notes. History shows progress for you or for any partner.
- **Program weeks & role switching:** each session records its program week (suggested automatically, adjustable), and an optional chime every N minutes signals partners to switch roles. When you're logging a partner, it also flips who your taps count for.
- **Reference videos:** every drill has a YouTube search link. You can also save your own video links (in the plan or during a session) or include them in imported plans.
- **Works offline.** Everything is stored on the phone (IndexedDB). An unfinished session survives closing the app.
- **History:** past sessions plus per-drill success % trends.
- **Export:** a CSV that opens in Excel (one row per drill) and a full JSON backup that can be re-imported.

No frameworks, no build step: just `index.html`, `app.js`, `styles.css`, plus `sw.js` and `manifest.webmanifest` so it installs and works offline.

## Run it on your PC

```bash
python -m http.server 8090
```

Then open http://localhost:8090. To get a phone-sized view in Chrome, press F12 and turn on device mode.

## Put it on your Android phone

Offline mode and installing need **HTTPS**, so host the app online. The easiest free option is **GitHub Pages**:

1. Create a GitHub repo (e.g. `pickleball-tracker`) and push this folder to it.
2. In the repo, go to **Settings → Pages**, set the source to the `main` branch and the `/ (root)` folder, then save.
3. On your phone, open `https://<your-username>.github.io/pickleball-tracker/` in Chrome.
4. Open the Chrome menu (⋮) and choose **Add to Home screen** (or **Install app**).

Your data stays on the phone. It is **not** uploaded to GitHub.

**Updating the app:** edit the files, bump `CACHE` in `sw.js` (e.g. `v1` → `v2`), and push. The phone picks up the new version the next time you open the app (sometimes it takes two launches).

## Drill plan format

See [PLAN_FORMAT.md](PLAN_FORMAT.md) for the JSON and CSV formats and a ready-made prompt for Claude. The same prompt is also in the app under **Data → Get plans from Claude**.

## Getting results onto your PC

In the app, open **Data** and choose an export:

- **Share…** opens the Android share sheet. Send the file to Gmail, Google Drive, or Quick Share to your PC.
- **Download** saves the file to the phone's `Downloads` folder. Copy it over USB.

Export a **Full backup (JSON)** now and then. If the phone is lost or Chrome's data is cleared, that backup is your copy.
