# Drill plan format

## Prompt for Claude

Copy this prompt into Claude and fill in the brackets. Save the reply as a `.json` file, put it on your phone (email, Google Drive, etc.), and import it in the app.

```
Create a pickleball drill plan for me.
About me: [skill level, e.g. 3.5] · Focus: [e.g. third-shot drops and resets] · Length: [e.g. 60 min] · Setup: [solo / partner / ball machine]

Return ONLY valid JSON (no extra text) in exactly this format so I can import it into my drill tracker:
{
  "name": "Plan name",
  "description": "1–2 sentences on the session focus",
  "drills": [
    {
      "name": "Drill name",
      "category": "Warm-up | Dinking | Drops | Drives | Volleys | Resets | Serves | Returns | Lobs | Overheads | Footwork",
      "durationMin": 10,
      "reps": 50,
      "target": "Measurable success goal, e.g. 8 of 10 land in the kitchen",
      "instructions": "Setup, how to run it, and key coaching cues",
      "videos": [{ "title": "Video title", "url": "https://..." }]
    }
  ]
}
Only include "videos" with URLs you are certain exist; otherwise use an empty list (the app adds a YouTube search link for every drill).
For several plans at once, wrap them as {"plans": [ ... ]}.
```

## JSON

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Plan name. Re-importing a plan with the same name replaces the old one. |
| `description` | no | |
| `drills[].name` | yes | |
| `drills[].category` | no | Shown as a label. |
| `drills[].durationMin` | no | Sets the countdown. Leave it out (or use 0) for a stopwatch. |
| `drills[].reps` | no | Rep goal shown next to the counter. |
| `drills[].target` | no | Success criterion. |
| `drills[].instructions` | no | Text or a list of steps. |
| `drills[].videos` | no | Reference videos: a list of `{"title", "url"}` objects, or plain URLs. Only `http(s)` links are kept. Every drill also gets a *Search YouTube* link automatically, and you can add your own links in the app. |

A single file can hold one plan, `{"plans": [...]}`, or a list of plans `[...]`. The importer also accepts common alternative field names (`title`, `minutes`, `goal`, `description`, …), so small variations in Claude's output still import.

## Multi-session programs

Multi-week programs with several session types also import. Each entry in `sessions` becomes its own plan:

```json
{
  "plan": { "id": "third-shot", "name": "Third Shot Drops & Drives", "duration_weeks": 4, "sessions_per_week": 2, "notes": "…" },
  "scoring": { "attempts_per_set": 10 },
  "sessions": [
    {
      "id": "session_a",
      "label": "Session A — Drops",
      "blocks": [
        {
          "order": 2, "name": "Stationary drops", "duration_minutes": 25, "goal": 7,
          "description": "…", "coaching_point": "…", "tags": ["drop", "feed"]
        }
      ]
    }
  ],
  "progression": [{ "weeks": [1, 2], "description": "…" }],
  "regression_rule": { "threshold": 5, "action": "…" },
  "fault_checklist": { "drop": ["…"], "drive": ["…"] }
}
```

- Blocks become drills, sorted by `order`.
- `goal` combined with `attempts_per_set` becomes the target, e.g. "7/10 makes". Use `goal_unit` (e.g. `correct_calls_out_of_10`) to change the wording. Blocks with `"goal": null` show "Unscored".
- `coaching_point` and any `fault_checklist` entries whose key matches one of the block's `tags` are added to the drill instructions.
- `progression`, `regression_rule` and the schedule from `plan` appear in the plan description.

## CSV

Use one row per drill. Add a `Plan` column to put several plans in one file. Without it, the file name becomes the plan name.

```csv
Plan,Drill,Category,Duration (min),Reps,Target,Instructions,Videos
Soft game,Cross-court dinks,Dinking,10,50,80% in the kitchen,Paddle up; lift with the legs,https://www.youtube.com/watch?v=...
Soft game,Third-shot drops,Drops,12,30,7 of 10 in the kitchen,Partner feeds deep,
```

To list several videos in the `Videos` column, separate the URLs with `|`.
