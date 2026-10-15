"""Uni schedule optimizer.

Picks one option (section) for every course so that nothing overlaps,
every mandatory course is included, and the weighted "badness" score
(days on campus, 8 am starts, gaps, late finishes ...) is as low as possible.

Usage:
    python main.py courses.json            # show the 3 best schedules
    python main.py courses.json --top 10
"""

import argparse
import heapq
import itertools
import json
from dataclasses import dataclass

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

DEFAULT_WEIGHTS = {
    "day": 100,           # per day you have to go in
    "early": 30,          # per class starting before `early_before`
    "gap_hour": 10,       # per hour of idle time between classes on the same day
    "late": 15,           # per class ending after `late_after`
    "optional_missing": 40,  # per optional course left out
}
DEFAULT_SETTINGS = {"early_before": "09:00", "late_after": "18:00"}


class InputError(ValueError):
    """Raised when the input JSON is malformed; the message says where."""


def to_minutes(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        hours, minutes = int(h), int(m)
    except (AttributeError, ValueError):
        raise InputError(f"invalid time {hhmm!r}, expected HH:MM") from None
    if not (0 <= hours <= 24 and 0 <= minutes < 60) or hours * 60 + minutes > 24 * 60:
        raise InputError(f"invalid time {hhmm!r}, out of range")
    return hours * 60 + minutes


@dataclass(frozen=True)
class Meeting:
    day: int
    start: int  # minutes since midnight
    end: int

    def overlaps(self, other: "Meeting") -> bool:
        return self.day == other.day and self.start < other.end and other.start < self.end

    def __str__(self) -> str:
        return f"{DAYS[self.day]} {self.start // 60:02d}:{self.start % 60:02d}-{self.end // 60:02d}:{self.end % 60:02d}"


@dataclass(frozen=True)
class Option:
    course: str
    name: str
    meetings: tuple[Meeting, ...]


@dataclass
class Course:
    name: str
    mandatory: bool
    options: list[Option]


def parse_meeting(m: dict, where: str) -> Meeting:
    for key in ("day", "start", "end"):
        if key not in m:
            raise InputError(f"{where}: missing {key!r}")
    if m["day"] not in DAYS:
        raise InputError(f"{where}: unknown day {m['day']!r}, use one of {', '.join(DAYS)}")
    try:
        start, end = to_minutes(m["start"]), to_minutes(m["end"])
    except InputError as e:
        raise InputError(f"{where}: {e}") from None
    if start >= end:
        raise InputError(f"{where}: start {m['start']} is not before end {m['end']}")
    return Meeting(DAYS.index(m["day"]), start, end)


def parse(data: dict) -> tuple[list[Course], dict, dict]:
    """Build the model from already-decoded JSON, validating it on the way."""
    if not isinstance(data.get("courses"), list) or not data["courses"]:
        raise InputError("'courses' must be a non-empty list")
    courses = []
    seen: set[str] = set()
    for ci, c in enumerate(data["courses"]):
        name = c.get("name")
        if not name:
            raise InputError(f"course #{ci + 1}: missing 'name'")
        if name in seen:
            raise InputError(f"course {name!r}: duplicate name")
        seen.add(name)
        if not c.get("options"):
            raise InputError(f"course {name!r}: needs at least one option")
        options = []
        for oi, o in enumerate(c["options"]):
            oname = o.get("name") or f"#{oi + 1}"
            if not o.get("meetings"):
                raise InputError(f"course {name!r}, option {oname!r}: needs at least one meeting")
            meetings = tuple(
                parse_meeting(m, f"course {name!r}, option {oname!r}, meeting #{mi + 1}")
                for mi, m in enumerate(o["meetings"])
            )
            for a, b in itertools.combinations(meetings, 2):
                if a.overlaps(b):
                    raise InputError(f"course {name!r}, option {oname!r}: its own meetings overlap ({a} / {b})")
            options.append(Option(name, oname, meetings))
        courses.append(Course(name, c.get("mandatory", True), options))

    weights = {**DEFAULT_WEIGHTS, **data.get("weights", {})}
    unknown = set(weights) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise InputError(f"unknown weight(s): {', '.join(sorted(unknown))}")
    settings = {**DEFAULT_SETTINGS, **data.get("settings", {})}
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise InputError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    for key, value in settings.items():
        try:
            to_minutes(value)
        except InputError as e:
            raise InputError(f"setting {key!r}: {e}") from None
    return courses, weights, settings


def load(path: str) -> tuple[list[Course], dict, dict]:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise InputError(f"{path}: not valid JSON ({e})") from None
    return parse(data)


class Scorer:
    def __init__(self, weights: dict, settings: dict, courses: list[Course]):
        self.w = weights
        self.early_before = to_minutes(settings["early_before"])
        self.late_after = to_minutes(settings["late_after"])
        self.optional_names = {c.name for c in courses if not c.mandatory}
        self.n_optional = len(self.optional_names)

    def breakdown(self, chosen: list[Option]) -> dict:
        meetings = [m for o in chosen for m in o.meetings]
        by_day: dict[int, list[Meeting]] = {}
        for m in meetings:
            by_day.setdefault(m.day, []).append(m)
        gap_minutes = 0
        for day_meetings in by_day.values():
            day_meetings.sort(key=lambda m: m.start)
            for a, b in itertools.pairwise(day_meetings):
                gap_minutes += max(0, b.start - a.end)
        n_optional_taken = sum(1 for o in chosen if o.course in self.optional_names)
        return {
            "days": len(by_day),
            "early": sum(1 for m in meetings if m.start < self.early_before),
            "gap_hours": gap_minutes / 60,
            "late": sum(1 for m in meetings if m.end > self.late_after),
            "optional_missing": self.n_optional - n_optional_taken,
        }

    def score(self, chosen: list[Option]) -> float:
        b = self.breakdown(chosen)
        return (
            b["days"] * self.w["day"]
            + b["early"] * self.w["early"]
            + b["gap_hours"] * self.w["gap_hour"]
            + b["late"] * self.w["late"]
            + b["optional_missing"] * self.w["optional_missing"]
        )

    def lower_bound(self, chosen: list[Option], optional_left: int) -> float:
        """Cost that can only grow as more options are added (used for pruning).

        Gaps are excluded because filling a gap can shrink it, and courses
        not yet decided might still be taken, so they are not counted missing.
        """
        b = self.breakdown(chosen)
        return (
            b["days"] * self.w["day"]
            + b["early"] * self.w["early"]
            + b["late"] * self.w["late"]
            + (b["optional_missing"] - optional_left) * self.w["optional_missing"]
        )


def solve(courses: list[Course], scorer: Scorer, top: int) -> list[tuple[float, list[Option]]]:
    # Most constrained first: fewer options -> earlier failures -> faster search.
    order = sorted(courses, key=lambda c: (not c.mandatory, len(c.options)))
    optional_after = [sum(1 for c in order[i:] if not c.mandatory) for i in range(len(order) + 1)]

    best: list[tuple[float, int, list[Option]]] = []  # max-heap via negated score
    counter = itertools.count()

    def worst_kept() -> float:
        return -best[0][0] if len(best) >= top else float("inf")

    def search(i: int, chosen: list[Option], taken: list[Meeting]) -> None:
        if scorer.lower_bound(chosen, optional_after[i]) > worst_kept():
            return
        if i == len(order):
            s = scorer.score(chosen)
            if s < worst_kept():
                heapq.heappush(best, (-s, next(counter), list(chosen)))
                if len(best) > top:
                    heapq.heappop(best)
            return
        course = order[i]
        for opt in course.options:
            if any(m.overlaps(t) for m in opt.meetings for t in taken):
                continue
            chosen.append(opt)
            search(i + 1, chosen, taken + list(opt.meetings))
            chosen.pop()
        if not course.mandatory:
            search(i + 1, chosen, taken)

    search(0, [], [])
    return sorted(((-s, c) for s, _, c in best), key=lambda x: x[0])


def print_schedule(rank: int, score: float, chosen: list[Option], scorer: Scorer) -> None:
    b = scorer.breakdown(chosen)
    print(f"#{rank}  score {score:.0f}  |  {b['days']} days, {b['early']} early, "
          f"{b['gap_hours']:.1f}h gaps, {b['late']} late, {b['optional_missing']} optional skipped")
    rows = sorted(((m, o) for o in chosen for m in o.meetings), key=lambda x: (x[0].day, x[0].start))
    for day, group in itertools.groupby(rows, key=lambda x: x[0].day):
        print(f"  {DAYS[day]}")
        for m, o in group:
            print(f"    {str(m)[4:]}  {o.course} ({o.name})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Find the best uni timetable.")
    parser.add_argument("file", help="JSON file with courses (see example.json)")
    parser.add_argument("--top", type=int, default=3, help="how many schedules to show")
    args = parser.parse_args()

    try:
        courses, weights, settings = load(args.file)
    except (OSError, InputError) as e:
        parser.exit(1, f"error: {e}\n")
    scorer = Scorer(weights, settings, courses)
    results = solve(courses, scorer, args.top)
    if not results:
        print("No valid schedule: the mandatory courses always clash.")
        return
    for rank, (score, chosen) in enumerate(results, 1):
        print_schedule(rank, score, chosen, scorer)


if __name__ == "__main__":
    main()
