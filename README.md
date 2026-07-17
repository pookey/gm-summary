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
- `leftRight`: 0 bilateral, 1 left, 2 right. Unilateral moves emit sets in L/R pairs.
- PR flags (`maxWeightPr`, `totalCapacityPr`, `oneRepMaxPr`) are the API's own.
