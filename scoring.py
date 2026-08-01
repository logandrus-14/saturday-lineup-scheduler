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
