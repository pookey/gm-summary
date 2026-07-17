"""Minimal read-only client for the (unofficial) Speediance Gym Monster API.

Endpoint shapes were derived from hbui3/UnofficialSpeedianceWorkoutManager, with two
corrections needed to read workout history:

  * every authenticated request needs the ``App_type: SOFTWARE`` header, or the API
    answers HTTP 200 with ``{"code": 1002, "message": "Invalid appid"}``
  * AI-generated "Goal-Focused" sessions (type 4) only resolve via the aiCourse
    detail endpoint; the course/ctt endpoints return 403 for them
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

HOSTS = {"EU": "euapi.speediance.com", "Global": "api2.speediance.com"}

# `type` as it appears in a calendar entry -> detail endpoints to try, best first.
# Unknown types fall back to trying every endpoint.
_DETAIL_ROUTES = {
    1: ("cttTrainingInfoDetail",),
    2: ("courseTrainingInfoDetail",),
    4: ("aiCourseTrainingInfoDetail",),
    5: ("cttTrainingInfoDetail",),
}
_ALL_ROUTES = ("aiCourseTrainingInfoDetail", "courseTrainingInfoDetail", "cttTrainingInfoDetail")


class SpeedianceError(RuntimeError):
    pass


@dataclass
class WorkSet:
    """One set. Note the API calls these `finishedReps`, but each entry is a set."""

    reps: int
    target_reps: int
    weights: list[float]  # one entry per rep, already in the account's display unit
    seconds: int
    side: int  # 0 bilateral, 1 left, 2 right
    capacity: float = 0.0  # the API's own work total for this set

    @property
    def top_weight(self) -> float:
        """Working load. The first rep is often a lighter ramp-up, so max beats [0]."""
        return max(self.weights) if self.weights else 0.0

    @property
    def is_loaded(self) -> bool:
        return bool(self.weights) and self.top_weight > 0

    @property
    def load_points(self) -> int:
        """How many points the load is applied at: 2 for dual-handle, 1 for barbell etc.

        `weights` is the load at ONE point, so a dual-handle move reporting 50kg is 50kg in
        each hand. The API doesn't say so directly, but `capacity` is the total work, so the
        ratio recovers it. Measured across 445 sets of real history this is exactly 1 or 2,
        never anything else, and never varies within an exercise.
        """
        total = sum(self.weights)
        if total <= 0 or self.capacity <= 0:
            return 1
        return max(1, round(self.capacity / total))

    @property
    def total_weight(self) -> float:
        """Load across all points — what the body actually moved."""
        return self.top_weight * self.load_points


@dataclass
class Exercise:
    name: str
    sets: list[WorkSet] = field(default_factory=list)
    max_weight: float = 0.0
    volume: float = 0.0  # the API's own `totalCapacity`
    one_rep_max: float | None = None
    score: int | None = None
    is_barbell: bool = False
    is_unilateral: bool = False
    pr_weight: bool = False
    pr_volume: bool = False
    pr_one_rep_max: bool = False

    @property
    def is_loaded(self) -> bool:
        return self.max_weight > 0

    @property
    def total_reps(self) -> int:
        return sum(s.reps for s in self.sets)

    @property
    def load_points(self) -> int:
        loaded = [s for s in self.sets if s.is_loaded]
        return loaded[0].load_points if loaded else 1

    @property
    def per_side(self) -> bool:
        """True when the reported weight is the load in each hand, not the total."""
        return self.load_points > 1

    @property
    def prs(self) -> list[str]:
        return [
            label
            for label, hit in (
                ("weight", self.pr_weight),
                ("volume", self.pr_volume),
                ("1RM", self.pr_one_rep_max),
            )
            if hit
        ]


@dataclass
class Session:
    training_id: int
    day: date
    title: str
    finished_at: datetime | None
    duration_min: int
    calories: int
    volume: float
    type: int
    exercises: list[Exercise] = field(default_factory=list)

    @property
    def loaded_exercises(self) -> list[Exercise]:
        """Exercises with weight on the cables — excludes warm-ups and stretches."""
        return [e for e in self.exercises if e.is_loaded]


class Speediance:
    def __init__(self, email: str, password: str, region: str = "EU", device_type: int = 1):
        if region not in HOSTS:
            raise ValueError(f"region must be one of {sorted(HOSTS)}")
        self.email = email
        self.password = password
        self.host = HOSTS[region]
        self.base_url = f"https://{self.host}"
        self.device_type = device_type
        self.user_id: str | None = None
        self.token: str | None = None
        self.unit = "kg"
        self._session = requests.Session()

    # -- plumbing ----------------------------------------------------------

    def _headers(self) -> dict:
        headers = {
            "Host": self.host,
            "Content-Type": "application/json",
            "User-Agent": "Dart/3.9 (dart:io)",
            "Timestamp": str(int(time.time() * 1000)),
            "Versioncode": "40304",
            # Without this the API rejects every authenticated call as "Invalid appid".
            "App_type": "SOFTWARE",
        }
        if self.token:
            headers["Token"] = self.token
            headers["App_user_id"] = self.user_id or ""
        return headers

    def _get(self, path: str, *, allow_error: bool = False) -> object:
        resp = self._session.get(self.base_url + path, headers=self._headers(), timeout=30)
        return self._unwrap(resp, allow_error=allow_error)

    def _post(self, path: str, payload: dict) -> object:
        resp = self._session.post(
            self.base_url + path, headers=self._headers(), json=payload, timeout=30
        )
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: requests.Response, *, allow_error: bool = False) -> object:
        try:
            body = resp.json()
        except ValueError:
            raise SpeedianceError(f"{resp.status_code}: non-JSON response {resp.text[:200]!r}")
        # The API signals failure in the body, not the HTTP status.
        code = body.get("code")
        if code not in (0, None):
            if allow_error:
                return None
            raise SpeedianceError(f"API error {code}: {body.get('message')}")
        return body.get("data")

    # -- auth --------------------------------------------------------------

    def login(self) -> None:
        check = self._post("/api/app/v2/login/verifyIdentity", {"type": 2, "userIdentity": self.email})
        if check.get("isExist") is False:
            raise SpeedianceError(f"No Speediance account for {self.email} on {self.host}")
        if check.get("hasPwd") is False:
            raise SpeedianceError("Account has no password set; set one in the Speediance app")

        data = self._post(
            "/api/app/v2/login/byPass",
            {"userIdentity": self.email, "password": self.password, "type": 2},
        )
        self.token = data.get("token")
        self.user_id = str(data.get("appUserId") or "")
        if not self.token or not self.user_id:
            raise SpeedianceError("Login succeeded but returned no token")
        self.unit = "lb" if data.get("unit") == 1 else "kg"

    # -- reads -------------------------------------------------------------

    def calendar_month(self, month: str) -> list[dict]:
        """`month` is 'YYYY-MM'. Returns one entry per day of that month."""
        return (
            self._get(
                f"/api/app/v5/trainingCalendar/monthNew"
                f"?date={month}&selectedDeviceType={self.device_type}"
            )
            or []
        )

    def _detail_raw(self, training_id: int, type_: int) -> list[dict]:
        routes = _DETAIL_ROUTES.get(type_, _ALL_ROUTES)
        for route in routes:
            data = self._get(f"/api/app/trainingInfo/{route}/{training_id}", allow_error=True)
            if data:
                return data
        return []

    def session_detail(self, training_id: int, type_: int) -> list[Exercise]:
        return [_parse_exercise(raw) for raw in self._detail_raw(training_id, type_)]

    def recent_sessions(self, since: date, until: date, *, with_detail: bool = True) -> list[Session]:
        """Completed sessions in [since, until], newest last."""
        sessions: list[Session] = []
        for month in _months_between(since, until):
            for day in self.calendar_month(month):
                try:
                    day_date = date.fromisoformat(day["date"])
                except (KeyError, ValueError):
                    continue
                if not since <= day_date <= until:
                    continue
                for plan in day.get("trainingPlanList") or []:
                    # Unfinished plans are scheduled-but-not-done, and carry no trainingId.
                    if plan.get("isFinish") != 1 or not plan.get("trainingId"):
                        continue
                    sessions.append(_parse_session(plan, day_date))
        sessions.sort(key=lambda s: (s.finished_at or datetime.min, s.training_id))
        if with_detail:
            for s in sessions:
                s.exercises = self.session_detail(s.training_id, s.type)
        return sessions


def _parse_session(plan: dict, day: date) -> Session:
    finished_at = None
    if plan.get("finishTime"):
        try:
            finished_at = datetime.strptime(plan["finishTime"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return Session(
        training_id=plan["trainingId"],
        day=day,
        title=plan.get("title") or "Workout",
        finished_at=finished_at,
        duration_min=plan.get("durationMinute") or 0,
        calories=plan.get("calorie") or 0,
        volume=plan.get("totalCapacity") or 0.0,
        type=plan.get("type") or 0,
    )


def _parse_exercise(raw: dict) -> Exercise:
    sets = []
    for entry in raw.get("finishedReps") or []:
        detail = entry.get("trainingInfoDetail") or {}
        sets.append(
            WorkSet(
                reps=entry.get("finishedCount") or 0,
                target_reps=entry.get("targetCount") or 0,
                weights=[float(w) for w in (detail.get("weights") or [])],
                seconds=entry.get("time") or 0,
                side=entry.get("leftRight") or 0,
                capacity=float(entry.get("capacity") or 0.0),
            )
        )
    return Exercise(
        name=raw.get("actionLibraryName") or "(unnamed)",
        sets=sets,
        max_weight=float(raw.get("maxWeight") or 0.0),
        volume=float(raw.get("totalCapacity") or 0.0),
        one_rep_max=raw.get("oneRepMax"),
        score=raw.get("score"),
        is_barbell=bool(raw.get("isBarbell")),
        is_unilateral=bool(raw.get("isLeftRight")),
        pr_weight=bool(raw.get("maxWeightPr")),
        pr_volume=bool(raw.get("totalCapacityPr")),
        pr_one_rep_max=bool(raw.get("oneRepMaxPr")),
    )


def _months_between(start: date, end: date) -> list[str]:
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months
