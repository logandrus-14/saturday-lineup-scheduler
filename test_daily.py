#!/usr/bin/env python3
"""ESPN's NHL, college basketball, soccer and MLB scoreboards -> the app's
game shape (daily.py).

    python3 test_daily.py
"""
import copy
import datetime as dt
import gzip
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "daily_fixture.json")))
checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


def one(key, fixture, slug="x", prior=None):
    return daily.parse(key, [(slug, fixture)], prior)[0]


# ── The NHL, a real night (Oct 19 2026), before puck drop ────────────────
nhl = one("nhl", FIX["nhl_20261019"])
check("id prefixed with its league", nhl["id"], "nhl401892535")
check("home team in full", nhl["homeTeam"], "New York Rangers")
check("puck line, home side", nhl["spread"], -1.5)
check("…with its real price, not -110",
      (nhl["homeSpreadPrice"], nhl["awaySpreadPrice"]), (180, -218))
check("total and both prices", (nhl["overUnder"], nhl["overPrice"],
                                nhl["underPrice"]), (6.5, 105, -125))
check("moneylines", (nhl["homeMoneyline"], nhl["awayMoneyline"]),
      (-135, 114))
check("opening total, for the line moves", nhl["openOverUnder"], 5.5)
check("no competition outside soccer", nhl["competition"], None)
check("season from the response",
      daily.season_of([("x", FIX["nhl_20261019"])], "20261019"), 2027)
check("…or the day's own year",
      daily.season_of([("x", {"events": []})], "20261019"), 2026)


def live(fixture, clock="12:34", period=2, completed=False, detail=None,
         outs=None, home="3", away="1"):
    d = copy.deepcopy(fixture)
    comp = d["events"][0]["competitions"][0]
    comp["status"] = {
        "displayClock": clock, "period": period,
        "type": {"state": "post" if completed else "in",
                 "completed": completed, "shortDetail": detail or ""}}
    if outs is not None:
        comp["situation"] = {"outs": outs}
    for c in comp["competitors"]:
        c["score"] = home if c["homeAway"] == "home" else away
    comp["odds"] = []  # dropped at the start
    return d


on = one("nhl", live(FIX["nhl_20261019"]), prior=[nhl])
check("live", (on["status"], on["period"], on["clock"]), ("live", 2, "12:34"))
check("scores", (on["homeScore"], on["awayScore"]), (3, 1))
check("lines AND prices carried forward",
      (on["spread"], on["homeSpreadPrice"], on["overPrice"]), (-1.5, 180, 105))

# ── MLB: no clock — the half and the outs ────────────────────────────────
mlb = one("mlb", FIX["mlb_20261019"])
check("run line priced", (mlb["spread"], mlb["homeSpreadPrice"]), (-1.5, 171))
m = FIX["mlb_20261019"]
check("top of the 5th, one out",
      one("mlb", live(m, clock="0:00", period=5, detail="Top 5th", outs=1))
      ["clock"], "top:1")
check("bottom, two out",
      one("mlb", live(m, period=7, detail="Bot 7th", outs=2))["clock"],
      "bot:2")
check("middle of an inning carries no outs",
      one("mlb", live(m, period=3, detail="Mid 3rd", outs=3))["clock"],
      "mid:0")
check("an unreadable half is no clock, not a guess",
      one("mlb", live(m, period=3, detail="Delayed"))["clock"], None)
check("the inning", one("mlb", live(m, period=9, detail="Top 9th"))["period"],
      9)

# ── Soccer: three-way odds become draw no bet ────────────────────────────
epl = one("soc", FIX["soc_eng.1_20261025"], "eng.1")
check("competition named", epl["competition"], "Premier League")
check("Bet365's decimal 3-way -> draw-no-bet prices",
      (epl["homeMoneyline"], epl["awayMoneyline"]), (105, -125))
check("no handicap offered", epl["spread"], None)
liga = one("soc", FIX["soc_esp.1_20261025"], "esp.1")
check("DraftKings' American 3-way -> draw no bet",
      (liga["homeMoneyline"], liga["awayMoneyline"]), (-395, 300))
