#!/usr/bin/env python3
"""A shift that is already running must not throw itself away.

Friday Sep 18 2026: two shifts started before the night's kickoffs and
BOTH ended within a minute, because the next game was more than an hour
off. The one request that would have covered the games never arrived.
Every phone in the league showed 0-0 for 78 minutes of live football.

    /usr/bin/python3 test_wait_for_kickoff.py
"""

import datetime as dt
import sys

from live_refresh import MAX_SHIFT, WARMUP, next_delay, wait_for_kickoff

UTC = dt.timezone.utc


def game(start, *, points=None, completed=False):
    return {"startDate": start.isoformat().replace("+00:00", "Z"),
            "homePoints": points, "awayPoints": points,
            "completed": completed}


# Friday night, as it happened.
THURSDAY_FINAL = game(dt.datetime(2026, 9, 17, 23, 30, tzinfo=UTC),
                      points=27, completed=True)
MIAMI_WAKE = game(dt.datetime(2026, 9, 18, 23, 30, tzinfo=UTC))
HOUSTON_TTU = game(dt.datetime(2026, 9, 19, 0, 0, tzinfo=UTC))
FRIDAY = [THURSDAY_FINAL, MIAMI_WAKE, HOUSTON_TTU]

failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"{'ok ' if ok else 'FAIL'}  {label}: got {got}, want {want}")


# THE REGRESSION. The 20:00Z shift: nothing live, kickoff 3.5h away.
start = dt.datetime(2026, 9, 18, 20, 0, 47, tzinfo=UTC)
check("20:00 shift — next_delay still says nothing is close",
      next_delay(FRIDAY, start), None)
wait = wait_for_kickoff(FRIDAY, start, start + MAX_SHIFT)
want = int((MIAMI_WAKE_START := dt.datetime(2026, 9, 18, 23, 30, tzinfo=UTC))
           .__sub__(WARMUP).__sub__(start).total_seconds())
check("20:00 shift WAITS until an hour before the 23:30 kickoff",
      wait, want)

# The 21:21Z shift: the same, two hours out.
start = dt.datetime(2026, 9, 18, 21, 22, 16, tzinfo=UTC)
wait = wait_for_kickoff(FRIDAY, start, start + MAX_SHIFT)
check("21:21 shift waits too (it would have covered the whole night)",
      wait is not None and wait > 0, True)

# A kickoff after this shift has to end is the NEXT run's job.
start = dt.datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
late = [game(dt.datetime(2026, 9, 18, 23, 30, tzinfo=UTC))]
check("kickoff after the shift ends — stand down, don't wait",
      wait_for_kickoff(late, start, start + MAX_SHIFT), None)

# Kickoff only minutes before the shift must end covers nothing.
deadline = dt.datetime(2026, 9, 18, 23, 40, tzinfo=UTC)
check("kickoff 10 minutes before the shift ends — not worth waiting",
      wait_for_kickoff(FRIDAY, deadline - dt.timedelta(hours=3), deadline),
      None)

check("the week is over — nothing to wait for",
      wait_for_kickoff([THURSDAY_FINAL], start, start + MAX_SHIFT), None)

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all checks OK — a running shift waits for kickoff")
