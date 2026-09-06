#!/usr/bin/env python3
"""A season score must charge only what has actually been decided.

WHY THIS EXISTS — Sep 5 2026. Game Day showed Oakley +21 and the global
leaderboard showed him +16, and Game Day was right. He had 22 points
banked, one point missed, and five points still being played. The season
board computed `2 x won - 28 x weeks`, which assumes every week it is
handed is FINISHED — so it charged those five live points against him as
losses. Logan spotted it from the two screens disagreeing.

`won - lost` is the fix and it is not a special case: once a week is over
`won + lost == 28`, so it equals `2 x won - 28` exactly.

    /usr/bin/python3 test_season_lost.py
"""

import sys

from scoring import weekly_lost, weekly_points, week_is_complete

failures = 0


def check(label, ok):
    global failures
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures += 1


def game(gid, home, away, spread, hs=None, as_=None, status="final"):
    return {"id": gid, "homeTeam": home, "awayTeam": away, "spread": spread,
            "homeScore": hs, "awayScore": as_, "status": status}


print(__doc__.strip().splitlines()[0])
print()

# Oakley's actual week: 22 banked, the kicker missed, flex and def live.
GAMES = [
    game("1", "Syracuse", "New Hampshire", -30, 66, 3),
    game("2", "Stanford", "Miami", 20, 6, 45),
    game("3", "Duke", "Tulane", -3, 17, 3),
    game("4", "Pittsburgh", "Miami (OH)", -10, 59, 14),
    game("5", "California", "UCLA", -3, 7, 7, status="in_progress"),
    game("6", "LSU", "Clemson", -6, 30, 3, status="in_progress"),
    game("7", "Cincinnati", "Boston College", -6, 34, 15),
]
PICKS = {
    "qb": {"gameId": "1", "team": "Syracuse"},
    "rb": {"gameId": "2", "team": "Miami"},
    "wr": {"gameId": "3", "team": "Duke"},
    "te": {"gameId": "4", "team": "Pittsburgh"},
    "flex": {"gameId": "5", "team": "California"},
    "def": {"gameId": "6", "team": "LSU"},
    "kicker": {"gameId": "7", "team": "Boston College"},
}

won = weekly_points(PICKS, GAMES)
lost = weekly_lost(PICKS, GAMES)
check("the banked points are what Game Day banked", won == 22)
check("only the settled miss is charged", lost == 1)
check("Oakley reads +21, which is what Game Day showed",
      won - lost == 21)
check("the old formula got +16 by charging live picks as losses",
      2 * won - 28 == 16)

# The identity that makes this safe to use everywhere.
FINAL = [
    game("1", "A", "B", -3, 30, 0),   # home covers
    game("2", "C", "D", -3, 0, 30),   # home does not
]
ALL_SETTLED = {
    "qb": {"gameId": "1", "team": "A"},
    "rb": {"gameId": "2", "team": "C"},
    "wr": {"gameId": "1", "team": "A"},
    "te": {"gameId": "2", "team": "C"},
    "flex": {"gameId": "1", "team": "A"},
    "def": {"gameId": "2", "team": "C"},
    "kicker": {"gameId": "1", "team": "A"},
}
w = weekly_points(ALL_SETTLED, FINAL)
l = weekly_lost(ALL_SETTLED, FINAL)
check("a finished week accounts for all 28 points", w + l == 28)
check("and the two formulas agree exactly once it is over",
      w - l == 2 * w - 28)

check("a pick nobody made costs nothing WHILE THE WEEK IS STILL ON",
      weekly_lost({}, GAMES) == 0)

# THE INVARIANT THAT NEARLY GOT BROKEN. The first version of weekly_lost
# counted only settled misses, full stop — so somebody who never picked had
# no misses, was charged nothing, and finished ABOVE everybody who played
# and lost. Logan settled this rule the same morning: a no-show is charged
# the full 28, and a trier can do no worse than -26.
ALL_DONE = [game(str(i), "H", "A", -3, 30, 0) for i in range(1, 8)]
check("a week that is over is recognised as over", week_is_complete(ALL_DONE))
check("a no-show is charged the whole week",
      weekly_lost({}, ALL_DONE) == 28)
check("and somebody who tried and went 0-for is charged the same 28, "
      "never more", weekly_lost(
          {"qb": {"gameId": "1", "team": "A"}}, ALL_DONE) == 28)
check("while a live week charges a no-show nothing yet",
      weekly_lost({}, GAMES) == 0)
check("a game missing from the slate is not a loss",
      weekly_lost({"qb": {"gameId": "nope", "team": "X"}}, GAMES) == 0)
check("a live game is neither won nor lost",
      weekly_lost({"qb": {"gameId": "5", "team": "UCLA"}}, GAMES) == 0)

# A PUSH IS CHARGED AS A MISS, and that is deliberate rather than an
# oversight found here. `did_cover` returns False on a push — landing
# exactly on the number is not covering, matching Game.didCover — and the
# formula this replaces charged it too, because it charged a flat 28
# whatever happened. Exempting it here would be a real scoring change: the
# week would stop adding to 28 and `won - lost` would stop agreeing with
# `2 x won - 28`, which is the identity that makes this safe to use on
# finished weeks at all. If pushes should ever score differently, that is
# a decision about the game and it belongs in did_cover, once, not in a
# second opinion bolted on here.
# A second game still being played, so this is a LIVE week and the settled
# -miss path is what gets exercised. On a completed week the whole 28 is
# charged anyway and a push would tell us nothing.
PUSH = [game("1", "A", "B", -3, 23, 20),
        game("9", "Y", "Z", -3, 0, 0, status="in_progress")]
pick = {"qb": {"gameId": "1", "team": "A"}}
check("a push wins nothing", weekly_points(pick, PUSH) == 0)
check("and is charged as a miss, exactly as the old formula charged it",
      weekly_lost(pick, PUSH) == 7)

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 16 checks OK — a half-played week is charged honestly")
