#!/usr/bin/env python3
"""NFL games for Fumbling, the app's pretend sportsbook.

Logan, Oct 4 2026: "yes add NFL games to Fumbling". The pick'em is college
only and stays that way — NFL games exist in the app ONLY as pretend bets.

SOURCE: ESPN's public scoreboard — free, no key. It carries every game of
the week, live and final scores, and DraftKings' lines (spread, total,
moneyline) up to kickoff. It is UNOFFICIAL, so ESPN can change it without
warning; everything here is written to fail quietly (no NFL board) rather
than take the college cache down with it.

ESPN DROPS A GAME'S LINES AT KICKOFF. A bet keeps the line it was taken at,
so that alone would not break settling — but a board with no line for a
live game would look broken. So the lines are carried forward from the
last cached copy of the same game.

Written to cache/nfl_<season>_<type>_<week> (gamesJson, like the college
slates) and cache/nfl_current (which week that is). Week keys carry the
season TYPE because ESPN numbers the postseason from 1 again.
"""
import json
import urllib.request

ESPN = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
        "scoreboard")


def fetch(week=None, season_type=None):
    url = ESPN
    if week is not None:
        url += f"?week={week}&seasontype={season_type or 2}"
    req = urllib.request.Request(url, headers={"User-Agent": "saturday-lineup"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price(v):
    """'-535' / '+400' / 'EVEN' -> int, or None."""
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in ("EVEN", "EV"):
        return 100
    try:
        return int(float(s.replace("+", "")))
    except ValueError:
        return None


def _status(comp):
    state = comp.get("status", {}).get("type", {})
    if state.get("completed"):
        return "final"
    if state.get("state") == "in":
        return "live"
    return "scheduled"


def _period(comp):
    p = (comp.get("status") or {}).get("period")
    return int(p) if isinstance(p, (int, float)) and p > 0 else None


def _clock(comp):
    """'8:14' while the game is on. ESPN's displayClock, checked to be a
    clock, because a halftime or delay can carry other text."""
    c = (comp.get("status") or {}).get("displayClock")
    if not isinstance(c, str) or ":" not in c:
        return None
    m, _, sec = c.partition(":")
    return c if m.isdigit() and sec[:2].isdigit() else None


def _possession(comp, home, away):
    pid = (comp.get("situation") or {}).get("possession")
    if not pid:
        return None
    if str(pid) == str(home.get("team", {}).get("id") or home.get("id")):
        return "home"
    if str(pid) == str(away.get("team", {}).get("id") or away.get("id")):
        return "away"
    return None


def parse(data, prior=None):
    """ESPN's scoreboard -> the app's game shape. [prior] is the last cached
    list for the same week, whose lines fill in any game ESPN has stopped
    pricing (it stops at kickoff)."""
    prior_by_id = {g["id"]: g for g in (prior or [])}
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue
        status = _status(comp)
        odds = (comp.get("odds") or [{}])[0]
        ml = odds.get("moneyline") or {}
        game = {
            "id": f"nfl{ev['id']}",
            "homeTeam": home["team"].get("displayName"),
            "awayTeam": away["team"].get("displayName"),
            "homeAbbr": home["team"].get("abbreviation"),
            "awayAbbr": away["team"].get("abbreviation"),
            "startDate": ev.get("date"),
            "status": status,
            # Quarter and clock while the game is on, for Fumbling's win
            # chance (the score alone cannot say how much football is left).
            # Null otherwise, exactly as the college cache carries them.
            "period": _period(comp) if status == "live" else None,
            "clock": _clock(comp) if status == "live" else None,
            # Who has the ball, as 'home'/'away' — the college cache's own
            # convention. ESPN names the TEAM ID; null whenever it does not
            # say (between quarters, after a score), never a guess.
            "possession": _possession(comp, home, away)
            if status == "live" else None,
            "situation": ((comp.get("situation") or {})
                          .get("downDistanceText") or None)
            if status == "live" else None,
            "homeScore": int(home["score"]) if status != "scheduled"
            and home.get("score") not in (None, "") else None,
            "awayScore": int(away["score"]) if status != "scheduled"
            and away.get("score") not in (None, "") else None,
            # ESPN's `spread` is the HOME team's line: −9.5 = home favored,
            # the same convention as the college games.
            "spread": _num(odds.get("spread")),
            "overUnder": _num(odds.get("overUnder")),
            "homeMoneyline": _price(ml.get("home", {}).get("close", {})
                                    .get("odds")),
            "awayMoneyline": _price(ml.get("away", {}).get("close", {})
                                    .get("odds")),
        }
        before = prior_by_id.get(game["id"])
        if before:
            for k in ("spread", "overUnder", "homeMoneyline", "awayMoneyline"):
                if game[k] is None:
                    game[k] = before.get(k)
        games.append(game)
    games.sort(key=lambda g: g["startDate"] or "")
    return games


def week_of(data):
    """(season year, season type, week number) of a scoreboard response."""
    season = data.get("season", {})
    return (season.get("year"), season.get("type"),
            data.get("week", {}).get("number"))


def next_week(season_type, week):
    """The ESPN week after (type, week), or None at the end of a season.
    The regular season is 18 weeks; the postseason is numbered 1–5 again
    (wild card, divisional, conference, the bye, the Super Bowl)."""
    if season_type == 2:
        return (2, week + 1) if week < 18 else (3, 1)
    if season_type == 3 and week < 5:
        return (3, week + 1)
    if season_type == 1:  # preseason runs into week 1
        return (2, 1)
    return None

