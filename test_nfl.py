#!/usr/bin/env python3
"""ESPN's NFL scoreboard -> the app's game shape.

    python3 test_nfl.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nfl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "nfl_fixture.json")))
checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


games = nfl.parse(FIX["scheduled"])
g = games[0]
check("id is prefixed so it never collides with a college id",
      g["id"].startswith("nfl"), True)
check("home team named in full", g["homeTeam"], "Dallas Cowboys")
check("home spread, home-favored negative", g["spread"], -9.5)
check("total", g["overUnder"], 47.5)
check("moneylines as numbers", (g["homeMoneyline"], g["awayMoneyline"]),
      (-535, 400))
check("scheduled has no score", (g["homeScore"], g["awayScore"]),
      (None, None))
check("scheduled", g["status"], "scheduled")

started = nfl.parse(FIX["started"])
check("started games lose ESPN's lines", started[0]["spread"], None)
prior = [dict(started[0], spread=-3.0, overUnder=44.5,
              homeMoneyline=-150, awayMoneyline=130)]
carried = nfl.parse(FIX["started"], prior)
check("…and get them back from the last cached copy",
      (carried[0]["spread"], carried[0]["overUnder"]), (-3.0, 44.5))
check("a started game has a score", carried[0]["homeScore"] is not None, True)

check("a live game carries its quarter and clock",
      (carried[0]["period"], carried[0]["clock"]), (1, "10:16"))
check("a scheduled game carries neither", (g["period"], g["clock"]),
      (None, None))
check("a clock that is not a clock is dropped",
      nfl._clock({"status": {"displayClock": "Halftime"}}), None)

home = {"team": {"id": "16"}}
away = {"team": {"id": "15"}}
check("ball with the home team",
      nfl._possession({"situation": {"possession": "16"}}, home, away), "home")
check("ball with the away team",
      nfl._possession({"situation": {"possession": "15"}}, home, away), "away")
check("no possession is None, never a side",
      nfl._possession({"situation": {}}, home, away), None)

check("week_of", nfl.week_of(FIX["scheduled"])[1:], (2, 5))
check("EVEN is +100", nfl._price("EVEN"), 100)
check("junk price is None", nfl._price("n/a"), None)

print(f"{checks - failures}/{checks} NFL checks pass")
sys.exit(1 if failures else 0)
