# Gym Monster workout summary

Summarises recent Speediance Gym Monster workouts — movements, sets, reps, weights,
session stats and personal records — straight from the (unofficial) Speediance API.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Credentials come from `.env`:

```
GM_USER="you@example.com"
GM_PASS="..."
GM_REGION="EU"      # optional; EU or Global, defaults to EU
```

## Use

```sh
.venv/bin/python summary.py              # last 14 days
.venv/bin/python summary.py --days 30    # longer window
.venv/bin/python summary.py --sets       # per-set breakdown, incl. left/right and warm-up ramps
.venv/bin/python summary.py --json       # machine-readable, for feeding elsewhere
```

### Feeding your last workout to an AI agent

`--last` finds the most recent completed workout and prints a dense, decoration-free digest
meant to be pasted into an agent that will log it:

```
$ .venv/bin/python summary.py --last
Workout: Goal-Focused Workout (Speediance Gym Monster)
Finished: 2026-07-16 13:17 (Thu 16 Jul 2026)
Duration: 50 min | Calories: 438 kcal | Volume: 11811 kg
Note: dual-handle moves report the load in EACH hand; the total moved is given in brackets.

Exercises (12), weights in kg:
1. Dual-Handle Narrow Stance Squat — 2 sets: 9 reps @ 42 kg/hand (84 kg total); 9 reps @ 50 kg/hand (100 kg total)
2. Barbell Box Squat — 1 set: 9 reps @ 88 kg
...
```

Add `--json` for the same thing as structured data, `--lookback N` to hunt further back than
90 days. Exits non-zero if no workout is found, so it's safe to script.

## Layout

- `speediance.py` — read-only API client: login, calendar, session detail. Returns typed
  `Session` / `Exercise` / `WorkSet` objects.
- `summary.py` — CLI and rendering.

## API notes

Endpoint shapes came from [UnofficialSpeedianceWorkoutManager](https://github.com/hbui3/UnofficialSpeedianceWorkoutManager),
but the client here is standalone because reading history needs two fixes that repo doesn't have:

1. **`App_type: SOFTWARE` is required on every authenticated request.** Without it the API
   returns HTTP 200 with `{"code":1002,"message":"Invalid appid"}`. That repo's `_get_headers()`
   only sends it on login/logout, and its error handling turns the failure into an empty list —
   so history reads silently return nothing.
2. **AI "Goal-Focused" sessions (`type: 4`) need `aiCourseTrainingInfoDetail`.** The
   `courseTrainingInfoDetail` / `cttTrainingInfoDetail` endpoints return `403` for them.

Reading the data:

- `finishedReps` is the **sets** array, not reps. Each entry is one set.
- Per-rep weights live at `set.trainingInfoDetail.weights`. The first rep is often a lighter
  ramp-up, so a set's working load is `max(weights)`.
- Weights are already in the account's display unit (kg here) — no conversion. The `× 2.2`
  factor in the upstream repo applies to the **write** path only.
- **Weights are per load point, not the total.** A dual-handle move reporting 50 kg means
  50 kg in *each* hand, i.e. 100 kg moved. `capacity` is the total work for the set, so
  `round(capacity / sum(weights))` recovers the number of load points. Measured across 445
  real sets that ratio is only ever exactly 1 or 2, is stable within an exercise, and is 2
  for every dual-handle move and 1 for barbell/belt/single-handle/alternating ones — so it's
  derived from the data here rather than guessed from exercise names.
- `leftRight`: 0 bilateral, 1 left, 2 right. Unilateral moves emit sets in L/R pairs.
- PR flags (`maxWeightPr`, `totalCapacityPr`, `oneRepMaxPr`) are the API's own.
