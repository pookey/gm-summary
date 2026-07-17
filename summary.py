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


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise recent Gym Monster workouts.")
    parser.add_argument("--days", type=int, default=14, help="days back to include (default 14)")
    parser.add_argument("--sets", action="store_true", help="show a per-set breakdown")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--region", default=os.environ.get("GM_REGION", "EU"), choices=["EU", "Global"])
    args = parser.parse_args()

    load_dotenv()
    email, password = os.environ.get("GM_USER"), os.environ.get("GM_PASS")
    if not email or not password:
        print("Set GM_USER and GM_PASS in .env", file=sys.stderr)
        return 2

    client = Speediance(email, password, region=args.region)
    try:
        client.login()
        until = date.today()
        sessions = client.recent_sessions(until - timedelta(days=args.days - 1), until)
    except SpeedianceError as exc:
        print(f"Speediance API: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = {
            "account": email,
            "unit": client.unit,
            "from": str(until - timedelta(days=args.days - 1)),
            "to": str(until),
            "sessions": [asdict(s) for s in sessions],
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render(sessions, client.unit, args.sets, use_colour=sys.stdout.isatty()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