check("soccer total with its prices",
      (liga["overUnder"], liga["overPrice"], liga["underPrice"]),
      (2.5, 125, -160))
check("draw no bet from even chances is -110 a side",
      daily.draw_no_bet(0.36, 0.36, 0.28), (-110, -110))
check("no prices, no draw no bet", daily.draw_no_bet(None, 0.4, 0.3),
      (None, None))
s = live(FIX["soc_esp.1_20261025"], clock="67'", period=2, home="1",
         away="1")
check("soccer's clock as ESPN shows it", one("soc", s, "esp.1")["clock"],
      "67'")
s = live(FIX["soc_esp.1_20261025"], clock="45'", period=1)
s["events"][0]["competitions"][0]["status"]["type"]["name"] = \
    "STATUS_HALFTIME"
check("half time reads HT", one("soc", s, "esp.1")["clock"], "HT")
s = live(FIX["soc_esp.1_20261025"], clock="Delayed", period=1)
check("anything else is no clock, not a guess",
      one("soc", s, "esp.1")["clock"], None)
nulled = copy.deepcopy(FIX["soc_esp.1_20261025"])
nulled["events"][0]["competitions"][0]["odds"] = [None]
check("odds: [null] (a finished soccer game) is no lines, not a crash",
      one("soc", nulled, "esp.1")["homeMoneyline"], None)
both = daily.parse("soc", [("eng.1", FIX["soc_eng.1_20261025"]),
                           ("esp.1", FIX["soc_esp.1_20261025"])])
check("a soccer day is every competition's games, by kickoff",
      [g["competition"] for g in both], ["La Liga", "Premier League"])

# ── College basketball: lines come the day before ────────────────────────
cbb = one("cbb", FIX["cbb_20261104"])
check("a game with no lines yet has none", (cbb["spread"],
                                            cbb["homeMoneyline"]),
      (None, None))
check("college hoops' clock reads like the NBA's",
      one("cbb", live(FIX["cbb_20261104"], clock="45.2", period=2))["clock"],
      "0:45")
check("every Division I game is asked for",
      daily.LEAGUES["cbb"]["query"].startswith("groups=50"), True)

# ── The shift fetches a day only when something is on ────────────────────
now = dt.datetime(2026, 10, 19, 22, 30, tzinfo=dt.timezone.utc)
check("a game within the hour wants a refresh",
      daily.needs_refresh([nhl], now), True)
check("…a game hours away does not",
      daily.needs_refresh([nhl], now - dt.timedelta(hours=3)), False)
check("a live game does", daily.needs_refresh([{"status": "live"}], now),
      True)
check("a finished day does not",
      daily.needs_refresh([dict(nhl, status="final")], now), False)

# ── Fetching: gzip, and a soccer competition that fails is left out ──────
body = gzip.compress(json.dumps(FIX["nhl_20261019"]).encode())


class _Resp:
    def read(self):
        return body


with mock.patch("urllib.request.urlopen", return_value=_Resp()):
    check("gzipped response read",
          len(daily.fetch("hockey/nhl", "20261019")["events"]), 2)

calls = []


def flaky(path, day, query=None):
    calls.append(path)
    if path.endswith("ita.1"):
        raise OSError("down")
    return {"events": []}


with mock.patch.object(daily, "fetch", flaky):
    got = daily.fetch_day("soc", "20261025")
check("one competition down, the rest still read",
      (len(got), "ita.1" in [s for s, _ in got]),
      (len(daily.LEAGUES["soc"]["paths"]) - 1, False))
with mock.patch.object(daily, "fetch",
                       mock.Mock(side_effect=OSError("all down"))):
    try:
        daily.fetch_day("nhl", "20261019")
        check("every scoreboard down raises", False, True)
    except OSError:
        check("every scoreboard down raises", True, True)

print(f"{checks - failures}/{checks} passed")
sys.exit(1 if failures else 0)
