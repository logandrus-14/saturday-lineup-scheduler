#!/usr/bin/env python3
"""Live scores come from /scoreboard, not /games.

WHY THIS EXISTS. This project assumed the regular games endpoint carried
live scores — "no special live endpoint needed" — and nothing tested that
until the first kickoff of the season. Eighteen minutes into North
Carolina at TCU on Aug 29 2026, /games still returned homePoints: null,
period: null, clock: null, while /scoreboard had the game in_progress,
1st quarter, 9:22 left, UNC 3 TCU 0.

Without the overlay there are no live scores anywhere in the app, and
nothing goes final until CFBD backfills /games some time after the
whistle.

    /usr/bin/python3 test_scoreboard.py
"""

import sys

from scoring import apply_scoreboard

SCHEDULE = [
    {"id": 1, "homeTeam": "TCU", "awayTeam": "North Carolina",
     "completed": False, "homePoints": None, "awayPoints": None},
    {"id": 2, "homeTeam": "USC", "awayTeam": "San José State",
     "completed": False, "homePoints": None, "awayPoints": None},
]


def board(**kw):
    e = {"id": 1, "status": "in_progress", "period": 1, "clock": "09:22",
         "homeTeam": {"points": 0}, "awayTeam": {"points": 3}}
    e.update(kw)
    return [e]


failures = 0


def check(label, ok):
    global failures
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


got = apply_scoreboard(SCHEDULE, board())
g1 = next(g for g in got if g["id"] == 1)
check("THE REGRESSION: a live score reaches the slate",
      (g1["awayPoints"], g1["homePoints"]) == (3, 0))
check("period and clock come across", (g1["period"], g1["clock"]) == (1, "09:22"))
check("an in-progress game is not marked final", g1["completed"] is False)

g2 = next(g for g in got if g["id"] == 2)
check("a game the scoreboard does not mention is untouched",
      g2["homePoints"] is None and g2["completed"] is False)

done = apply_scoreboard(SCHEDULE, board(status="completed",
                                        homeTeam={"points": 17},
                                        awayTeam={"points": 24}))
d1 = next(g for g in done if g["id"] == 1)
check("a finished game goes final with its score",
      d1["completed"] is True and (d1["awayPoints"], d1["homePoints"]) == (24, 17))

# THE ONE-WAY RULE. The scoreboard drops finished games from its window,
# and /games backfills `completed` late — so neither source may un-finish
# a game the other has already called.
was_final = [dict(SCHEDULE[0], completed=True, homePoints=17, awayPoints=24)]
back = apply_scoreboard(was_final, board(status="scheduled"))
check("a final game is never un-finished by a later scoreboard",
      back[0]["completed"] is True)

check("no scoreboard at all is survivable",
      apply_scoreboard(SCHEDULE, None)[0]["homePoints"] is None)
check("an empty schedule does not crash", apply_scoreboard([], board()) == [])
check("a scoreboard entry with no points does not blank a known score",
      apply_scoreboard(was_final, [{"id": 1, "status": "in_progress",
                                    "homeTeam": {}, "awayTeam": {}}]
                       )[0]["homePoints"] == 17)

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 9 checks OK — live scores reach the slate")
