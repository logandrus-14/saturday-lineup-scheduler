#!/usr/bin/env python3
"""A run that learns nothing new must publish what it already had.

WHY THIS EXISTS — Sep 5 2026, mid-afternoon, seventeen games in progress.

`update_cache` wraps its `/scoreboard` call in a try/except that prints
"scoreboard skipped" and carries on. Carrying on means writing the raw
`/games` rows, and `/games` reports null points, null period and null
clock for anything still being played. So one flaky scoreboard call did
not merely fail to ADD live scores — it erased the ones already
published. Every live game on every phone went blank at once while the
finished games kept their scores, which read as the app breaking rather
than the feed hiccupping.

    /usr/bin/python3 test_carry_forward.py
"""

import sys

from scoring import carry_live_forward

failures = 0


def check(label, ok):
    global failures
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures += 1


# What /games hands back for a game being played right now: it knows the
# fixture and nothing about the play.
BLANK = {"id": 1, "homeTeam": "Indiana", "awayTeam": "North Texas",
         "homePoints": None, "awayPoints": None, "period": None,
         "clock": None, "completed": False}

# What was already published for it a minute earlier.
CACHED = dict(BLANK, homePoints=24, awayPoints=13, period=3, clock="10:19")

print(__doc__.strip().splitlines()[0])
print()

out = carry_live_forward([BLANK], [CACHED])[0]
check("a blank live row keeps the score already published",
      (out["homePoints"], out["awayPoints"]) == (24, 13))
check("and keeps the period and clock with it",
      (out["period"], out["clock"]) == (3, "10:19"))

# The whole point is that this must not freeze a game in place.
fresher = dict(BLANK, homePoints=31, awayPoints=13, period=4, clock="02:00")
out = carry_live_forward([fresher], [CACHED])[0]
check("a real update always wins over the cached value",
      (out["homePoints"], out["period"], out["clock"]) == (31, 4, "02:00"))

# 0 is a score. `if not merged.get(key)` would have thrown it away and
# reinstated a stale number over a genuine shutout.
zero = dict(BLANK, homePoints=0, awayPoints=0)
out = carry_live_forward([zero], [CACHED])[0]
check("nil-nil is a score, not a missing one",
      (out["homePoints"], out["awayPoints"]) == (0, 0))

out = carry_live_forward([BLANK], [dict(CACHED, completed=True)])[0]
check("a finished game is never un-finished by a forgetful feed",
      out["completed"] is True)

out = carry_live_forward([dict(BLANK, completed=True)], [CACHED])[0]
check("and a game that has just finished stays finished",
      out["completed"] is True)

check("a game with no cached history is passed through untouched",
      carry_live_forward([BLANK], [])[0] == BLANK)
check("no prior cache at all is survivable",
      carry_live_forward([BLANK], None)[0] == BLANK)
check("an empty slate does not crash", carry_live_forward([], [CACHED]) == [])
check("a cached row for a game no longer on the slate is ignored",
      carry_live_forward([], [CACHED]) == [])

# The Sep 5 shape, end to end: the finished games kept their scores and
# only the live ones went blank, which is what made it look like the app.
final = {"id": 2, "homePoints": 13, "awayPoints": 14, "completed": True}
out = carry_live_forward([BLANK, final], [CACHED, final])
check("the whole board survives a scoreboard outage",
      out[0]["homePoints"] == 24 and out[1]["homePoints"] == 13)

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 11 checks OK — a scoreboard outage cannot blank the board")
