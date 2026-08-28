#!/usr/bin/env python3
"""Scoring, mirrored from the Dart app so the scheduler can total a season.

THIS IS A SECOND COPY OF THE RULES THAT DECIDE WHO WINS. Treat it that way.

The app scores in Dart (`buildSlate`, `Game.didCover`, `computeWeeklyPoints`,
`LineupSlot.points`). Aggregating season standings server-side means the
same arithmetic has to exist here, in Python, and two copies of a rule drift
— usually silently, and usually in the direction nobody notices until
someone is told they lost.

The guard against that is `test/scoring_parity_test.dart` plus
`test/fixtures/scoring_cases.json`: one file of cases that BOTH languages
run and must agree on. **If you change scoring anywhere, add a case there
first and make both sides pass it.**

Mirrors, function for function:
  pick_spread   <- parseGame's consensus-or-first-provider choice
  build_slate   <- buildSlate (spread required, sort by kickoff, dedupe)
  did_cover     <- Game.didCover
  weekly_points <- computeWeeklyPoints
"""

import datetime as dt

SLOT_POINTS = {
    "qb": 7,
    "rb": 6,
    "wr": 5,
    "te": 4,
    "def": 3,
    "flex": 2,
    "kicker": 1,
}


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_spread(line_data):
    """The consensus provider's spread, else the first provider listed.

    Mirrors parseGame. The order matters: CFBD returns several books and
    picking a different one changes who covered.
    """
    if not line_data:
        return None
    lines = line_data.get("lines") or []
    best = None
    for line in lines:
        if str(line.get("provider", "")).lower() == "consensus":
            best = line
            break
        if best is None:
            best = line
    return _as_float(best.get("spread")) if best else None


def parse_status(game):
    """Mirrors _parseStatus: completed wins, then any score means live."""
    if game.get("completed") is True:
        return "final"
    if game.get("homePoints") is not None:
        return "live"
    return "scheduled"


# ── Week 0 ───────────────────────────────────────────────────────────────
#
# College football opens with a handful of games the weekend BEFORE the
# real first Saturday, and everyone calls that Week 0. CFBD does not: its
# 2026 week 1 runs Aug 29 -> Sep 8 and holds 389 games across BOTH
# weekends. Left alone that makes the app's first week ten days long, and
# — worse — build_slate de-dupes by team, so every team playing on both
# weekends would have its second game silently dropped.
#
# So the app numbers its own weeks. Only CFBD's week 1 is ever split:
#
#   app week 0  -> CFBD week 1, kickoff before the split
#   app week 1  -> CFBD week 1, kickoff on or after the split
#   app week N  -> CFBD week N, all of it
#
# Every app week from 1 up keeps CFBD's number, which is what stops the
# app drifting a week behind every scoreboard in the country.
#
# MIRRORS lib/core/utils/season_weeks.dart. Change these together.


def week_zero_ends_at(season):
    """Midnight Mountain on the Tuesday after the season's first Saturday.

    Tuesday because that is when this app already rolls a week over, and
    because football does the same — nothing is scheduled between a Monday
    night game and the following Wednesday. For 2026 that is Sep 1, a day
    with no games on either side of it.

    UTC-6, not -7: Mountain is on daylight time through September.
    """
    d = dt.datetime(season, 8, 25, tzinfo=dt.timezone.utc)
    while d.weekday() != 5:          # 5 == Saturday
        d += dt.timedelta(days=1)
    tuesday = d + dt.timedelta(days=3)
    return tuesday.replace(hour=6, minute=0, second=0, microsecond=0)


def cfbd_week_for(app_week):
    """Which CFBD week to ASK for, given an app week."""
    return 1 if app_week == 0 else app_week


