#!/usr/bin/env python3
"""The shift must not end while football is being played.

WHY THIS EXISTS. At 16:00Z on Aug 29 2026 — the first kickoff of the
season — the live shift ended. North Carolina had kicked off at TCU, but
CFBD had not published a score yet, so `status_of` still called the game
"scheduled". It was not "live" (no points) and not "upcoming" (its start
had passed), so it counted for nothing, and the delay was decided by the
NEXT kickoff three hours away: "nothing for over an hour — let the shift
end".

The watch stopped at the exact moment the season started. Nothing errored.
The shift exited 0 and the scores sat eight minutes stale.

    /usr/bin/python3 test_next_delay.py
"""

import datetime as dt
import sys

from live_refresh import IDLE_INTERVAL, LIVE_INTERVAL, next_delay

NOW = dt.datetime(2026, 8, 29, 16, 6, tzinfo=dt.timezone.utc)


def game(hours_from_now, *, points=None, completed=False):
    start = NOW + dt.timedelta(hours=hours_from_now)
    return {"startDate": start.isoformat().replace("+00:00", "Z"),
            "homePoints": points, "awayPoints": points,
            "completed": completed}


CASES = [
    ("THE REGRESSION: kicked off, no score yet, next game hours away",
     [game(-0.1), game(3)], LIVE_INTERVAL),
    ("a game actually scoring", [game(-1, points=7), game(3)], LIVE_INTERVAL),
    ("kicked off with no score, and nothing else on the slate",
     [game(-0.1)], LIVE_INTERVAL),
    ("every game final — the week is done, stop",
     [game(-5, points=21, completed=True)], None),
    ("one final, one still playing", [game(-5, points=21, completed=True),
                                      game(-0.2)], LIVE_INTERVAL),
    ("kickoff within the hour — stay awake, slowly",
     [game(0.5)], IDLE_INTERVAL),
    ("nothing for hours — let the shift end", [game(4)], None),
    ("no games at all", [], None),
    # A game with no start time cannot be placed; it must not be treated as
    # started, or an unscheduled fixture would hold a shift open forever.
    ("a game with no kickoff time does not hold the shift open",
     [{"homePoints": None, "completed": False}, game(4)], None),
]

failures = 0
for label, games, expected in CASES:
    got = next_delay(games, NOW)
    ok = got == expected
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   (got {got}, wanted {expected})"))

print()
if failures:
    print(f"{failures} case(s) FAILED")
    sys.exit(1)
print(f"all {len(CASES)} cases OK — the shift stays awake while football is on")
