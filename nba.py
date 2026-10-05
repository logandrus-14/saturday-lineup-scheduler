#!/usr/bin/env python3
"""NBA games for Fumbling, the app's pretend sportsbook.

Logan, Oct 5 2026: "can we add the NBA? Preseason just started". Like the
NFL (nfl.py), NBA games exist in the app ONLY as pretend bets.

SOURCE: ESPN's public scoreboard, the same unofficial family as nfl.py —
free, no key, DraftKings' lines up to tip-off. Written to fail quietly (no
NBA board) rather than cost anything else.

BY DAY, NOT BY WEEK. The NBA plays every night, so its games are cached one
doc per US Eastern calendar day — cache/nba_<YYYYMMDD> — and a slip's leg
remembers that day in its `week` field (an NBA leg's "week" is 20261005),
the way an NFL leg remembers its encoded week. That is how a slip finds its
game again to settle, long after the board has moved on. cache/nba_current
names the season and the days the board should show.

Lines are carried forward from the last cached copy of the same game,
because ESPN drops them at tip-off — exactly as nfl.py does.
"""
import datetime as dt
import gzip
import json
import urllib.request
from zoneinfo import ZoneInfo

import nfl

ESPN = ("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
        "scoreboard")
ET = ZoneInfo("America/New_York")

# The board shows today and the next six days; yesterday is kept fresh too,
# because a 10:30pm ET tip-off finishes after midnight.
DAYS_AHEAD = 6


def fetch(day):
    """One ET day's games, [day] as 'YYYYMMDD'. ESPN sometimes answers
    gzipped whether asked or not, so both are handled."""
    req = urllib.request.Request(
        f"{ESPN}?dates={day}",
        headers={"User-Agent": "saturday-lineup", "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def day_key(when):
    """The ET calendar day of a UTC datetime, as 'YYYYMMDD'."""
    return when.astimezone(ET).strftime("%Y%m%d")


def window(now, ahead=DAYS_AHEAD):
    """Yesterday through [ahead] days from now, as day keys, in order."""
    return [day_key(now + dt.timedelta(days=d)) for d in range(-1, ahead + 1)]


def _clock(comp):
    """'5:32' while the game is on. In a period's last minute ESPN shows
    tenths ('45.2'), which reads here as '0:45' so the app has one shape."""
    c = (comp.get("status") or {}).get("displayClock")
    if not isinstance(c, str):
        return None
    if ":" in c:
        return nfl._clock(comp)
    try:
        secs = int(float(c))
    except ValueError:
        return None
    return f"0:{secs:02d}"


def season_of(data):
    return (data.get("season") or {}).get("year")


def parse(data, prior=None):
    """ESPN's scoreboard -> the app's game shape, the same as nfl.parse's
    (minus possession, which a basketball scoreboard doesn't carry)."""
    prior_by_id = {g["id"]: g for g in (prior or [])}
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue
        status = nfl._status(comp)
        odds = (comp.get("odds") or [{}])[0]
        ml = odds.get("moneyline") or {}
        game = {
            "id": f"nba{ev['id']}",
            "homeTeam": home["team"].get("displayName"),
            "awayTeam": away["team"].get("displayName"),
            "homeAbbr": home["team"].get("abbreviation"),
            "awayAbbr": away["team"].get("abbreviation"),
            "startDate": ev.get("date"),
            "status": status,
            "period": nfl._period(comp) if status == "live" else None,
            "clock": _clock(comp) if status == "live" else None,
            "possession": None,
            "situation": None,
            "homeScore": int(home["score"]) if status != "scheduled"
            and home.get("score") not in (None, "") else None,
            "awayScore": int(away["score"]) if status != "scheduled"
            and away.get("score") not in (None, "") else None,
            # The HOME team's line, −3.5 = home favoured, as for football.
            "spread": nfl._num(odds.get("spread")),
            "overUnder": nfl._num(odds.get("overUnder")),
            "openSpread": nfl.opening(odds)[0],
            "openOverUnder": nfl.opening(odds)[1],
            "homeMoneyline": nfl._price(ml.get("home", {}).get("close", {})
                                        .get("odds")),
            "awayMoneyline": nfl._price(ml.get("away", {}).get("close", {})
                                        .get("odds")),
        }
        before = prior_by_id.get(game["id"])
        if before:
            for k in ("spread", "overUnder", "homeMoneyline", "awayMoneyline",
                      "openSpread", "openOverUnder"):
                if game[k] is None:
                    game[k] = before.get(k)
        games.append(game)
    games.sort(key=lambda g: g["startDate"] or "")
    return games