def _kickoff(raw):
    value = raw.get("startDate") or raw.get("start_date")
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def in_app_week(season, app_week, raw):
    """Whether one raw CFBD game belongs to [app_week].

    A game with no kickoff time is KEPT, never dropped: being unable to
    place a game is our problem, and hiding it would take it off somebody's
    slate for a reason they could never see.
    """
    if app_week > 1:
        return True
    kickoff = _kickoff(raw)
    if kickoff is None:
        return True
    split = week_zero_ends_at(season)
    return kickoff < split if app_week == 0 else kickoff >= split


def fbs_only(games_raw):
    """Games with at least one FBS team, judged from the DATA not the query.

    CFBD renamed the games filter from `division` to `classification`, and
    an unrecognised query parameter is ignored rather than rejected — so
    `division=fbs` silently started returning every division. On Aug 28
    2026 that turned the eight-game preseason slate into fifty-two, with
    D2 games that had already kicked off showing as live and final, which
    in turn woke the Live Activity and the widgets a day early.

    Both parameter names are still sent, but a filter the SERVER applies
    can be renamed out from under us again. This one reads the
    classification already present on every game, so a third rename costs
    nothing. A game with no classification at all is kept — dropping a
    real game is worse than carrying a stray one.
    """
    out = []
    for g in games_raw or []:
        home = g.get("homeClassification")
        away = g.get("awayClassification")
        if home is None and away is None:
            out.append(g)
        elif "fbs" in (home, away):
            out.append(g)
    return out


def games_in_app_week(season, app_week, games_raw):
    """The half of a CFBD week that belongs to one app week."""
    return [g for g in (games_raw or []) if in_app_week(season, app_week, g)]


def build_slate(games_raw, lines_raw):
    """Mirrors buildSlate: spread required, earliest first, teams de-duped.

    The de-dupe matters for scoring, not just display: preseason data can
    list a team in two games, and keeping both would let one pick be scored
    against the wrong game.
    """
    lines_by_id = {}
    for line in lines_raw or []:
        if line.get("id") is not None:
            lines_by_id[line["id"]] = line

    games = []
    for raw in games_raw or []:
        spread = pick_spread(lines_by_id.get(raw.get("id")))
        if spread is None:
            continue  # only slate games with a spread, same as the app
        games.append({
            "id": str(raw.get("id")),
            "homeTeam": raw.get("homeTeam"),
            "awayTeam": raw.get("awayTeam"),
            "startDate": raw.get("startDate"),
            "spread": spread,
            "homeScore": raw.get("homePoints"),
            "awayScore": raw.get("awayPoints"),
            "status": parse_status(raw),
        })

    games.sort(key=lambda g: g["startDate"] or "")

    seen, deduped = set(), []
    for game in games:
        if game["homeTeam"] in seen or game["awayTeam"] in seen:
            continue
        seen.add(game["homeTeam"])
        seen.add(game["awayTeam"])
        deduped.append(game)
    return deduped


def did_cover(game, picked_team):
    """Mirrors Game.didCover. None until the game is final.

    Home covers when homeMargin > -spread; away when -homeMargin > spread.
    A push (exactly on the number) is NOT a cover, matching the app.
    """
    if game.get("status") != "final":
        return None
    home, away, spread = (
        game.get("homeScore"), game.get("awayScore"), game.get("spread"))
    if home is None or away is None or spread is None:
        return None

    home_margin = home - away
    if picked_team == game.get("homeTeam"):
        return home_margin > -spread
    return -home_margin > spread


def weekly_points(picks, games):
    """Mirrors computeWeeklyPoints.

    [picks] is {slot_name: {"gameId": str, "team": str}}. A pick whose game
    isn't on the slate scores nothing rather than raising — the app does the
    same, and a missing game is a data problem, not a reason to lose a
    whole season total.
    """
    games_by_id = {g["id"]: g for g in games}
    total = 0
    for slot, pick in (picks or {}).items():
        points = SLOT_POINTS.get(slot)
        if points is None:
            continue
        game = games_by_id.get(str(pick.get("gameId")))
        if game is None:
            continue
        if did_cover(game, pick.get("team")) is True:
            total += points
    return total
