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


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


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


def load(path: str) -> tuple[list[Course], dict, dict]:
    with open(path) as f:
        data = json.load(f)
    courses = []
    for c in data["courses"]:
        options = []
        for o in c["options"]:
            meetings = tuple(
                Meeting(DAYS.index(m["day"]), to_minutes(m["start"]), to_minutes(m["end"]))
                for m in o["meetings"]
            )
            options.append(Option(c["name"], o["name"], meetings))
        courses.append(Course(c["name"], c.get("mandatory", True), options))
    weights = {**DEFAULT_WEIGHTS, **data.get("weights", {})}
    settings = {**DEFAULT_SETTINGS, **data.get("settings", {})}
    return courses, weights, settings


class Scorer:
    def __init__(self, weights: dict, settings: dict, n_optional: int):
        self.w = weights
        self.early_before = to_minutes(settings["early_before"])
        self.late_after = to_minutes(settings["late_after"])
        self.n_optional = n_optional

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
    scorer.optional_names = {c.name for c in courses if not c.mandatory}
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

    courses, weights, settings = load(args.file)
    scorer = Scorer(weights, settings, sum(1 for c in courses if not c.mandatory))
    results = solve(courses, scorer, args.top)
    if not results:
        print("No valid schedule: the mandatory courses always clash.")
        return
    for rank, (score, chosen) in enumerate(results, 1):
        print_schedule(rank, score, chosen, scorer)


if __name__ == "__main__":
    main()
