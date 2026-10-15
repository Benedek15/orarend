import copy
import itertools
import json
import random
from pathlib import Path

import pytest

from main import (
    DEFAULT_SETTINGS,
    DEFAULT_WEIGHTS,
    Course,
    InputError,
    Meeting,
    Option,
    Scorer,
    parse,
    solve,
    to_minutes,
)

ROOT = Path(__file__).resolve().parent.parent


# --- to_minutes -------------------------------------------------------------

@pytest.mark.parametrize("s, expected", [("00:00", 0), ("08:15", 495), ("24:00", 1440)])
def test_to_minutes(s, expected):
    assert to_minutes(s) == expected


@pytest.mark.parametrize("s", ["8", "8:5x", "25:00", "10:60", "24:01", "", None])
def test_to_minutes_rejects_garbage(s):
    with pytest.raises(InputError):
        to_minutes(s)


# --- Meeting.overlaps -------------------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ((0, 480, 600), (0, 540, 660), True),    # partial overlap
    ((0, 480, 720), (0, 540, 600), True),    # containment
    ((0, 480, 600), (0, 600, 720), False),   # touching ends are fine
    ((0, 480, 600), (1, 480, 600), False),   # different day
])
def test_overlaps(a, b, expected):
    ma, mb = Meeting(*a), Meeting(*b)
    assert ma.overlaps(mb) is expected
    assert mb.overlaps(ma) is expected  # symmetric


# --- Scorer -----------------------------------------------------------------

def opt(course, *meetings):
    return Option(course, course, tuple(Meeting(*m) for m in meetings))


def test_breakdown_counts_everything():
    courses = [Course("A", True, []), Course("B", True, []), Course("PE", False, [])]
    scorer = Scorer(DEFAULT_WEIGHTS, DEFAULT_SETTINGS, courses)
    chosen = [
        opt("A", (0, 8 * 60, 10 * 60)),                          # Mon early
        opt("B", (0, 13 * 60, 15 * 60), (2, 17 * 60, 19 * 60)),  # 3h gap Mon, Wed late
    ]
    assert scorer.breakdown(chosen) == {
        "days": 2, "early": 1, "gap_hours": 3.0, "late": 1, "optional_missing": 1,
    }
    w = DEFAULT_WEIGHTS
    assert scorer.score(chosen) == 2 * w["day"] + w["early"] + 3 * w["gap_hour"] + w["late"] + w["optional_missing"]


def test_breakdown_works_without_solve():
    # Regression: optional_names used to be set only inside solve().
    scorer = Scorer(DEFAULT_WEIGHTS, DEFAULT_SETTINGS, [Course("PE", False, [])])
    assert scorer.breakdown([])["optional_missing"] == 1


# --- parse / validation -----------------------------------------------------

def load_example():
    return json.loads((ROOT / "example.json").read_text(encoding="utf-8"))


def test_example_parses():
    courses, weights, settings = parse(load_example())
    assert len(courses) == 6
    assert weights == DEFAULT_WEIGHTS


def broken(mutate):
    data = load_example()
    mutate(data)
    return data


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["courses"][0]["options"][0]["meetings"][0].update(day="Tues"), "unknown day"),
    (lambda d: d["courses"][0]["options"][0]["meetings"][0].update(end="07:00"), "not before end"),
    (lambda d: d["courses"][0]["options"][0]["meetings"][0].update(start="8.00"), "invalid time"),
    (lambda d: d["courses"][0]["options"][0]["meetings"][0].pop("end"), "missing 'end'"),
    (lambda d: d["courses"][0].update(options=[]), "at least one option"),
    (lambda d: d["courses"][0]["options"][0].update(meetings=[]), "at least one meeting"),
    (lambda d: d["courses"][1].update(name=d["courses"][0]["name"]), "duplicate name"),
    (lambda d: d["weights"].update(gap=5), "unknown weight"),
    (lambda d: d["settings"].update(late_after="6pm"), "setting 'late_after'"),
    (lambda d: d.update(courses=[]), "non-empty list"),
])
def test_invalid_input(mutate, message):
    with pytest.raises(InputError, match=message):
        parse(broken(mutate))


def test_error_says_where():
    data = broken(lambda d: d["courses"][1]["options"][2]["meetings"][0].update(day="Wen"))
    with pytest.raises(InputError, match=r"Calculus practice.*GY3.*meeting #1"):
        parse(data)


# --- solve vs brute force ---------------------------------------------------

def brute_force(courses, scorer):
    choices = [c.options + ([] if c.mandatory else [None]) for c in courses]
    scores = []
    for combo in itertools.product(*choices):
        chosen = [o for o in combo if o is not None]
        meetings = [m for o in chosen for m in o.meetings]
        if any(a.overlaps(b) for a, b in itertools.combinations(meetings, 2)):
            continue
        scores.append(scorer.score(chosen))
    return sorted(scores)


def random_instance(rng):
    courses = []
    for ci in range(rng.randint(2, 5)):
        options = []
        for oi in range(rng.randint(1, 3)):
            meetings, used = [], set()
            for _ in range(rng.randint(1, 2)):
                day = rng.randrange(3)
                if day in used:
                    continue
                used.add(day)
                start = rng.randrange(8, 19) * 60
                meetings.append(Meeting(day, start, start + rng.choice([60, 90, 120])))
            options.append(Option(f"C{ci}", f"O{oi}", tuple(meetings)))
        courses.append(Course(f"C{ci}", rng.random() < 0.7, options))
    return courses


@pytest.mark.parametrize("seed", range(300))
@pytest.mark.parametrize("top", [1, 3])
def test_solve_matches_brute_force(seed, top):
    rng = random.Random(seed)
    courses = random_instance(rng)
    scorer = Scorer(DEFAULT_WEIGHTS, DEFAULT_SETTINGS, courses)
    expected = brute_force(courses, scorer)[:top]
    got = solve(copy.deepcopy(courses), scorer, top)
    assert [s for s, _ in got] == pytest.approx(expected)
    for s, chosen in got:  # every returned schedule is valid and scored right
        meetings = [m for o in chosen for m in o.meetings]
        assert not any(a.overlaps(b) for a, b in itertools.combinations(meetings, 2))
        assert {c.name for c in courses if c.mandatory} <= {o.course for o in chosen}
        assert scorer.score(chosen) == pytest.approx(s)
