#!/usr/bin/env python3
"""Week 0 splits CFBD's week 1, and both halves have to agree with Dart.

CFBD has no week 0. Its 2026 week 1 runs Aug 29 -> Sep 8 and holds 389
games across BOTH weekends — the openers and Labor Day together. The app
cuts that in two so a week is one weekend again.

**This is a rule that decides who wins**, in the same way build_slate is:
put a game in the wrong week and it scores against the wrong lineup, or
gets de-duped out of the slate entirely because the team also plays the
following Saturday. So the rule lives in two languages and this pins them
to the same answers as test/season_weeks_test.dart.

    /usr/bin/python3 test_week_zero.py
"""

import datetime as dt
import sys

from scoring import (build_slate, cfbd_week_for, games_in_app_week,
                     in_app_week, week_zero_ends_at)


def game(start):
    """A raw CFBD game with just the field the split reads."""
    return {"id": start, "startDate": start,
            "homeTeam": f"H{start}", "awayTeam": f"A{start}"}


# (season, expected split instant) — the same value the Dart test pins.
SPLITS = [(2026, "2026-09-01T06:00:00+00:00")]

# (kickoff, app week that owns it)
OWNERSHIP = [
    ("2026-08-27T23:00:00.000Z", 0),   # Thursday openers
    ("2026-08-29T16:00:00.000Z", 0),   # the opening Saturday
    ("2026-08-31T23:00:00.000Z", 0),   # Monday night, still week 0
    ("2026-09-01T05:59:00.000Z", 0),   # one minute inside
    ("2026-09-01T06:00:00.000Z", 1),   # the instant itself is week 1
    ("2026-09-02T23:00:00.000Z", 1),   # the Wednesday after
    ("2026-09-05T16:00:00.000Z", 1),   # Labor Day Saturday
    ("2026-09-07T23:00:00.000Z", 1),   # Labor Day itself
]


def main():
    failures = []

    for season, expected in SPLITS:
        got = week_zero_ends_at(season).isoformat()
        if got != expected:
            failures.append(f"split {season}: {got} != {expected}")

    for season in range(2026, 2036):
        split = week_zero_ends_at(season)
        if split.weekday() != 1:
            failures.append(f"{season} split is not a Tuesday")
        if split.hour != 6:
            failures.append(f"{season} split is not midnight Mountain")
        saturday = split - dt.timedelta(days=3)
        if saturday.month != 8 or not 25 <= saturday.day <= 31:
            failures.append(f"{season} split does not follow an August "
                            f"Saturday ({saturday.date()})")

    if cfbd_week_for(0) != 1 or cfbd_week_for(1) != 1:
        failures.append("weeks 0 and 1 must both ask CFBD for week 1")
    for w in range(2, 16):
        if cfbd_week_for(w) != w:
            failures.append(f"week {w} must keep CFBD's number")

    for start, owner in OWNERSHIP:
        in_zero = in_app_week(2026, 0, game(start))
        in_one = in_app_week(2026, 1, game(start))
        if in_zero == in_one:
            failures.append(f"{start} is in both weeks or neither")
        elif (0 if in_zero else 1) != owner:
            failures.append(f"{start} landed in week {0 if in_zero else 1}, "
                            f"expected {owner}")

    # A game we cannot place is KEPT, never dropped — hiding it would take
    # it off somebody's slate for a reason they could never see.
    undated = {"id": "x", "homeTeam": "H", "awayTeam": "A"}
    if not in_app_week(2026, 0, undated) or not in_app_week(2026, 1, undated):
        failures.append("a game with no kickoff must be kept, not dropped")

    # Weeks CFBD already split are taken whole.
    if not in_app_week(2026, 5, game("2026-10-03T16:00:00.000Z")):
        failures.append("week 5 must take everything CFBD gives it")

    # THE ONE THAT MATTERS MOST. build_slate de-dupes by team, so a team
    # playing on both weekends loses its second game if the two weeks are
    # ever handed over together. This is the bug the split exists to stop.
    both = [
        {"id": 1, "startDate": "2026-08-29T16:00:00.000Z",
         "homeTeam": "TCU", "awayTeam": "North Carolina"},
        {"id": 2, "startDate": "2026-09-05T16:00:00.000Z",
         "homeTeam": "TCU", "awayTeam": "SMU"},
    ]
    lines = [{"id": 1, "lines": [{"spread": -6.5}]},
             {"id": 2, "lines": [{"spread": -3.0}]}]
    if len(build_slate(both, lines)) != 1:
        failures.append("fixture wrong: unsplit, the de-dupe should drop one")
    split_total = sum(
        len(build_slate(games_in_app_week(2026, w, both), lines))
        for w in (0, 1))
    if split_total != 2:
        failures.append(f"split slates kept {split_total} of 2 TCU games — "
                        "a team playing both weekends must keep both")

    for f in failures:
        print(f"FAIL  {f}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1

    print(f"{len(OWNERSHIP)} kickoffs + {len(SPLITS)} splits OK — "
          f"week 0 owns the openers, week 1 owns Labor Day")
    return 0


if __name__ == "__main__":
    sys.exit(main())
