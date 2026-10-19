#!/usr/bin/env python3
"""More sports for Fumbling: the NHL, college basketball, soccer and MLB.

Logan, Oct 18 2026: "I also think we should as many more sports as we can
for free right now to always have something to bet on" — and, shown the
short list, "Yes, build all four". Like the NBA (nba.py) they exist in the
app ONLY as pretend bets.

SOURCE: ESPN's public scoreboard, the same unofficial family as nfl.py and
nba.py — free, no key, DraftKings' lines (Bet365's for some soccer) up to
the start. Written to fail quietly: a league that breaks only loses its own
board.

BY DAY, LIKE THE NBA. Each league is cached one doc per US Eastern day,
cache/<key>_<YYYYMMDD> (nhl_20261019), and cache/<key>_current names the
season and the days on the board. A leg's `week` is its day.

PRICES TRAVEL WITH THE LINES. Football and basketball spreads and totals
are -110 a side, which the app assumes. A hockey puck line or a baseball
run line is ±1.5 at real odds (+180 / -218), and soccer totals are rarely
even, so these games carry each side's price — homeSpreadPrice,
awaySpreadPrice, overPrice, underPrice — and the app bets them at it.

SOCCER'S MONEYLINE IS "DRAW NO BET". A real book sells three outcomes
(home, draw, away). Fumbling's moneyline already gives the stake back on
a tie, so the home and away prices here are the draw-no-bet ones, worked
out from the three-way odds: the chance of each side, with the draw taken
out, plus the usual -110 cut.

BASEBALL HAS NO CLOCK. Its `clock` is the half and outs — "top:1" (top of
the inning, one out), "mid:0", "bot:2", "end:0" — and `period` the inning.
"""
import datetime as dt
import gzip
import json
import urllib.request

import nba
import nfl

BASE = "https://site.api.espn.com/apis/site/v2/sports/"

# key -> what the app calls it and where ESPN keeps it. A soccer day is
# several competitions' scoreboards put together.
LEAGUES = {
    "nhl": {"paths": ["hockey/nhl"]},
    # groups=50 is all of Division I; without it ESPN sends a handful.
    "cbb": {"paths": ["basketball/mens-college-basketball"],
            "query": "groups=50&limit=400"},
    "soc": {"paths": ["soccer/eng.1", "soccer/esp.1", "soccer/ita.1",
                      "soccer/ger.1", "soccer/fra.1", "soccer/uefa.champions",
                      "soccer/uefa.europa", "soccer/usa.1"]},
    "mlb": {"paths": ["baseball/mlb"]},
}

# The soccer competitions, as the board labels them.
COMPETITIONS = {
    "eng.1": "Premier League", "esp.1": "La Liga", "ita.1": "Serie A",
    "ger.1": "Bundesliga", "fra.1": "Ligue 1",
    "uefa.champions": "Champions League", "uefa.europa": "Europa League",
    "usa.1": "MLS",
}

# The cut on a price this file works out itself (soccer's draw-no-bet):
# 50% costs 52.4%, which is -110 — the same as the app's live prices.
HOLD = 1.048


def fetch(path, day, query=None):
    """One scoreboard for one ET day ('YYYYMMDD'), gzipped or not."""
    url = f"{BASE}{path}/scoreboard?dates={day}"
    if query:
        url += f"&{query}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "saturday-lineup",
                      "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def fetch_day(key, day):
    """[(competition slug, scoreboard)] for one league's day. A soccer
    competition that fails is left out; the others still count."""
    cfg = LEAGUES[key]
    out, errors = [], []
    for path in cfg["paths"]:
        try:
            out.append((path.split("/")[-1], fetch(path, day, cfg.get("query"))))
        except Exception as e:
            errors.append(e)
    if not out and errors:
        raise errors[0]
    return out


def american(p):
    """A chance (0..1) as American odds, rounded to 5, never inside ±100."""
    p = min(max(p, 0.01), 0.99)
    raw = -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p
    r = int(round(raw / 5) * 5)
    if abs(r) < 100:
        return -100 if p >= 0.5 else 100
    return r


def _implied(price):
    """American odds -> the chance they imply, cut included."""
    if price is None or price == 0:
        return None
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def draw_no_bet(home, away, draw):
    """Three-way chances (any scale) -> draw-no-bet (home, away) prices."""
    if not home or not away:
        return (None, None)
    fair = home / (home + away)
    return (american(fair * HOLD), american((1 - fair) * HOLD))


def _decimal_chance(side):
    """Bet365's {"value": 2.7} -> 1/2.7."""
    try:
        v = float((side or {}).get("value"))
    except (TypeError, ValueError):
        return None
    return 1 / v if v > 1 else None


def _close(side, field="odds"):
    return ((side or {}).get("close") or {}).get(field)


def _soccer_clock(comp):
    """"67'" / "45'+2'" while on, as ESPN shows it, "HT" at half time;
    None otherwise."""
    status = comp.get("status") or {}
    if (status.get("type") or {}).get("name") == "STATUS_HALFTIME":
        return "HT"
    c = status.get("displayClock")
    if not isinstance(c, str) or not c[:1].isdigit():
        return None
    return c


