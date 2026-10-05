#!/usr/bin/env python3
"""ESPN's NBA scoreboard -> the app's game shape, and the days it covers.

    python3 test_nba.py
"""
import copy
import datetime as dt
import gzip
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nba  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "nba_fixture.json")))
checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


# ── A real preseason night (Oct 5 2026), before tip-off ──────────────────
games = nba.parse(FIX)
g = games[0]
check("id is prefixed so it never collides with any other",
      g["id"].startswith("nba"), True)
check("home team named in full", g["homeTeam"], "Atlanta Hawks")
check("home spread, home-favoured negative", g["spread"], -3.5)
check("total", g["overUnder"], 232.5)
check("moneylines as numbers", isinstance(g["homeMoneyline"], int), True)
check("scheduled", g["status"], "scheduled")
check("no score before tip-off", (g["homeScore"], g["awayScore"]),
      (None, None))
check("no possession — a basketball scoreboard doesn't say",
      g["possession"], None)
check("season read from the response", nba.season_of(FIX), 2027)
check("opening spread, for the board's line moves", g["openSpread"], -2.5)
check("opening total", g["openOverUnder"], 232.5)


def live(clock, period=3, completed=False):
    d = copy.deepcopy(FIX)
    comp = d["events"][0]["competitions"][0]
    comp["status"] = {
        "displayClock": clock, "period": period,
        "type": {"state": "post" if completed else "in",
                 "completed": completed}}
    for c in comp["competitors"]:
        c["score"] = "88" if c["homeAway"] == "home" else "80"
    comp["odds"] = []  # ESPN drops the lines at tip-off
    return d


on = nba.parse(live("5:32"), prior=games)[0]
check("live", on["status"], "live")
check("quarter", on["period"], 3)
check("clock", on["clock"], "5:32")
check("scores", (on["homeScore"], on["awayScore"]), (88, 80))
check("lines carried forward from before tip-off",
      (on["spread"], on["overUnder"]), (-3.5, 232.5))
check("opening lines carried forward too", on["openSpread"], -2.5)
check("a period's last minute shows tenths; read as 0:45",
      nba.parse(live("45.2"))[0]["clock"], "0:45")
check("overtime is period 5", nba.parse(live("2:10", period=5))[0]["period"],
      5)
done = nba.parse(live("0.0", period=4, completed=True))[0]
check("final", done["status"], "final")
check("no clock once final", done["clock"], None)
check("no lines and no prior: none, not a guess",
      nba.parse(live("5:32"))[0]["spread"], None)

# ── Days, on the US Eastern calendar ─────────────────────────────────────
late = dt.datetime(2026, 10, 6, 2, 30, tzinfo=dt.timezone.utc)  # 10:30pm ET
check("a 10:30pm ET game belongs to that ET day", nba.day_key(late),
      "20261005")
w = nba.window(late)
check("window: yesterday through six days on", (w[0], w[1], w[-1], len(w)),
      ("20261004", "20261005", "20261011", 8))

# ── fetch copes with a gzipped answer ────────────────────────────────────
body = gzip.compress(json.dumps(FIX).encode())


class _Resp:
    def read(self):
        return body


with mock.patch("urllib.request.urlopen", return_value=_Resp()):
    check("gzipped response read", len(nba.fetch("20261005")["events"]), 2)

print(f"{checks - failures}/{checks} passed")
sys.exit(1 if failures else 0)
