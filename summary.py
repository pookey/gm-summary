#!/usr/bin/env python3
"""Summarise recent Gym Monster workouts: movements, reps, weights and session stats.

    ./summary.py                 # last 14 days
    ./summary.py --days 30       # a longer window
    ./summary.py --sets          # per-set breakdown
    ./summary.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta

from dotenv import load_dotenv

from speediance import Session, Speediance, SpeedianceError

BOLD, DIM, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"


def _fmt(value: float) -> str:
    """Trim pointless decimals: 42.0 -> '42', 23.5 -> '23.5'."""
    return f"{value:g}"


def _weight_range(exercise) -> str:
    tops = sorted({s.top_weight for s in exercise.sets if s.is_loaded})
    if not tops:
        return "bodyweight"
    if len(tops) == 1:
        return f"{_fmt(tops[0])}"
    return f"{_fmt(tops[0])}-{_fmt(tops[-1])}"


def _reps_summary(exercise) -> str:
    counts = [s.reps for s in exercise.sets]
    if not counts:
        return "-"
    if len(set(counts)) == 1:
        return f"{len(counts)}x{counts[0]}"
    return "+".join(str(c) for c in counts)


def render(sessions: list[Session], unit: str, show_sets: bool, use_colour: bool) -> str:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if use_colour else text

    if not sessions:
        return "No completed workouts in this window."

    out: list[str] = []
    for s in sessions:
        when = s.finished_at.strftime("%a %d %b %Y, %H:%M") if s.finished_at else s.day.strftime("%a %d %b %Y")
        out.append("")
        out.append(c(BOLD, f"{when}  ·  {s.title}"))
        stats = f"{s.duration_min} min · {s.calories} kcal · {_fmt(s.volume)} volume"
        loaded = s.loaded_exercises
        skipped = len(s.exercises) - len(loaded)
        if skipped:
            stats += f" · {len(loaded)} loaded movements (+{skipped} warm-up/stretch)"
        out.append(c(DIM, "  " + stats))

        if not s.exercises:
            out.append(c(YELLOW, "  (no per-exercise detail returned for this session)"))
            continue

        width = max((len(e.name) for e in loaded), default=0)
        for e in loaded:
            line = f"  {e.name.ljust(width)}  {_reps_summary(e):>9}  {_weight_range(e):>9} {unit}"
            extras = []
            if e.one_rep_max:
                extras.append(f"1RM ~{_fmt(e.one_rep_max)}{unit}")
            if e.prs:
                extras.append(c(GREEN, "PR: " + ", ".join(e.prs)))
            if extras:
                line += "   " + " · ".join(extras)
            out.append(line)

            if show_sets:
                for i, st in enumerate(e.sets, 1):
                    side = {1: " (left)", 2: " (right)"}.get(st.side, "")
                    detail = f"{st.reps} reps @ {_fmt(st.top_weight)}{unit}{side}"
                    if st.reps != st.target_reps:
                        detail += f"  [target {st.target_reps}]"
                    # A lighter opening rep is the machine ramping up to the working load.
                    if len(set(st.weights)) > 1:
                        detail += c(DIM, f"  ramp {_fmt(min(st.weights))}->{_fmt(st.top_weight)}")
                    out.append(c(DIM, f"      set {i}: ") + detail)

    out.append("")
    out.append(c(BOLD, "Totals"))
    days = {s.day for s in sessions}
    out.append(f"  {len(sessions)} sessions across {len(days)} days")
    out.append(
        f"  {sum(s.duration_min for s in sessions)} min · "
        f"{sum(s.calories for s in sessions)} kcal · "
        f"{_fmt(sum(s.volume for s in sessions))} total volume"
    )

    bests: dict[str, float] = defaultdict(float)
    for s in sessions:
        for e in s.loaded_exercises:
            bests[e.name] = max(bests[e.name], e.max_weight)
    if bests:
        out.append("")
        out.append(c(BOLD, f"Heaviest per movement ({unit})"))
        for name, w in sorted(bests.items(), key=lambda kv: -kv[1]):
            out.append(f"  {_fmt(w):>6}  {name}")

    prs = [
        (s.day, e.name, e.prs)
        for s in sessions
        for e in s.loaded_exercises
        if e.prs
    ]
    if prs:
        out.append("")
        out.append(c(BOLD, "Personal records"))
        for day, name, labels in prs:
            out.append(f"  {day:%d %b}  {name} " + c(GREEN, f"({', '.join(labels)})"))
    return "\n".join(out)


def agent_payload(s: Session, unit: str) -> dict:
    """JSON twin of render_for_agent.

    Built by hand rather than via asdict() so the derived per-hand fields are present:
    they're the whole point, and a consumer shouldn't have to rediscover them from
    capacity ratios.
    """
    return {
        "workout": s.title,
        "source": "Speediance Gym Monster",
        "finished_at": s.finished_at.isoformat(sep=" ") if s.finished_at else None,
        "date": str(s.day),
        "duration_min": s.duration_min,
        "calories": s.calories,
        "volume": s.volume,
        "unit": unit,
        "exercises": [
            {
                "name": e.name,
                "per_side": e.per_side,
                "load_points": e.load_points,
                "sets": [
                    {
                        "reps": st.reps,
                        "target_reps": st.target_reps,
                        "weight": st.top_weight,
                        "weight_total": st.total_weight,
                        "side": {1: "left", 2: "right"}.get(st.side, "both"),
                    }
                    for st in e.sets
                ],
            }
            for e in s.loaded_exercises
        ],
        "unweighted": [e.name for e in s.exercises if not e.is_loaded],
    }


def render_for_agent(s: Session, unit: str) -> str:
    """A dense, unambiguous digest of one session, for pasting into an AI agent.

    Deliberately plain: no colour, no PR/score noise, and every load spelled out so the
    agent never has to infer whether a weight is per-hand or total.
    """
    out: list[str] = []
    when = s.finished_at.strftime("%Y-%m-%d %H:%M") if s.finished_at else str(s.day)
    out.append(f"Workout: {s.title} (Speediance Gym Monster)")
    out.append(f"Finished: {when} ({s.day:%a %d %b %Y})")
    out.append(f"Duration: {s.duration_min} min | Calories: {s.calories} kcal | Volume: {_fmt(s.volume)} {unit}")

    loaded = s.loaded_exercises
    if not loaded:
        out.append("")
        out.append("No weighted exercises recorded for this session.")
        return "\n".join(out)

    if any(e.per_side for e in loaded):
        out.append(
            "Note: dual-handle moves report the load in EACH hand; the total moved is given "
            "in brackets. Log whichever your tracker expects, but don't double-count."
        )
    out.append("")
    out.append(f"Exercises ({len(loaded)}), weights in {unit}:")

    for i, e in enumerate(loaded, 1):
        parts = []
        for st in e.sets:
            side = {1: " (left)", 2: " (right)"}.get(st.side, "")
            if e.per_side:
                load = f"{_fmt(st.top_weight)} {unit}/hand ({_fmt(st.total_weight)} {unit} total)"
            else:
                load = f"{_fmt(st.top_weight)} {unit}"
            parts.append(f"{st.reps} reps @ {load}{side}")
        out.append(f"{i}. {e.name} — {len(e.sets)} set{'s' if len(e.sets) != 1 else ''}: " + "; ".join(parts))

    skipped = [e.name for e in s.exercises if not e.is_loaded]
    if skipped:
        out.append("")
        out.append("Unweighted (warm-up/mobility): " + ", ".join(skipped))
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise recent Gym Monster workouts.")
    parser.add_argument("--days", type=int, default=14, help="days back to include (default 14)")
    parser.add_argument("--sets", action="store_true", help="show a per-set breakdown")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--last",
        action="store_true",
        help="just the most recent workout, as a concise digest to feed an AI agent",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=90,
        help="with --last, how many days back to hunt for the most recent workout (default 90)",
    )
    parser.add_argument("--region", default=os.environ.get("GM_REGION", "EU"), choices=["EU", "Global"])
    args = parser.parse_args()

    load_dotenv()
    email, password = os.environ.get("GM_USER"), os.environ.get("GM_PASS")
    if not email or not password:
        print("Set GM_USER and GM_PASS in .env", file=sys.stderr)
        return 2

    client = Speediance(email, password, region=args.region)
    until = date.today()
    window = args.lookback if args.last else args.days
    since = until - timedelta(days=window - 1)
    try:
        client.login()
        # For --last, skip detail on the whole window: we only need one session's exercises.
        sessions = client.recent_sessions(since, until, with_detail=not args.last)
    except SpeedianceError as exc:
        print(f"Speediance API: {exc}", file=sys.stderr)
        return 1

    if args.last:
        if not sessions:
            print(f"No completed workouts in the last {window} days.", file=sys.stderr)
            return 1
        # recent_sessions sorts oldest-first, so the most recent is last.
        latest = sessions[-1]
        try:
            latest.exercises = client.session_detail(latest.training_id, latest.type)
        except SpeedianceError as exc:
            print(f"Speediance API: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(agent_payload(latest, client.unit), indent=2, default=str))
        else:
            print(render_for_agent(latest, client.unit))
        return 0

    if args.json:
        payload = {
            "account": email,
            "unit": client.unit,
            "from": str(since),
            "to": str(until),
            "sessions": [asdict(s) for s in sessions],
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render(sessions, client.unit, args.sets, use_colour=sys.stdout.isatty()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