def _baseball_clock(comp):
    """"top:1" — the half and the outs. ESPN's shortDetail says "Top 5th",
    "Bot 5th", "Mid 5th", "End 5th"; the outs are in the situation."""
    detail = ((comp.get("status") or {}).get("type") or {}).get(
        "shortDetail") or ""
    word = detail.split(" ")[0].lower()
    half = {"top": "top", "bot": "bot", "bottom": "bot", "mid": "mid",
            "middle": "mid", "end": "end"}.get(word)
    if half is None:
        return None
    outs = (comp.get("situation") or {}).get("outs")
    outs = int(outs) if isinstance(outs, (int, float)) and 0 <= outs <= 3 \
        else 0
    if half in ("mid", "end"):
        outs = 0
    return f"{half}:{outs}"


def _clock(key, comp):
    if key == "soc":
        return _soccer_clock(comp)
    if key == "mlb":
        return _baseball_clock(comp)
    return nba._clock(comp)  # mm:ss, with a last-minute "45.2" read as 0:45


CARRIED = ("spread", "overUnder", "homeMoneyline", "awayMoneyline",
           "openSpread", "openOverUnder", "homeSpreadPrice", "awaySpreadPrice",
           "overPrice", "underPrice")


def parse(key, boards, prior=None):
    """[(competition, scoreboard)] -> the app's game shape (nba.parse's, plus
    each side's price and, for soccer, the competition)."""
    prior_by_id = {g["id"]: g for g in (prior or [])}
    games = []
    for comp_slug, data in boards:
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            home, away = teams.get("home"), teams.get("away")
            if not home or not away:
                continue
            status = nfl._status(comp)
            # A finished soccer game can carry "odds": [null].
            odds = (comp.get("odds") or [{}])[0] or {}
            ml = odds.get("moneyline") or {}
            ps = odds.get("pointSpread") or {}
            tot = odds.get("total") or {}
            hml = nfl._price(_close(ml.get("home")))
            aml = nfl._price(_close(ml.get("away")))
            spread = nfl._num(odds.get("spread"))
            if key == "soc":
                # Three-way -> draw no bet. DraftKings gives American odds,
                # Bet365 decimal ones; either will do.
                dml = nfl._price(_close(ml.get("draw"))) or nfl._price(
                    (odds.get("drawOdds") or {}).get("moneyLine"))
                if hml and aml and dml:
                    hml, aml = draw_no_bet(_implied(hml), _implied(aml),
                                           _implied(dml))
                else:
                    hml, aml = draw_no_bet(
                        _decimal_chance(odds.get("homeTeamOdds")),
                        _decimal_chance(odds.get("awayTeamOdds")),
                        _decimal_chance(odds.get("drawOdds")))
                spread = None  # soccer's handicaps are not offered
            over_under = nfl._num(odds.get("overUnder"))
            game = {
                "id": f"{key}{ev['id']}",
                "homeTeam": home["team"].get("displayName"),
                "awayTeam": away["team"].get("displayName"),
                "homeAbbr": home["team"].get("abbreviation"),
                "awayAbbr": away["team"].get("abbreviation"),
                "startDate": ev.get("date"),
                "status": status,
                "period": nfl._period(comp) if status == "live" else None,
                "clock": _clock(key, comp) if status == "live" else None,
                "possession": None,
                "situation": None,
                "homeScore": int(home["score"]) if status != "scheduled"
                and home.get("score") not in (None, "") else None,
                "awayScore": int(away["score"]) if status != "scheduled"
                and away.get("score") not in (None, "") else None,
                "spread": spread,
                "overUnder": over_under,
                "openSpread": nfl.opening(odds)[0] if spread is not None
                else None,
                "openOverUnder": nfl.opening(odds)[1],
                "homeMoneyline": hml,
                "awayMoneyline": aml,
                "homeSpreadPrice": nfl._price(_close(ps.get("home")))
                if spread is not None else None,
                "awaySpreadPrice": nfl._price(_close(ps.get("away")))
                if spread is not None else None,
                "overPrice": nfl._price(_close(tot.get("over")))
                if over_under is not None else None,
                "underPrice": nfl._price(_close(tot.get("under")))
                if over_under is not None else None,
                "competition": COMPETITIONS.get(comp_slug)
                if key == "soc" else None,
            }
            before = prior_by_id.get(game["id"])
            if before:
                for k in CARRIED:
                    if game[k] is None:
                        game[k] = before.get(k)
            games.append(game)
    games.sort(key=lambda g: (g["startDate"] or "", g["id"]))
    return games


def season_of(boards, day):
    """ESPN's season year for the day, or the day's own year."""
    for _, data in boards:
        found = nba.season_of(data)
        if found:
            return found
    return int(day[:4])


def needs_refresh(games, now, ahead=dt.timedelta(hours=1)):
    """For the every-minute shift: is anything in [games] on, or about to
    be? A day of finished (or far-off) games is left alone between the
    hourly full refreshes — soccer alone is eight requests a day."""
    for g in games:
        if g.get("status") == "live":
            return True
        if g.get("status") == "final":
            continue
        try:
            start = dt.datetime.fromisoformat(
                (g.get("startDate") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if start <= now + ahead:
            return True
    return False
