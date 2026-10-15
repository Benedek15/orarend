# orarend – uni timetable optimizer

Give it every course you need to take and all the sections (time slots) each course is offered in. It picks one section per course so that:

- nothing overlaps,
- every mandatory course is included,
- the schedule is as comfortable as possible: few days on campus, few early starts, few gaps, few late finishes.

It prints the best few timetables, each with its score.

## Requirements

Python 3.10 or newer. There are no external dependencies.

## Usage

```bash
python main.py example.json            # show the 3 best schedules
python main.py example.json --top 10   # show the 10 best
```

Example output:

```
#1  score 270  |  2 days, 1 early, 0.0h gaps, 0 late, 1 optional skipped
  Mon
    08:00-10:00  Calculus lecture (EA1)
    10:00-12:00  Calculus practice (GY1)
    12:00-14:00  Programming (G2)
    14:00-16:00  Physics (P1)
  Wed
    10:00-12:00  Discrete Math (D2)
    12:00-14:00  Programming (G2)
```

If the mandatory courses always clash, it prints `No valid schedule: the mandatory courses always clash.`

## Input format

The input is a JSON file. See [example.json](example.json) for a complete example.

```json
{
  "settings": { "early_before": "09:00", "late_after": "18:00" },
  "weights":  { "day": 100, "early": 30, "gap_hour": 10, "late": 15, "optional_missing": 40 },
  "courses": [
    {
      "name": "Programming",
      "mandatory": true,
      "options": [
        { "name": "G1", "meetings": [
          { "day": "Tue", "start": "10:00", "end": "12:00" },
          { "day": "Thu", "start": "10:00", "end": "12:00" } ] },
        { "name": "G2", "meetings": [
          { "day": "Mon", "start": "12:00", "end": "14:00" } ] }
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `courses[].name` | Course name |
| `courses[].mandatory` | `true` (default) means the course must be in every schedule. `false` means it can be skipped, at a cost. |
| `courses[].options` | The sections you can choose from. Exactly one is chosen, or none for a skipped optional course. |
| `options[].meetings` | Every time slot of that section. A section can meet several times a week. |
| `day` | `Mon`, `Tue`, `Wed`, `Thu`, `Fri` or `Sat` |
| `start`, `end` | `HH:MM`, 24-hour clock |
| `settings` | Optional. Sets what counts as "early" and "late". |
| `weights` | Optional. Sets how much each kind of inconvenience costs. Any key you leave out uses its default. |

## How the score works

Lower is better. A schedule's score is the sum of these penalties:

| Penalty | Default weight | Counted |
|---|---|---|
| `day` | 100 | per day with at least one class |
| `early` | 30 | per class starting before `early_before` (default 09:00) |
| `gap_hour` | 10 | per hour of idle time between classes on the same day |
| `late` | 15 | per class ending after `late_after` (default 18:00) |
| `optional_missing` | 40 | per optional course left out |

The defaults treat an extra day on campus as the worst thing. To change your priorities, change the weights. For example, set `early` to 200 if you never want 8 am classes.

## How the search works

The search lives in `solve()` in [main.py](main.py).

1. **Ordering.** Mandatory courses come first. Within each group, courses with fewer options come first, so the search hits clashes early and drops dead ends quickly.
2. **Backtracking.** For each course in turn, it tries each section that doesn't overlap anything already chosen. For an optional course, it also tries skipping it.
3. **Pruning (branch and bound).** It keeps the best `--top` schedules found so far in a heap. Before going deeper, it calculates a lower bound on the score of any schedule that could come out of the current partial one. If that bound is already worse than the worst schedule being kept, the whole branch is skipped.
   - The bound counts days, early classes and late classes, because adding more classes can only raise these. It also counts optional courses that have already been skipped.
   - It leaves out gaps, because a class added later can fill a gap and make it smaller.
4. **Result.** It prints the kept schedules sorted from best to worst, each with a breakdown of what made up its score.

## Code overview

| Part | Role |
|---|---|
| `Meeting`, `Option`, `Course` | Data classes: one time slot, one section, one course |
| `load()` | Reads the JSON file and fills in the default weights and settings |
| `Scorer` | Computes the penalty breakdown, the total score and the lower bound used for pruning |
| `solve()` | Runs the branch-and-bound search |
| `print_schedule()` | Prints a schedule grouped by day |
